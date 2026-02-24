#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для раздела "Пополнение склада" (админ).
Показывает полный список товаров с возможностью пополнить склад Р на любое количество.
Учитывает активные заявки на пополнение.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu, get_back_keyboard
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

MAIN_MENU, ENTERING_QUANTITY = range(2)

async def restock_admin_start(update: Update, context):
    """Главное меню раздела – показывает все товары и кнопки для пополнения."""
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем ID продавца Р (центральный склад)
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return MAIN_MENU
        central_id = central['id']

        # Получаем все активные товары, их остаток на складе Р и сумму запросов
        cursor.execute("""
            SELECT 
                p.id as product_id,
                p.product_name,
                p.price,
                COALESCE(sp.quantity, 0) as current_quantity,
                COALESCE((
                    SELECT SUM(ri.quantity_requested)
                    FROM restock_items ri
                    JOIN restock_requests rr ON ri.request_id = rr.id
                    WHERE ri.product_id = p.id AND rr.status = 'pending'
                ), 0) as total_requested
            FROM products p
            LEFT JOIN seller_products sp ON sp.product_id = p.id AND sp.seller_id = ?
            WHERE p.is_active = 1
            ORDER BY p.product_name
        """, (central_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text(
            "📭 Нет товаров.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="restock_back")
            ]])
        )
        return MAIN_MENU

    text = "🆘 **Пополнение склада Р**\n\n"
    text += "Выберите товар для пополнения:\n\n"
    keyboard = []
    for prod in products:
        prod_id = prod['product_id']
        name = prod['product_name']
        price = prod['price']
        current = prod['current_quantity']
        requested = prod['total_requested']
        text += f"**{name}** (цена {price} руб)\n"
        text += f"   Текущий остаток: {current} упак\n"
        text += f"   Запрошено в заявках: {requested} упак\n\n"
        keyboard.append([InlineKeyboardButton(
            f"➕ Пополнить {name}",
            callback_data=f"restock_item_{prod_id}"
        )])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="restock_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return MAIN_MENU

async def select_item(update: Update, context):
    """Админ выбрал товар – запрашиваем количество для пополнения."""
    query = update.callback_query
    await query.answer()
    logger.info(f"select_item called with data: {query.data}")

    if not query.data.startswith('restock_item_'):
        return MAIN_MENU

    product_id = int(query.data.replace('restock_item_', ''))
    context.user_data['current_product_id'] = product_id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT product_name, price
            FROM products
            WHERE id = ? AND is_active = 1
        """, (product_id,))
        prod = cursor.fetchone()
        if not prod:
            await query.edit_message_text("❌ Товар не найден.")
            return MAIN_MENU
        context.user_data['product_name'] = prod['product_name']
        context.user_data['product_price'] = prod['price']

    # Убираем инлайн-клавиатуру из текущего сообщения
    await query.edit_message_text(query.message.text, reply_markup=None)

    # Запрашиваем количество
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"Товар: **{prod['product_name']}** (цена {prod['price']} руб/упак)\n\n"
             f"Введите количество упаковок для пополнения склада Р:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка введённого количества – пополняем склад Р, учитываем заявки."""
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END

    text = update.message.text
    if text == '🔙 Назад':
        await restock_admin_start(update, context)
        return MAIN_MENU

    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое положительное число.",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_QUANTITY

    product_id = context.user_data['current_product_id']
    product_name = context.user_data['product_name']
    price = context.user_data['product_price']
    total_value = qty * price

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем ID продавца Р
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return MAIN_MENU
        central_id = central['id']

        # Добавляем товар на склад Р
        cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (central_id, product_id))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("UPDATE seller_products SET quantity = quantity + ? WHERE seller_id = ? AND product_id = ?", (qty, central_id, product_id))
        else:
            cursor.execute("INSERT INTO seller_products (seller_id, product_id, quantity) VALUES (?, ?, ?)", (central_id, product_id, qty))

        # Увеличиваем долг продавца Р
        cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (central_id,))
        debt = cursor.fetchone()
        if debt:
            cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (total_value, central_id))
        else:
            cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (central_id, total_value))

        # Обрабатываем активные заявки на этот товар (распределяем пополнение)
        cursor.execute("""
            SELECT ri.id, ri.quantity_requested, rr.request_number, rr.id as request_id, rr.seller_id
            FROM restock_items ri
            JOIN restock_requests rr ON ri.request_id = rr.id
            WHERE ri.product_id = ? AND rr.status = 'pending'
            ORDER BY rr.created_at ASC
        """, (product_id,))
        pending_items = cursor.fetchall()

        remaining = qty
        for item in pending_items:
            if remaining <= 0:
                break
            take = min(item['quantity_requested'], remaining)
            cursor.execute("UPDATE restock_items SET quantity_received = ? WHERE id = ?", (take, item['id']))
            remaining -= take

        # Закрываем заявки, которые полностью выполнены
        if pending_items:
            cursor.execute("""
                SELECT request_id
                FROM restock_items
                WHERE request_id IN (SELECT DISTINCT request_id FROM restock_items WHERE product_id = ?)
                GROUP BY request_id
                HAVING SUM(quantity_received) = SUM(quantity_requested)
            """, (product_id,))
            completed_requests = cursor.fetchall()
            for req in completed_requests:
                cursor.execute("UPDATE restock_requests SET status = 'completed', completed_at = CURRENT_TIMESTAMP WHERE id = ?", (req['request_id'],))

    # Уведомляем всех продавцов о пополнении склада
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM sellers WHERE telegram_id IS NOT NULL")
        sellers = cursor.fetchall()
        for s in sellers:
            try:
                await context.bot.send_message(
                    chat_id=s['telegram_id'],
                    text=f"✅ **Склад Р пополнен!**\n\n"
                         f"Товар: {product_name}\n"
                         f"Количество: {qty} упак\n"
                         f"Теперь вы можете делать заявки."
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить продавца {s['telegram_id']}: {e}")

    await update.message.reply_text(
        f"✅ Пополнение выполнено!\n"
        f"Товар {product_name} добавлен на склад Р в количестве {qty} упак.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К списку товаров", callback_data="restock_back_to_list")
        ]])
    )
    context.user_data.clear()
    return MAIN_MENU

async def back_to_list(update: Update, context):
    """Вернуться к списку товаров."""
    query = update.callback_query
    await query.answer()
    await restock_admin_start(update, context)
    return MAIN_MENU

async def back_to_admin(update: Update, context):
    """Выход в главное админское меню."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Выход в главное меню", reply_markup=get_admin_menu())
    return ConversationHandler.END

restock_admin_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^🆘 Пополнение склада$'), restock_admin_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(select_item, pattern='^restock_item_'),
            CallbackQueryHandler(back_to_admin, pattern='^restock_back$'),
            CallbackQueryHandler(back_to_list, pattern='^restock_back_to_list$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ]
    },
    fallbacks=[CommandHandler('cancel', back_to_admin)],
    allow_reentry=True
)
