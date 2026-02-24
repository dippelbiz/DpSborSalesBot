#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для заявок на пополнение центрального склада (продавец).
Любой продавец (включая Р) может создать заявку на закупку товара.
Количество не ограничено.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_back_keyboard, get_restock_confirm_keyboard
from backup_decorator import send_backup_to_admin
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Состояния разговора
SELECTING_PRODUCT, ENTERING_QUANTITY, CONFIRMING = range(3)

async def restock_start(update: Update, context):
    """Начало создания заявки на пополнение. Показываем список товаров с остатками на складе Р."""
    user_id = update.effective_user.id
    logger.info("restock_start called by user %s", user_id)

    # Получаем информацию о продавце
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, seller_code FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
        if not seller:
            await update.message.reply_text("❌ Ошибка: продавец не найден.")
            return ConversationHandler.END
        seller_id = seller['id']
        seller_code = seller['seller_code']
        context.user_data['seller_id'] = seller_id
        context.user_data['seller_code'] = seller_code

    # Получаем список товаров и остатки на складе Р (продавец с кодом 'Р')
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return ConversationHandler.END
        central_id = central['id']
        context.user_data['central_id'] = central_id

        cursor.execute("""
            SELECT p.id, p.product_name, p.price, COALESCE(sp.quantity, 0) as central_quantity
            FROM products p
            LEFT JOIN seller_products sp ON sp.product_id = p.id AND sp.seller_id = ?
            WHERE p.is_active = 1
            ORDER BY p.product_name
        """, (central_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text("📭 Нет доступных товаров.")
        return ConversationHandler.END

    # Сохраняем список товаров в контекст
    context.user_data['products'] = products
    context.user_data['cart'] = {}  # товары, добавленные в текущую заявку

    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def show_product_selection(update: Update, context):
    """Показывает список товаров с возможностью выбора."""
    products = context.user_data['products']
    cart = context.user_data.get('cart', {})

    # Формируем текст с текущей корзиной
    text = "📦 **Создание заявки на пополнение склада**\n\n"
    if cart:
        text += "**Добавленные товары:**\n"
        total = 0
        for pid, item in cart.items():
            subtotal = item['qty'] * item['price']
            total += subtotal
            text += f"• {item['name']}: {item['qty']} упак × {item['price']} руб = {subtotal} руб\n"
        text += f"\n**Общая сумма: {total} руб**\n\n"
    text += "**Доступные товары (остаток на складе Р):**"

    # Клавиатура с товарами
    keyboard = []
    for prod in products:
        prod_id = prod['id']
        name = prod['product_name']
        price = prod['price']
        central_qty = prod['central_quantity']
        button_text = f"{name} ({price} руб) – на складе Р: {central_qty} упак"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"restock_prod_{prod_id}")])

    # Кнопка завершения, если корзина не пуста
    if cart:
        keyboard.append([InlineKeyboardButton("✅ Завершить заявку", callback_data="restock_finish")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="restock_cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def product_selected(update: Update, context):
    """Обработка выбора товара – запрашиваем количество."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "restock_finish":
        # Переходим к финальному подтверждению
        await show_restock_summary(update, context)
        return CONFIRMING
    elif data == "restock_cancel":
        await query.edit_message_text("❌ Заявка отменена.")
        await context.bot.send_message(chat_id=update.effective_user.id, text="Выберите действие:", reply_markup=get_seller_menu(context.user_data['seller_code']))
        context.user_data.clear()
        return ConversationHandler.END

    product_id = int(data.replace('restock_prod_', ''))
    context.user_data['selected_product_id'] = product_id

    # Находим товар в списке
    product = next((p for p in context.user_data['products'] if p['id'] == product_id), None)
    if not product:
        await query.edit_message_text("❌ Товар не найден.")
        return SELECTING_PRODUCT

    context.user_data['selected_product_name'] = product['product_name']
    context.user_data['selected_product_price'] = product['price']
    context.user_data['selected_product_central_qty'] = product['central_quantity']

    await query.edit_message_text(
        f"Товар: {product['product_name']}\n"
        f"Цена: {product['price']} руб/упак\n"
        f"На складе Р сейчас: {product['central_quantity']} упак\n\n"
        f"Введите количество упаковок для заказа (можно любое число):",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите число:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка ввода количества – добавляем в корзину."""
    text = update.message.text

    if text == '🔙 Назад':
        await show_product_selection(update, context)
        return SELECTING_PRODUCT

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

    # Добавляем в корзину
    prod_id = context.user_data['selected_product_id']
    prod_name = context.user_data['selected_product_name']
    price = context.user_data['selected_product_price']

    cart = context.user_data.get('cart', {})
    if prod_id in cart:
        cart[prod_id]['qty'] += qty
    else:
        cart[prod_id] = {
            'name': prod_name,
            'price': price,
            'qty': qty
        }
    context.user_data['cart'] = cart

    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def show_restock_summary(update: Update, context):
    """Показывает сводку корзины и запрашивает подтверждение."""
    cart = context.user_data['cart']
    text = "📋 **Проверьте заявку на пополнение:**\n\n"
    total = 0
    for item in cart.values():
        subtotal = item['qty'] * item['price']
        total += subtotal
        text += f"• {item['name']}: {item['qty']} упак × {item['price']} руб = {subtotal} руб\n"
    text += f"\n**Общая сумма: {total} руб**"

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="restock_confirm")],
        [InlineKeyboardButton("✏️ Изменить состав", callback_data="restock_edit")],
        [InlineKeyboardButton("❌ Отмена", callback_data="restock_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

@send_backup_to_admin("заявка на пополнение склада")
async def restock_confirm(update: Update, context):
    """Финальное подтверждение – сохраняем заявку в БД."""
    query = update.callback_query
    await query.answer()
    logger.info("restock_confirm called")

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']
    cart = context.user_data['cart']

    # Генерируем номер заявки (З – закупка)
    today = datetime.now()
    date_str = today.strftime("%d%m")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM restock_requests
            WHERE seller_code = ? AND date(created_at) = date('now')
        """, (seller_code,))
        count = cursor.fetchone()[0] + 1
        request_number = f"З-{seller_code}-{date_str}-{count:03d}"

        # Создаём заявку
        cursor.execute("""
            INSERT INTO restock_requests (request_number, seller_id, seller_code, status)
            VALUES (?, ?, ?, 'pending')
        """, (request_number, seller_id, seller_code))
        request_id = cursor.lastrowid

        # Добавляем товары
        for prod_id, item in cart.items():
            cursor.execute("""
                INSERT INTO restock_items (request_id, product_id, quantity_requested)
                VALUES (?, ?, ?)
            """, (request_id, prod_id, item['qty']))

    # Уведомляем админов
    items_summary = "\n".join([f"{item['name']}: {item['qty']} упак" for item in cart.values()])
    total_sum = sum(item['qty'] * item['price'] for item in cart.values())
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🆘 **Новая заявка на пополнение склада!**\n\n"
                     f"Номер: {request_number}\n"
                     f"Продавец: {seller_code}\n"
                     f"{items_summary}\n"
                     f"Общая сумма: {total_sum} руб\n\n"
                     f"Перейдите в раздел «🆘 Пополнение склада» для обработки."
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    await query.edit_message_text(
        f"✅ Заявка на пополнение №{request_number} создана!\n\n"
        f"Ожидайте, администратор обработает заявку.",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_seller_menu(seller_code)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def restock_edit(update: Update, context):
    """Вернуться к выбору товара."""
    query = update.callback_query
    await query.answer()
    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def restock_cancel(update: Update, context):
    """Полная отмена."""
    query = update.callback_query
    await query.answer()
    seller_code = context.user_data.get('seller_code')
    await query.edit_message_text("❌ Заявка отменена.")
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_seller_menu(seller_code) if seller_code else get_main_menu()
    )
    context.user_data.clear()
    return ConversationHandler.END

restock_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📦 Заявка на пополнение склада$'), restock_start)],
    states={
        SELECTING_PRODUCT: [
            CallbackQueryHandler(product_selected, pattern='^restock_prod_'),
            CallbackQueryHandler(product_selected, pattern='^restock_finish$'),
            CallbackQueryHandler(product_selected, pattern='^restock_cancel$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ],
        CONFIRMING: [
            CallbackQueryHandler(restock_confirm, pattern='^restock_confirm$'),
            CallbackQueryHandler(restock_edit, pattern='^restock_edit$'),
            CallbackQueryHandler(restock_cancel, pattern='^restock_cancel$')
        ]
    },
    fallbacks=[CommandHandler('cancel', restock_cancel)],
    allow_reentry=True
)
