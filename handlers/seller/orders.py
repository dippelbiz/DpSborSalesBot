#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для заявок на поставку (продавец)
Мультитоварная заявка с накоплением товаров в корзине.
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
SELECTING_PRODUCT, ENTERING_QUANTITY, CONFIRMING_CART = range(3)

async def orders_start(update: Update, context):
    """Начало создания заявки (инициализация корзины)"""
    logger.info("orders_start called by user %s", update.effective_user.id)

    user_id = update.effective_user.id
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()

    if not seller:
        await update.message.reply_text(
            "❌ Вы не активированы как продавец. Нажмите /start для активации.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    context.user_data['seller_id'] = seller['id']
    context.user_data['seller_code'] = seller['seller_code']
    context.user_data['cart'] = {}

    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def show_product_selection(update: Update, context):
    """Отправляет сообщение с инлайн-кнопками товаров."""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, product_name, price FROM products WHERE is_active = 1 ORDER BY product_name")
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text(
            "❌ В данный момент нет доступных товаров.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    keyboard = []
    row = []
    for i, prod in enumerate(products):
        button = InlineKeyboardButton(
            f"{prod['product_name']} ({prod['price']} руб)",
            callback_data=f"product_{prod['id']}"
        )
        row.append(button)
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📦 Выберите товар для добавления в заявку:",
        reply_markup=reply_markup
    )

async def product_selected(update: Update, context):
    """Обработка выбора товара (запрос количества)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        await query.edit_message_text("❌ Создание заявки отменено.")
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    product_id = int(data.replace('product_', ''))
    context.user_data['selected_product_id'] = product_id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, price FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()

    if not product:
        await query.edit_message_text("❌ Товар не найден.")
        return ConversationHandler.END

    context.user_data['selected_product_name'] = product['product_name']
    context.user_data['selected_product_price'] = product['price']

    await query.edit_message_text(
        f"Товар: {product['product_name']}\n"
        f"Цена: {product['price']} руб/упак\n\n"
        f"Введите количество упаковок (только целое число):",
        reply_markup=None
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка ввода количества."""
    text = update.message.text

    if text == '🔙 Назад':
        await show_product_selection(update, context)
        return SELECTING_PRODUCT

    if text == '❌ Отмена':
        await update.message.reply_text(
            "❌ Создание заявки отменено.",
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

    prod_id = context.user_data['selected_product_id']
    prod_name = context.user_data['selected_product_name']
    prod_price = context.user_data['selected_product_price']

    cart = context.user_data.get('cart', {})
    if prod_id in cart:
        cart[prod_id]['qty'] += qty
    else:
        cart[prod_id] = {
            'name': prod_name,
            'price': prod_price,
            'qty': qty
        }
    context.user_data['cart'] = cart

    await show_cart_summary(update, context)
    return CONFIRMING_CART

async def show_cart_summary(update: Update, context):
    """Отображает текущее содержимое корзины и кнопки действий."""
    cart = context.user_data.get('cart', {})
    if not cart:
        await show_product_selection(update, context)
        return SELECTING_PRODUCT

    text = "📋 **Проверьте заявку:**\n\n"
    total_sum = 0
    for prod_id, item in cart.items():
        item_sum = item['qty'] * item['price']
        total_sum += item_sum
        text += f"**{item['name']}**\n"
        text += f"Количество: {item['qty']} упак\n"
        text += f"Цена: {item['price']} руб/упак\n"
        text += f"Сумма: {item_sum} руб\n\n"

    text += f"**Общий заказ на сумму: {total_sum} руб**"

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить заявку", callback_data="confirm_order")],
        [InlineKeyboardButton("➕ Добавить ещё товар", callback_data="add_more")],
        [InlineKeyboardButton("❌ Отменить всё", callback_data="cancel_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode='Markdown'
        )

async def add_more(update: Update, context):
    """Возврат к выбору товара для добавления (редактирует текущее сообщение)."""
    query = update.callback_query
    await query.answer()

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, product_name, price FROM products WHERE is_active = 1 ORDER BY product_name")
        products = cursor.fetchall()

    if not products:
        await query.edit_message_text("❌ Нет доступных товаров.")
        return SELECTING_PRODUCT

    keyboard = []
    row = []
    for i, prod in enumerate(products):
        button = InlineKeyboardButton(
            f"{prod['product_name']} ({prod['price']} руб)",
            callback_data=f"product_{prod['id']}"
        )
        row.append(button)
        if (i + 1) % 2 == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "📦 Выберите товар для добавления в заявку:",
        reply_markup=reply_markup
    )
    return SELECTING_PRODUCT

@send_backup_to_admin("создание заявки на поставку")
async def confirm_order(update: Update, context):
    """Подтверждение и сохранение заявки в БД."""
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get('cart', {})
    if not cart:
        await query.edit_message_text("❌ Корзина пуста. Заявка не может быть создана.")
        return ConversationHandler.END

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']

    from datetime import datetime
    date_str = datetime.now().strftime("%d%m")

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM orders
            WHERE seller_code = ? AND date(created_at) = date('now')
        """, (seller_code,))
        count = cursor.fetchone()[0] + 1
        order_number = f"{seller_code}-{date_str}-{count:03d}"

        cursor.execute("""
            INSERT INTO orders (order_number, seller_id, seller_code, status)
            VALUES (?, ?, ?, 'new')
        """, (order_number, seller_id, seller_code))
        order_id = cursor.lastrowid

        for prod_id, item in cart.items():
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, quantity_ordered, price_at_order)
                VALUES (?, ?, ?, ?)
            """, (order_id, prod_id, item['qty'], item['price']))

    await query.edit_message_text(
        f"✅ Заявка № {order_number} успешно создана!",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_main_menu()
    )

    context.user_data.clear()
    return ConversationHandler.END

async def cancel_all(update: Update, context):
    """Полная отмена создания заявки."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text("❌ Создание заявки отменено.", reply_markup=None)
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_main_menu()
    )
    return ConversationHandler.END

async def my_orders(update: Update, context):
    """Просмотр своих заявок."""
    user_id = update.effective_user.id
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE telegram_id = ?", (user_id,))
        result = cursor.fetchone()

    if not result:
        await update.message.reply_text(
            "❌ Вы не активированы как продавец.",
            reply_markup=get_main_menu()
        )
        return

    seller_id = result[0]

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.order_number, o.status, o.created_at,
                   GROUP_CONCAT(p.product_name || ' ' || oi.quantity_ordered || ' упак') as items
            FROM orders o
            LEFT JOIN order_items oi ON o.id = oi.order_id
            LEFT JOIN products p ON oi.product_id = p.id
            WHERE o.seller_id = ?
            GROUP BY o.id
            ORDER BY o.created_at DESC
            LIMIT 10
        """, (seller_id,))
        orders = cursor.fetchall()

    if not orders:
        await update.message.reply_text(
            "У вас пока нет заявок.",
            reply_markup=get_main_menu()
        )
        return

    text = "📋 Ваши последние заявки:\n\n"
    for order in orders:
        status_emoji = {
            'new': '🟡',
            'shipped': '🔵',
            'completed': '🟢',
            'cancelled': '⚫'
        }.get(order['status'], '⚪')
        text += f"{status_emoji} {order['order_number']} от {order['created_at'][:10]}\n"
        text += f"   {order['items']}\n\n"

    await update.message.reply_text(text, reply_markup=get_main_menu())

# --- Обработчики для регистрации в main.py ---
my_orders_handler = MessageHandler(filters.Regex('^📋 Мои заявки$'), my_orders)

# ConversationHandler для создания заявок
orders_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📦 Заявка на поставку$'), orders_start)],
    states={
        SELECTING_PRODUCT: [
            CallbackQueryHandler(product_selected, pattern='^product_'),
            CallbackQueryHandler(product_selected, pattern='^cancel$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ],
        CONFIRMING_CART: [
            CallbackQueryHandler(add_more, pattern='^add_more$'),
            CallbackQueryHandler(confirm_order, pattern='^confirm_order$'),
            CallbackQueryHandler(cancel_all, pattern='^cancel_all$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_all)],
    allow_reentry=True
)
