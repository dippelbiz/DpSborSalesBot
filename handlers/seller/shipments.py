#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для раздела "Отгруженные поставки" (продавец)
Просмотр заявок в пути, подтверждение получения, добавление товаров на склад,
создание новой заявки при неполном получении.
Учитывает центральный склад Р: списание происходит только при подтверждении получения,
причём со склада Р списывается фактически полученное количество.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_seller_menu, get_back_keyboard
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

SELECTING_SHIPMENT, ENTERING_QUANTITY, CONFIRMING_RECEIPT = range(3)

async def shipments_start(update: Update, context):
    user_id = update.effective_user.id
    logger.info("shipments_start called by user %s", user_id)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, seller_code FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
        if not seller:
            await update.message.reply_text(
                "❌ Вы не активированы как продавец. Нажмите /start для активации.",
                reply_markup=get_seller_menu('')
            )
            return ConversationHandler.END
        seller_id = seller['id']
        seller_code = seller['seller_code']
        context.user_data['seller_id'] = seller_id
        context.user_data['seller_code'] = seller_code

    # Получаем ID продавца Р (центральный склад)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return ConversationHandler.END
        context.user_data['central_id'] = central['id']

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.id, o.order_number, o.created_at,
                   COUNT(oi.id) as items_count
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            WHERE o.seller_id = ? AND o.status = 'shipped'
            GROUP BY o.id
            ORDER BY o.created_at DESC
        """, (seller_id,))
        shipments = cursor.fetchall()

    if not shipments:
        await update.message.reply_text(
            "📭 У вас нет отгруженных поставок.",
            reply_markup=get_seller_menu(seller_code)
        )
        return ConversationHandler.END

    keyboard = []
    for s in shipments:
        btn_text = f"📦 {s['order_number']} от {s['created_at'][:10]} ({s['items_count']} поз.)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"shipment_{s['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="shipments_back")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "📤 Ваши отгруженные поставки:",
        reply_markup=reply_markup
    )
    return SELECTING_SHIPMENT

async def shipment_selected(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("shipment_selected: %s", query.data)

    if query.data == "shipments_back":
        await query.edit_message_text("Выход в главное меню.")
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_seller_menu(context.user_data['seller_code'])
        )
        return ConversationHandler.END

    shipment_id = int(query.data.replace('shipment_', ''))
    context.user_data['current_shipment_id'] = shipment_id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_number, o.created_at, o.shipped_at,
                   oi.id as item_id, oi.product_id, p.product_name,
                   oi.quantity_ordered, oi.price_at_order
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            JOIN products p ON oi.product_id = p.id
            WHERE o.id = ?
        """, (shipment_id,))
        items = cursor.fetchall()

    if not items:
        await query.edit_message_text("❌ Заявка не найдена.")
        return SELECTING_SHIPMENT

    order_number = items[0]['order_number']
    created_at = items[0]['created_at'][:16]
    shipped_at = items[0]['shipped_at'][:16] if items[0]['shipped_at'] else 'неизвестно'

    text = f"📦 Заявка {order_number}\n"
    text += f"📅 Создана: {created_at}\n"
    text += f"🚚 Отгружена: {shipped_at}\n\n"
    text += "Состав:\n"
    total = 0
    for item in items:
        product_name = item['product_name']
        qty = item['quantity_ordered']
        price = item['price_at_order']
        subtotal = qty * price
        total += subtotal
        text += f"• {product_name}: {qty} упак × {price} руб = {subtotal} руб\n"
    text += f"\n**Общая сумма: {total} руб**"

    context.user_data['shipment_items'] = items

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить получение", callback_data="confirm_receipt")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        text, reply_markup=reply_markup, parse_mode='Markdown'
    )
    return SELECTING_SHIPMENT

async def confirm_receipt_start(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("confirm_receipt_start called")

    items = context.user_data.get('shipment_items')
    if not items:
        await query.edit_message_text("❌ Ошибка: данные заявки не найдены.")
        return SELECTING_SHIPMENT

    context.user_data['received_quantities'] = {}
    context.user_data['receipt_index'] = 0

    await query.edit_message_text("🔄 Начинаем подтверждение получения...", reply_markup=None)
    await send_quantity_request(context, update.effective_user.id)
    return ENTERING_QUANTITY

async def send_quantity_request(context, chat_id):
    items = context.user_data['shipment_items']
    idx = context.user_data['receipt_index']
    item = items[idx]
    product_name = item['product_name']
    ordered = item['quantity_ordered']

    text = f"📦 **{product_name}**\n"
    text += f"Заказано: {ordered} упак.\n"
    text += f"Введите фактически полученное количество (целое число, не больше {ordered}):"

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=get_back_keyboard(),
        parse_mode='Markdown'
    )

async def quantity_received(update: Update, context):
    text = update.message.text
    logger.info("quantity_received: %s", text)

    if text == '🔙 Назад':
        await show_shipment_details(update, context)
        return SELECTING_SHIPMENT

    if text == '❌ Отмена':
        await update.message.reply_text(
            "❌ Подтверждение получения отменено.",
            reply_markup=get_seller_menu(context.user_data['seller_code'])
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:
        qty = int(text)
        if qty < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое неотрицательное число.\n"
            "Например: 5 или 10",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_QUANTITY

    items = context.user_data['shipment_items']
    idx = context.user_data['receipt_index']
    item = items[idx]
    ordered = item['quantity_ordered']

    if qty > ordered:
        await update.message.reply_text(
            f"❌ Полученное количество не может превышать заказанное ({ordered} упак).",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_QUANTITY

    item_id = item['item_id']
    context.user_data['received_quantities'][item_id] = qty
    context.user_data['receipt_index'] += 1

    if context.user_data['receipt_index'] >= len(items):
        await show_receipt_summary(update, context)
        return CONFIRMING_RECEIPT
    else:
        await send_quantity_request(context, update.effective_user.id)
        return ENTERING_QUANTITY

async def show_receipt_summary(update: Update, context):
    items = context.user_data['shipment_items']
    received = context.user_data['received_quantities']
    shipment_id = context.user_data['current_shipment_id']

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT order_number FROM orders WHERE id = ?", (shipment_id,))
        order_number = cursor.fetchone()[0]

    text = f"📦 Заявка {order_number}\n\n"
    text += "**Фактическое получение:**\n"
    all_full = True
    for item in items:
        product_name = item['product_name']
        ordered = item['quantity_ordered']
        rec = received.get(item['item_id'], 0)
        text += f"• {product_name}: заказано {ordered}, получено {rec}\n"
        if rec < ordered:
            all_full = False

    if all_full:
        text += "\n✅ Все товары получены полностью."
    else:
        text += "\n⚠️ Некоторые товары получены не полностью."

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить получение", callback_data="final_confirm")],
        [InlineKeyboardButton("✏️ Изменить количество", callback_data="edit_quantities")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_receipt")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

@send_backup_to_admin("подтверждение получения поставки")
async def final_confirm(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("final_confirm called")

    shipment_id = context.user_data['current_shipment_id']
    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']
    central_id = context.user_data['central_id']
    items = context.user_data['shipment_items']
    received = context.user_data['received_quantities']

    # Получаем информацию о заявке (кто создал)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT seller_id, order_number FROM orders WHERE id = ?", (shipment_id,))
        order_info = cursor.fetchone()
        order_seller_id = order_info['seller_id']
        order_number = order_info['order_number']

    underdelivered = []
    items_summary = []

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Если заявка от самого Р – это пополнение его склада
        if order_seller_id == central_id:
            # Пополнение: просто добавляем полученное на склад Р и увеличиваем его долг
            for item in items:
                product_id = item['product_id']
                product_name = item['product_name']
                ordered = item['quantity_ordered']
                rec_qty = received.get(item['item_id'], 0)
                price = item['price_at_order']
                items_summary.append(f"{product_name}: {rec_qty}/{ordered}")

                # Добавляем на склад Р
                cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (central_id, product_id))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("UPDATE seller_products SET quantity = quantity + ? WHERE seller_id = ? AND product_id = ?", (rec_qty, central_id, product_id))
                else:
                    cursor.execute("INSERT INTO seller_products (seller_id, product_id, quantity) VALUES (?, ?, ?)", (central_id, product_id, rec_qty))

                # Увеличиваем долг Р
                cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (central_id,))
                debt = cursor.fetchone()
                if debt:
                    cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (price * rec_qty, central_id))
                else:
                    cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (central_id, price * rec_qty))

                # Обновляем полученное количество в order_items
                cursor.execute("UPDATE order_items SET quantity_received = ? WHERE id = ?", (rec_qty, item['item_id']))

                if rec_qty < ordered:
                    underdelivered.append(item)

            # Обновляем статус заявки
            cursor.execute("UPDATE orders SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (shipment_id,))

            # Уведомляем админов о пополнении
            items_text = "\n".join(items_summary)
            for admin_id in config.ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"🟢 **Склад Р пополнен!**\n\n"
                             f"Заявка №{order_number}\n"
                             f"Получено:\n{items_text}"
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

            await query.edit_message_text(
                "✅ Получение подтверждено. Товар добавлен на склад Р.",
                reply_markup=None
            )
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="Выберите следующее действие:",
                reply_markup=get_seller_menu(seller_code)
            )
            context.user_data.clear()
            return ConversationHandler.END

        # Иначе заявка от другого продавца – списываем со склада Р
        # Сначала проверяем наличие всех товаров на складе Р
        for item in items:
            product_id = item['product_id']
            product_name = item['product_name']
            rec_qty = received.get(item['item_id'], 0)
            cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (central_id, product_id))
            stock_row = cursor.fetchone()
            if not stock_row or stock_row['quantity'] < rec_qty:
                await query.edit_message_text(
                    f"❌ На складе Р недостаточно товара '{product_name}'.\n"
                    f"Доступно: {stock_row['quantity'] if stock_row else 0}, запрошено: {rec_qty}.\n"
                    f"Операция отменена. Попробуйте ввести меньшее количество."
                )
                return

        # Если всё в порядке, выполняем операции
        for item in items:
            product_id = item['product_id']
            product_name = item['product_name']
            ordered = item['quantity_ordered']
            rec_qty = received.get(item['item_id'], 0)
            price = item['price_at_order']
            items_summary.append(f"{product_name}: {rec_qty}/{ordered}")

            # Списываем со склада Р
            cursor.execute("UPDATE seller_products SET quantity = quantity - ? WHERE seller_id = ? AND product_id = ?", (rec_qty, central_id, product_id))

            # Добавляем на склад заказчика
            cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (seller_id, product_id))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE seller_products SET quantity = quantity + ? WHERE seller_id = ? AND product_id = ?", (rec_qty, seller_id, product_id))
            else:
                cursor.execute("INSERT INTO seller_products (seller_id, product_id, quantity) VALUES (?, ?, ?)", (seller_id, product_id, rec_qty))

            # Увеличиваем долг заказчика
            cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (seller_id,))
            debt = cursor.fetchone()
            if debt:
                cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (price * rec_qty, seller_id))
            else:
                cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (seller_id, price * rec_qty))

            # Обновляем полученное количество в order_items
            cursor.execute("UPDATE order_items SET quantity_received = ? WHERE id = ?", (rec_qty, item['item_id']))

            if rec_qty < ordered:
                underdelivered.append(item)

        # Обновляем статус заявки
        cursor.execute("UPDATE orders SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (shipment_id,))

    # Уведомляем админов о завершении поставки
    items_text = "\n".join(items_summary)
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🟢 **Поставка завершена**\n\n"
                     f"Номер заявки: {order_number}\n"
                     f"Продавец: {seller_code}\n"
                     f"Получено:\n{items_text}\n\n"
                     f"Заявка переведена в статус «Завершена»."
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    if underdelivered:
        context.user_data['underdelivered'] = underdelivered
        keyboard = [
            [InlineKeyboardButton("✅ Да, создать", callback_data="create_shortage")],
            [InlineKeyboardButton("❌ Нет, оставить как есть", callback_data="no_shortage")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Обнаружены недопоставленные товары. Создать новую заявку на недостающее количество?",
            reply_markup=reply_markup
        )
        return CONFIRMING_RECEIPT

    await query.edit_message_text(
        "✅ Получение подтверждено. Товары добавлены на склад.",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_seller_menu(seller_code)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def create_shortage_order(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("create_shortage_order called")

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']
    underdelivered = context.user_data['underdelivered']
    received = context.user_data['received_quantities']

    from datetime import datetime
    date_str = datetime.now().strftime("%d%m")

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM orders
            WHERE seller_code = ? AND date(created_at) = date('now')
        """, (seller_code,))
        count = cursor.fetchone()[0] + 1
        new_order_number = f"{seller_code}-{date_str}-{count:03d}"

        cursor.execute("""
            INSERT INTO orders (order_number, seller_id, seller_code, status)
            VALUES (?, ?, ?, 'new')
        """, (new_order_number, seller_id, seller_code))
        new_order_id = cursor.lastrowid

        for item in underdelivered:
            shortage = item['quantity_ordered'] - received[item['item_id']]
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, quantity_ordered, price_at_order)
                VALUES (?, ?, ?, ?)
            """, (new_order_id, item['product_id'], shortage, item['price_at_order']))

    await query.edit_message_text(
        f"✅ Создана новая заявка #{new_order_number} на недостающий товар.",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_seller_menu(seller_code)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def no_shortage(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("no_shortage called")

    seller_code = context.user_data['seller_code']
    await query.edit_message_text(
        "✅ Получение подтверждено. Товары добавлены на склад.",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_seller_menu(seller_code)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def edit_quantities(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("edit_quantities called")

    context.user_data['receipt_index'] = 0
    context.user_data['received_quantities'] = {}
    await send_quantity_request(context, update.effective_user.id)
    return ENTERING_QUANTITY

async def cancel_receipt(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("cancel_receipt called")

    await query.edit_message_text("❌ Подтверждение отменено.")
    await show_shipment_details(update, context)
    return SELECTING_SHIPMENT

async def back_to_list(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("back_to_list called")
    await shipments_start(update, context)
    return SELECTING_SHIPMENT

async def show_shipment_details(update: Update, context):
    shipment_id = context.user_data['current_shipment_id']
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_number, o.created_at, o.shipped_at,
                   p.product_name, oi.quantity_ordered, oi.price_at_order
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            JOIN products p ON oi.product_id = p.id
            WHERE o.id = ?
        """, (shipment_id,))
        items = cursor.fetchall()

    order_number = items[0]['order_number']
    created_at = items[0]['created_at'][:16]
    shipped_at = items[0]['shipped_at'][:16] if items[0]['shipped_at'] else 'неизвестно'

    text = f"📦 Заявка {order_number}\n"
    text += f"📅 Создана: {created_at}\n"
    text += f"🚚 Отгружена: {shipped_at}\n\n"
    text += "Состав:\n"
    for item in items:
        text += f"• {item['product_name']}: {item['quantity_ordered']} упак × {item['price_at_order']} руб\n"

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить получение", callback_data="confirm_receipt")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return SELECTING_SHIPMENT

shipments_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📤 Отгруженные поставки$'), shipments_start)],
    states={
        SELECTING_SHIPMENT: [
            CallbackQueryHandler(shipment_selected, pattern='^shipment_'),
            CallbackQueryHandler(back_to_list, pattern='^back_to_list$'),
            CallbackQueryHandler(shipments_start, pattern='^shipments_back$'),
            CallbackQueryHandler(confirm_receipt_start, pattern='^confirm_receipt$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_received)
        ],
        CONFIRMING_RECEIPT: [
            CallbackQueryHandler(final_confirm, pattern='^final_confirm$'),
            CallbackQueryHandler(create_shortage_order, pattern='^create_shortage$'),
            CallbackQueryHandler(no_shortage, pattern='^no_shortage$'),
            CallbackQueryHandler(edit_quantities, pattern='^edit_quantities$'),
            CallbackQueryHandler(cancel_receipt, pattern='^cancel_receipt$')
        ]
    },
    fallbacks=[CommandHandler('cancel', shipments_start)],
    allow_reentry=True
)
