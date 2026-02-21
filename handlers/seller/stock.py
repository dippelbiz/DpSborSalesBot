#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для раздела "Остатки" (продавец)
Показывает текущие остатки товаров на складе продавца,
общую сумму долга, сумму к переводу и предлагает запросить выплату.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import MessageHandler, filters
from database import db
from config import config
from keyboards import get_main_menu
import logging

logger = logging.getLogger(__name__)

async def stock_start(update: Update, context):
    """Показать остатки товаров продавца, долг, сумму к переводу и кнопку выплаты."""
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

        # Получаем остатки продавца
        cursor.execute("""
            SELECT 
                p.product_name,
                sp.quantity,
                p.price,
                (sp.quantity * p.price) as total
            FROM seller_products sp
            JOIN products p ON sp.product_id = p.id
            WHERE sp.seller_id = ? AND p.is_active = 1
            ORDER BY p.product_name
        """, (seller_id,))
        products = cursor.fetchall()

        # Получаем сумму к переводу
        cursor.execute("SELECT pending_amount FROM seller_pending WHERE seller_id = ?", (seller_id,))
        pending_row = cursor.fetchone()
        pending_amount = pending_row['pending_amount'] if pending_row else 0

    # Формируем сообщение
    if not products:
        text = "📭 У вас пока нет товаров на складе.\n\n"
    else:
        text = "📊 **Ваши остатки на складе**\n\n"
        total_debt = 0
        for prod in products:
            text += f"• **{prod['product_name']}**\n"
            text += f"  Количество: {prod['quantity']} упак\n"
            text += f"  Цена: {prod['price']} руб/упак\n"
            text += f"  Стоимость: {prod['total']} руб\n\n"
            total_debt += prod['total']
        text += f"💰 **Общая стоимость товаров (долг перед админом): {total_debt} руб**\n"

    text += f"💵 **Сумма к переводу (от продаж): {pending_amount} руб**\n"
    text += f"_Эту сумму можно передать администратору._"

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

# Обработчик для кнопки "Остатки"
stock_handler = MessageHandler(filters.Regex('^📊 Остатки$'), stock_start)
