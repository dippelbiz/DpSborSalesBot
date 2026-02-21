#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для раздела "Остатки" (продавец)
Показывает текущие остатки товаров на складе продавца,
количество проданного, стоимость непроданных товаров,
общую сумму долга (она же стоимость непроданных) и сумму к переводу.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_main_menu
import logging

logger = logging.getLogger(__name__)

async def stock_start(update: Update, context):
    """Показать остатки товаров продавца, продажи, долг и сумму к переводу."""
    user_id = update.effective_user.id
    logger.info("stock_start called by user %s", user_id)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
        if not seller:
            await update.message.reply_text(
                "❌ Вы не активированы как продавец. Нажмите /start для активации.",
                reply_markup=get_main_menu()
            )
            return
        seller_id = seller['id']

        # Получаем данные по каждому товару: остаток, сумма продаж, цена
        cursor.execute("""
            SELECT 
                p.product_name,
                COALESCE(sp.quantity, 0) as stock_quantity,
                p.price,
                COALESCE(SUM(s.quantity), 0) as sold_quantity
            FROM products p
            LEFT JOIN seller_products sp ON sp.product_id = p.id AND sp.seller_id = ?
            LEFT JOIN sales s ON s.product_id = p.id AND s.seller_id = ?
            WHERE p.is_active = 1
            GROUP BY p.id
            ORDER BY p.product_name
        """, (seller_id, seller_id))
        products = cursor.fetchall()

        # Получаем сумму к переводу
        cursor.execute("SELECT pending_amount FROM seller_pending WHERE seller_id = ?", (seller_id,))
        pending_row = cursor.fetchone()
        pending_amount = pending_row['pending_amount'] if pending_row else 0

    # Формируем сообщение
    text = "📊 **Ваши остатки на складе**\n\n"
    total_unsold_value = 0
    for prod in products:
        product_name = prod['product_name']
        stock = prod['stock_quantity']
        price = prod['price']
        sold = prod['sold_quantity']
        unsold_value = stock * price
        total_unsold_value += unsold_value

        text += f"• **{product_name}**\n"
        text += f"  Остаток на складе: {stock} упак\n"
        text += f"  Продано: {sold} упак\n"
        text += f"  Стоимость непроданных товаров: {unsold_value} руб\n\n"

    text += f"💰 **Стоимость непроданных товаров на складе: {total_unsold_value} руб**\n"
    text += f"💵 **Сумма к переводу (от продаж): {pending_amount} руб**\n"
    text += f"_Эту сумму нужно передать администратору._"

    # Добавляем инлайн-кнопку для запроса выплаты (если есть что переводить)
    keyboard = []
    if pending_amount > 0:
        keyboard.append([InlineKeyboardButton("💰 Перевести деньги", callback_data="request_payment")])
    keyboard.append([InlineKeyboardButton("🔙 В меню", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def handle_back_to_main(update: Update, context):
    """Обработчик нажатия кнопки 'В меню' в разделе 'Остатки'."""
    query = update.callback_query
    await query.answer()
    logger.info("handle_back_to_main called by user %s", update.effective_user.id)

    await query.edit_message_text("Возврат в главное меню.")
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_main_menu()
    )

# Обработчик для кнопки "Остатки"
stock_handler = MessageHandler(filters.Regex('^📊 Остатки$'), stock_start)

# Обработчик для кнопки "В меню" внутри сообщения остатков
back_to_main_handler = CallbackQueryHandler(handle_back_to_main, pattern='^back_to_main$')
