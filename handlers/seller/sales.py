#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для раздела "Реализовано" (продавец)
Быстрая фиксация продажи одной позиции за раз.
Списывает товар со склада продавца, увеличивает pending_amount,
уменьшает total_debt (за счёт себестоимости).
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_seller_menu, get_back_keyboard
from backup_decorator import send_backup_to_admin
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SELECTING_PRODUCT, ENTERING_QUANTITY, CONFIRMING = range(3)

async def sales_start(update: Update, context):
    user_id = update.effective_user.id
    logger.info("sales_start called by user %s", user_id)

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

    # Получаем товары с ненулевым остатком на складе продавца
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
            reply_markup=get_seller_menu(seller_code)
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

    await update.message.reply_text(
        "💰 Выберите товар, который продали:",
        reply_markup=reply_markup
    )
    return SELECTING_PRODUCT

async def product_selected(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("product_selected called with data: %s", query.data)

    if query.data == "back_to_main":
        await query.edit_message_text("Выход в главное меню.")
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_seller_menu(context.user_data['seller_code'])
        )
        return ConversationHandler.END

    product_id = int(query.data.replace('sell_', ''))
    context.user_data['selected_product_id'] = product_id

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

    await query.edit_message_text(
        f"Товар: {product['product_name']}\n"
        f"Цена: {product['price']} руб/упак\n"
        f"Доступно: {product['quantity']} упак\n\n"
        f"Введите количество проданных упаковок:",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите число:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    text = update.message.text
    logger.info("quantity_entered: %s", text)

    if text == '🔙 Назад':
        # Возвращаемся к выбору товара
        await sales_start(update, context)
        return SELECTING_PRODUCT

    if text == '❌ Отмена':
        await update.message.reply_text(
            "❌ Продажа отменена.",
            reply_markup=get_seller_menu(context.user_data['seller_code'])
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
            reply_markup=get_back_keyboard()
        )
        return ENTERING_QUANTITY

    max_qty = context.user_data['max_quantity']
    if qty > max_qty:
        await update.message.reply_text(
            f"❌ Недостаточно товара. Доступно только {max_qty} упак.",
            reply_markup=get_back_keyboard()
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
    query = update.callback_query
    await query.answer()
    logger.info("confirm_sale called")

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']
    product_id = context.user_data['selected_product_id']
    qty = context.user_data['sold_qty']
    price = context.user_data['product_price']
    total = qty * price

    today = datetime.now()
    date_str = today.strftime("%d%m")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Генерируем номер продажи
        cursor.execute("""
            SELECT COUNT(*) FROM sales
            WHERE seller_id = ? AND date(created_at) = date('now')
        """, (seller_id,))
        count = cursor.fetchone()[0] + 1
        sale_number = f"П-{seller_code}-{date_str}-{count:03d}"

        # Проверяем остаток ещё раз внутри транзакции
        cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (seller_id, product_id))
        avail = cursor.fetchone()[0]
        if avail < qty:
            await query.edit_message_text(
                "❌ Ошибка: недостаточно товара. Возможно, остаток изменился. Попробуйте снова."
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

        # Уменьшаем общий долг (себестоимость проданного товара)
        cursor.execute("""
            UPDATE seller_debt
            SET total_debt = total_debt - ?
            WHERE seller_id = ?
        """, (total, seller_id))  # здесь total = qty * price – себестоимость

        # Записываем продажу в таблицу sales
        cursor.execute("""
            INSERT INTO sales (sale_number, seller_id, product_id, quantity, amount, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sale_number, seller_id, product_id, qty, total))

    await query.edit_message_text(
        f"✅ Продажа оформлена!\n\n"
        f"Номер: {sale_number}\n"
        f"Товар: {context.user_data['product_name']}\n"
        f"Количество: {qty} упак\n"
        f"Сумма: {total} руб\n"
        f"Добавлено к переводу."
    )

    context.user_data.clear()
    # Возвращаемся в меню выбора товара для следующей продажи
    await sales_start(update, context)
    return SELECTING_PRODUCT

async def change_qty(update: Update, context):
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
        text="Введите число:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def cancel_sale(update: Update, context):
    query = update.callback_query
    await query.answer()
    logger.info("cancel_sale called")

    seller_code = context.user_data['seller_code']
    await query.edit_message_text("❌ Продажа отменена.")
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_seller_menu(seller_code)
    )
    context.user_data.clear()
    return ConversationHandler.END

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
