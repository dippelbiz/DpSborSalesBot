#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для заявок на поставку (продавец)
Мультитоварная заявка с накоплением товаров в корзине.
При создании проверяется доступное количество на складе Р.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_main_menu, get_back_keyboard, get_confirm_keyboard, get_seller_menu
from backup_decorator import send_backup_to_admin
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

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

    # Получаем ID продавца Р (центральный склад)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return ConversationHandler.END
        context.user_data['central_id'] = central['id']

    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def show_product_selection(update: Update, context):
    central_id = context.user_data['central_id']
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.id, p.product_name, p.price, COALESCE(sp.quantity, 0) as central_quantity
            FROM products p
            LEFT JOIN seller_products sp ON sp.product_id = p.id AND sp.seller_id = ?
            WHERE p.is_active = 1
            ORDER BY p.product_name
        """, (central_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text(
            "❌ В данный момент нет доступных товаров.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    context.user_data['products'] = products

    cart = context.user_data.get('cart', {})
    text = "📦 **Создание заявки на поставку**\n\n"
    if cart:
        text += "**Товары в заявке:**\n"
        total = 0
        for prod_id, item in cart.items():
            subtotal = item['qty'] * item['price']
            total += subtotal
            text += f"• {item['name']}: {item['qty']} упак × {item['price']} руб = {subtotal} руб\n"
        text += f"\n**Общая сумма: {total} руб**\n\n"
    text += "**Доступные товары (остаток на складе Р):**"

    keyboard = []
    for prod in products:
        prod_id = prod['id']
        name = prod['product_name']
        price = prod['price']
        central_qty = prod['central_quantity']
        button_text = f"{name} ({price} руб) – доступно {central_qty} упак"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"prod_{prod_id}")])

    if cart:
        keyboard.append([InlineKeyboardButton("✅ Завершить заявку", callback_data="finish_cart")])
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def product_selected(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "finish_cart":
        await show_cart_summary(update, context)
        return CONFIRMING_CART
    elif data == "cancel":
        await query.edit_message_text("❌ Создание заявки отменено.")
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_seller_menu(context.user_data['seller_code'])
        )
        context.user_data.clear()
        return ConversationHandler.END

    product_id = int(data.replace('prod_', ''))
    context.user_data['selected_product_id'] = product_id

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
        f"Доступно на складе Р: {product['central_quantity']} упак\n\n"
        f"Введите количество упаковок для заказа (не больше {product['central_quantity']}):",
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

    if text == '🔙 Назад':
        await show_product_selection(update, context)
        return SELECTING_PRODUCT

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

    max_qty = context.user_data['selected_product_central_qty']
    if qty > max_qty:
        await update.message.reply_text(
            f"❌ На складе Р недостаточно товара. Доступно только {max_qty} упак.",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_QUANTITY

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

async def show_cart_summary(update: Update, context):
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
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

@send_backup_to_admin("создание заявки на поставку")
async def confirm_order(update: Update, context):
    query = update.callback_query
    await query.answer()

    cart = context.user_data.get('cart', {})
    if not cart:
        await query.edit_message_text("❌ Корзина пуста. Заявка не может быть создана.")
        return ConversationHandler.END

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']

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

        items_summary = []
        for prod_id, item in cart.items():
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, quantity_ordered, price_at_order)
                VALUES (?, ?, ?, ?)
            """, (order_id, prod_id, item['qty'], item['price']))
            items_summary.append(f"{item['name']}: {item['qty']} упак")

    await query.edit_message_text(
        f"✅ Заявка № {order_number} успешно создана!",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_seller_menu(seller_code)
    )

    # Уведомляем админов
    total_sum = sum(item['qty'] * item['price'] for item in cart.values())
    items_text = "\n".join(items_summary)
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"🟡 **Новая заявка на поставку!**\n\n"
                     f"Номер: {order_number}\n"
                     f"Продавец: {seller_code}\n"
                     f"{items_text}\n"
                     f"Общая сумма: {total_sum} руб"
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить админа {admin_id}: {e}")

    context.user_data.clear()
    return ConversationHandler.END

async def add_more(update: Update, context):
    query = update.callback_query
    await query.answer()
    await show_product_selection(update, context)
    return SELECTING_PRODUCT

async def cancel_all(update: Update, context):
    query = update.callback_query
    await query.answer()
    seller_code = context.user_data.get('seller_code')
    context.user_data.clear()
    await query.edit_message_text("❌ Создание заявки отменено.", reply_markup=None)
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_seller_menu(seller_code) if seller_code else get_main_menu()
    )
    return ConversationHandler.END

async def my_orders(update: Update, context):
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

my_orders_handler = MessageHandler(filters.Regex('^📋 Мои заявки$'), my_orders)

orders_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📦 Заявка на поставку$'), orders_start)],
    states={
        SELECTING_PRODUCT: [
            CallbackQueryHandler(product_selected, pattern='^prod_'),
            CallbackQueryHandler(product_selected, pattern='^finish_cart$'),
            CallbackQueryHandler(product_selected, pattern='^cancel$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ],
        CONFIRMING_CART: [
            CallbackQueryHandler(confirm_order, pattern='^confirm_order$'),
            CallbackQueryHandler(add_more, pattern='^add_more$'),
            CallbackQueryHandler(cancel_all, pattern='^cancel_all$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_all)],
    allow_reentry=True
)
