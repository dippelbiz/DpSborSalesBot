#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для раздела "Реализовано" (продавец)
Быстрая фиксация продажи одной позиции за раз.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_main_menu, get_back_and_cancel_keyboard
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

# Состояния разговора
SELECTING_PRODUCT, ENTERING_QUANTITY, CONFIRMING = range(3)

async def sales_start(update: Update, context):
    """Начало раздела 'Реализовано' – показать список доступных товаров."""
    user_id = update.effective_user.id
    logger.info("sales_start called by user %s", user_id)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
        if not seller:
            await update.message.reply_text(
                "❌ Вы не активированы как продавец. Нажмите /start для активации.",
                reply_markup=get_main_menu()
            )
            return ConversationHandler.END
        seller_id = seller['id']
        context.user_data['seller_id'] = seller_id

    # Получаем товары с ненулевым остатком
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.product_name, p.price, sp.quantity
            FROM products p
            JOIN seller_products sp ON p.id = sp.product_id
            WHERE sp.seller_id = ? AND p.is_active = 1 AND sp.quantity > 0
            ORDER BY p.product_name
        """, (seller_id,))
        products = cursor.fetchall()
        logger.info("Found %d products with positive stock", len(products))

    if not products:
        await update.message.reply_text(
            "📭 У вас нет товаров в наличии для продажи.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    # Формируем инлайн-клавиатуру с товарами
    keyboard = []
    for prod in products:
        button = InlineKeyboardButton(
            f"{prod['product_name']} – {prod['quantity']} упак (цена {prod['price']} руб)",
            callback_data=f"sell_{prod['id']}"
        )
        keyboard.append([button])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    logger.info("Sending keyboard with %d buttons", len(keyboard))

    await update.message.reply_text(
        "💰 Выберите товар, который продали:",
        reply_markup=reply_markup
    )
    return SELECTING_PRODUCT

async def product_selected(update: Update, context):
    """Обработка выбора товара – запрашиваем количество."""
    query = update.callback_query
    await query.answer()
    logger.info("product_selected called with data: %s", query.data)

    if query.data == "back_to_main":
        await query.edit_message_text("Выход в главное меню.")
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    product_id = int(query.data.replace('sell_', ''))
    context.user_data['selected_product_id'] = product_id

    # Получаем информацию о товаре
    seller_id = context.user_data['seller_id']
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.product_name, p.price, sp.quantity
            FROM products p
            JOIN seller_products sp ON p.id = sp.product_id
            WHERE sp.seller_id = ? AND p.id = ?
        """, (seller_id, product_id))
        product = cursor.fetchone()

    if not product:
        await query.edit_message_text("❌ Товар не найден.")
        return SELECTING_PRODUCT

    context.user_data['product_name'] = product['product_name']
    context.user_data['product_price'] = product['price']
    context.user_data['max_quantity'] = product['quantity']

    # Убираем инлайн-клавиатуру, так как переходим к вводу текста
    await query.edit_message_text(
        f"Товар: {product['product_name']}\n"
        f"Цена: {product['price']} руб/упак\n"
        f"Доступно: {product['quantity']} упак\n\n"
        f"Введите количество проданных упаковок:",
        reply_markup=None
    )
    # После этого бот будет ждать текстового ввода – клавиатура "Назад/Отмена" будет отправлена отдельно,
    # но она уже есть в get_back_and_cancel_keyboard, которую мы используем в обработчике сообщений.
    # В данном месте мы не отправляем клавиатуру, она появится, когда пользователь начнёт ввод,
    # потому что обработчик quantity_entered использует get_back_and_cancel_keyboard при ошибках,
    # но для первого запроса мы её не даём. Чтобы пользователь сразу видел кнопки, нужно отправить сообщение с клавиатурой.
    # Лучше после редактирования отправить новое сообщение с клавиатурой. Но edit_message_text не может добавить обычную клавиатуру.
    # Поэтому мы отредактируем сообщение, убрав инлайн, а затем следующим сообщением отправим запрос с reply-клавиатурой.
    # Но так как у нас уже есть состояние ENTERING_QUANTITY, следующий шаг – ожидание сообщения, а клавиатуру мы можем отправить прямо сейчас отдельно.

    # Отправим отдельное сообщение с запросом количества и reply-клавиатурой
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"Введите количество проданных упаковок:",
        reply_markup=get_back_and_cancel_keyboard()
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка ввода количества – показываем подтверждение."""
    text = update.message.text
    logger.info("quantity_entered: %s", text)

    if text == '🔙 Назад':
        # Возвращаемся к выбору товара
        await sales_start(update, context)
        return SELECTING_PRODUCT

    if text == '❌ Отмена':
        await update.message.reply_text(
            "❌ Продажа отменена.",
            reply_markup=get_main_menu()
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:
        qty = int(text)
        if qty <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое положительное число.\n"
            "Например: 5 или 10",
            reply_markup=get_back_and_cancel_keyboard()
        )
        return ENTERING_QUANTITY

    max_qty = context.user_data['max_quantity']
    if qty > max_qty:
        await update.message.reply_text(
            f"❌ Недостаточно товара. Доступно только {max_qty} упак.",
            reply_markup=get_back_and_cancel_keyboard()
        )
        return ENTERING_QUANTITY

    context.user_data['sold_qty'] = qty
    product_name = context.user_data['product_name']
    price = context.user_data['product_price']
    total = qty * price

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_sale")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="change_qty")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_sale")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"Товар: {product_name}\n"
        f"Количество: {qty} упак\n"
        f"Цена: {price} руб/упак\n"
        f"Сумма: {total} руб\n\n"
        f"Подтверждаете продажу?",
        reply_markup=reply_markup
    )
    return CONFIRMING

@send_backup_to_admin("продажа товара")
async def confirm_sale(update: Update, context):
    """Подтверждение продажи – списываем товар, увеличиваем pending, возвращаемся к списку."""
    query = update.callback_query
    await query.answer()
    logger.info("confirm_sale called")

    seller_id = context.user_data['seller_id']
    product_id = context.user_data['selected_product_id']
    qty = context.user_data['sold_qty']
    price = context.user_data['product_price']
    total = qty * price

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Проверяем остаток ещё раз (на всякий случай)
        cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (seller_id, product_id))
        avail = cursor.fetchone()[0]
        if avail < qty:
            await query.edit_message_text(
                "❌ Ошибка: недостаточно товара. Возможно, остаток изменился. Попробуйте снова.",
                reply_markup=None
            )
            return SELECTING_PRODUCT

        # Списываем товар
        cursor.execute("""
            UPDATE seller_products
            SET quantity = quantity - ?
            WHERE seller_id = ? AND product_id = ?
        """, (qty, seller_id, product_id))

        # Увеличиваем сумму к переводу
        cursor.execute("""
            UPDATE seller_pending
            SET pending_amount = pending_amount + ?
            WHERE seller_id = ?
        """, (total, seller_id))

        # Записываем продажу в таблицу sales
        cursor.execute("""
            INSERT INTO sales (seller_id, product_id, quantity, amount, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (seller_id, product_id, qty, total))

    # Убираем клавиатуру с подтверждения
    await query.edit_message_text(
        f"✅ Продажа оформлена!\n\n"
        f"Товар: {context.user_data['product_name']}\n"
        f"Количество: {qty} упак\n"
        f"Сумма: {total} руб\n"
        f"Добавлено к переводу.",
        reply_markup=None
    )

    # Очищаем временные данные, но оставляем seller_id
    context.user_data.pop('selected_product_id', None)
    context.user_data.pop('product_name', None)
    context.user_data.pop('product_price', None)
    context.user_data.pop('max_quantity', None)
    context.user_data.pop('sold_qty', None)

    # Возвращаемся к списку товаров для следующей продажи
    await sales_start(update, context)
    return SELECTING_PRODUCT

async def change_qty(update: Update, context):
    """Изменить количество – возвращаемся к вводу."""
    query = update.callback_query
    await query.answer()
    logger.info("change_qty called")

    await query.edit_message_text(
        f"Товар: {context.user_data['product_name']}\n"
        f"Цена: {context.user_data['product_price']} руб/упак\n"
        f"Доступно: {context.user_data['max_quantity']} упак\n\n"
        f"Введите новое количество:",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите новое количество:",
        reply_markup=get_back_and_cancel_keyboard()
    )
    return ENTERING_QUANTITY

async def cancel_sale(update: Update, context):
    """Отмена текущей продажи – возврат к выбору товара."""
    query = update.callback_query
    await query.answer()
    logger.info("cancel_sale called")

    await query.edit_message_text("❌ Продажа отменена.", reply_markup=None)
    await sales_start(update, context)
    return SELECTING_PRODUCT

# ConversationHandler для продаж
sales_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^💰 Реализовано$'), sales_start)],
    states={
        SELECTING_PRODUCT: [
            CallbackQueryHandler(product_selected, pattern='^sell_'),
            CallbackQueryHandler(product_selected, pattern='^back_to_main$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ],
        CONFIRMING: [
            CallbackQueryHandler(confirm_sale, pattern='^confirm_sale$'),
            CallbackQueryHandler(change_qty, pattern='^change_qty$'),
            CallbackQueryHandler(cancel_sale, pattern='^cancel_sale$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_sale)],
    allow_reentry=True
)
