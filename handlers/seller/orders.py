#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для заявок на поставку (продавец)
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from database import db
from keyboards import get_back_and_cancel_keyboard, get_main_menu, get_confirm_keyboard
from backup_decorator import send_backup_to_admin

# Состояния разговора
SELECTING_PRODUCT, ENTERING_QUANTITY, CONFIRMING = range(3)

async def orders_start(update: Update, context):
    """Начало создания заявки"""
    await update.message.reply_text(
        "📦 Создание новой заявки на поставку\n\n"
        "Выберите товар:",
        reply_markup=await get_products_keyboard()
    )
    return SELECTING_PRODUCT

async def get_products_keyboard():
    """Получение клавиатуры с товарами"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, product_name, price FROM products WHERE is_active = 1")
        products = cursor.fetchall()
    
    # Создаем инлайн-клавиатуру
    keyboard = []
    row = []
    for i, product in enumerate(products):
        button = InlineKeyboardButton(
            f"{product['product_name']} ({product['price']} руб)", 
            callback_data=f"product_{product['id']}"
        )
        row.append(button)
        if (i + 1) % 2 == 0:  # По 2 в ряд
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    # Добавляем кнопку отмены
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])
    
    return InlineKeyboardMarkup(keyboard)

async def product_selected(update: Update, context):
    """Обработка выбора товара"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text(
            "❌ Создание заявки отменено",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END
    
    product_id = int(query.data.replace('product_', ''))
    context.user_data['selected_product_id'] = product_id
    
    # Получаем информацию о товаре
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT product_name, price FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
    
    context.user_data['selected_product'] = product['product_name']
    context.user_data['product_price'] = product['price']
    
    await query.edit_message_text(
        f"Товар: {product['product_name']}\n"
        f"Цена: {product['price']} руб/упак\n\n"
        f"Введите количество упаковок (только целое число):",
        reply_markup=get_back_and_cancel_keyboard()
    )
    
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка ввода количества"""
    text = update.message.text
    
    if text == '🔙 Назад':
        await orders_start(update, context)
        return SELECTING_PRODUCT
    
    if text == '❌ Отмена':
        await update.message.reply_text(
            "❌ Создание заявки отменено",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END
    
    # Проверяем, что введено число
    try:
        quantity = int(text)
        if quantity <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое положительное число\n"
            "Например: 5 или 10",
            reply_markup=get_back_and_cancel_keyboard()
        )
        return ENTERING_QUANTITY
    
    context.user_data['quantity'] = quantity
    
    product_name = context.user_data['selected_product']
    price = context.user_data['product_price']
    total = quantity * price
    
    await update.message.reply_text(
        f"Проверьте заявку:\n\n"
        f"Товар: {product_name}\n"
        f"Количество: {quantity} упак\n"
        f"Цена: {price} руб/упак\n"
        f"Сумма: {total} руб\n\n"
        f"Всё верно?",
        reply_markup=get_confirm_keyboard()
    )
    
    return CONFIRMING

@send_backup_to_admin("создание заявки на поставку")
async def confirm_order(update: Update, context):
    """Подтверждение создания заявки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm':
        # Создаем заявку в БД
        seller_id = 1  # Здесь нужно получить ID продавца
        seller_code = "А"  # Здесь нужно получить код продавца
        product_id = context.user_data['selected_product_id']
        quantity = context.user_data['quantity']
        price = context.user_data['product_price']
        
        # Генерируем номер заявки
        from datetime import datetime
        date_str = datetime.now().strftime("%d%m")
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем количество заявок продавца за сегодня
            cursor.execute("""
                SELECT COUNT(*) FROM orders 
                WHERE seller_code = ? AND date(created_at) = date('now')
            """, (seller_code,))
            count = cursor.fetchone()[0] + 1
            
            order_number = f"{seller_code}-{date_str}-{count:03d}"
            
            # Создаем заявку
            cursor.execute("""
                INSERT INTO orders (order_number, seller_id, seller_code, status)
                VALUES (?, ?, ?, 'new')
            """, (order_number, seller_id, seller_code))
            
            order_id = cursor.lastrowid
            
            # Добавляем товар в заявку
            cursor.execute("""
                INSERT INTO order_items (order_id, product_id, quantity_ordered, price_at_order)
                VALUES (?, ?, ?, ?)
            """, (order_id, product_id, quantity, price))
        
        await query.edit_message_text(
            f"✅ Заявка создана!\n\n"
            f"Номер: {order_number}\n"
            f"Товар: {context.user_data['selected_product']}\n"
            f"Количество: {quantity} упак\n"
            f"Статус: Новая",
            reply_markup=get_main_menu()
        )
        
        # Очищаем данные
        context.user_data.clear()
        
        return ConversationHandler.END
    
    elif query.data == 'edit':
        await query.edit_message_text(
            f"Товар: {context.user_data['selected_product']}\n"
            f"Введите новое количество:",
            reply_markup=get_back_and_cancel_keyboard()
        )
        return ENTERING_QUANTITY
    
    else:  # cancel
        await query.edit_message_text(
            "❌ Создание заявки отменено",
            reply_markup=get_main_menu()
        )
        context.user_data.clear()
        return ConversationHandler.END

async def my_orders(update: Update, context):
    """Просмотр своих заявок"""
    seller_id = 1  # Здесь нужно получить ID продавца
    
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
            "У вас пока нет заявок",
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

# Обработчик разговора для заявок
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
        CONFIRMING: [
            CallbackQueryHandler(confirm_order, pattern='^(confirm|edit|cancel)$')
        ]
    },
    fallbacks=[CommandHandler('cancel', orders_start)],
    allow_reentry=True  # ← ВАЖНО: добавляем эту строку

)
