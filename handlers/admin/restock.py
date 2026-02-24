#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для раздела "Пополнение склада" (админ).
Показывает срочные заявки и все товары, позволяет пополнить любой товар,
ведёт архив пополнений.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu, get_back_keyboard
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

MAIN_MENU, ENTERING_QUANTITY, CONFIRMING = range(3)

async def restock_admin_start(update: Update, context):
    """Главное меню – показывает срочные заявки и список всех товаров с кнопками."""
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await update.message.reply_text("❌ Ошибка: центральный склад не найден.")
            return ConversationHandler.END
        central_id = central['id']

        # Получаем все товары с остатками на складе Р и количеством в pending-заявках
        cursor.execute("""
            SELECT 
                p.id,
                p.product_name,
                COALESCE(sp.quantity, 0) as current_stock,
                COALESCE((
                    SELECT SUM(ri.quantity_requested)
                    FROM restock_items ri
                    JOIN restock_requests rr ON ri.request_id = rr.id
                    WHERE ri.product_id = p.id AND rr.status = 'pending'
                ), 0) as pending_requests
            FROM products p
            LEFT JOIN seller_products sp ON sp.product_id = p.id AND sp.seller_id = ?
            WHERE p.is_active = 1
            ORDER BY p.product_name
        """, (central_id,))
        products = cursor.fetchall()

    if not products:
        await update.message.reply_text("📭 Нет товаров.")
        return MAIN_MENU

    # Блок срочных заявок
    urgent_lines = [f"{p['product_name']} – {p['pending_requests']} упак" for p in products if p['pending_requests'] > 0]
    urgent_text = "**Срочные заявки:**\n" + "\n".join(urgent_lines) if urgent_lines else "✅ Срочные заявки отсутствуют."

    # Блок всех товаров (только название и остаток, без цены)
    product_lines = [f"**{p['product_name']}** – остаток: {p['current_stock']} упак" for p in products]
    products_text = "\n".join(product_lines)

    text = f"🆘 **Пополнение склада Р**\n\n{urgent_text}\n\n**Все товары:**\n{products_text}"

    # Клавиатура – кнопки для каждого товара
    keyboard = [[InlineKeyboardButton(f"✏️ {p['product_name']}", callback_data=f"restock_item_{p['id']}")] for p in products]
    keyboard.append([InlineKeyboardButton("📜 Архив пополнений", callback_data="restock_history")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="restock_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

    return MAIN_MENU

async def select_item(update: Update, context):
    """Админ выбрал товар – запрашиваем количество."""
    query = update.callback_query
    await query.answer()
    logger.info(f"select_item called with data: {query.data}")

    if not query.data.startswith('restock_item_'):
        return MAIN_MENU

    product_id = int(query.data.replace('restock_item_', ''))
    context.user_data['current_product_id'] = product_id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
        prod = cursor.fetchone()
        if not prod:
            await query.edit_message_text("❌ Товар не найден.")
            return MAIN_MENU
        context.user_data['product_name'] = prod['product_name']
        # Цена нам не нужна для отображения, но может понадобиться для расчёта долга. 
        # Получим её отдельно позже, если нужно.

    # Убираем инлайн-клавиатуру из текущего сообщения
    await query.edit_message_text(query.message.text, reply_markup=None)

    # Отправляем новое сообщение с запросом количества
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text=f"Товар: **{prod['product_name']}**\n\n"
             f"Введите количество упаковок для пополнения (целое положительное число):",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def quantity_entered(update: Update, context):
    """Обработка введённого количества, переход к подтверждению."""
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

    context.user_data['quantity'] = qty

    # Получаем цену товара для расчёта долга (понадобится при подтверждении)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT price FROM products WHERE id = ?", (context.user_data['current_product_id'],))
        price_row = cursor.fetchone()
        context.user_data['product_price'] = price_row['price'] if price_row else 0

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_restock")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="change_qty")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_restock")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"Товар: {context.user_data['product_name']}\n"
        f"Количество: {qty} упак\n\n"
        f"Подтвердить пополнение?",
        reply_markup=reply_markup
    )
    return CONFIRMING

@send_backup_to_admin("пополнение склада")
async def confirm_restock(update: Update, context):
    """Подтверждение пополнения – обновляем склад Р, долг, заявки и историю."""
    query = update.callback_query
    await query.answer()
    logger.info("confirm_restock called")

    product_id = context.user_data['current_product_id']
    product_name = context.user_data['product_name']
    price = context.user_data.get('product_price', 0)
    qty = context.user_data['quantity']

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем ID продавца Р
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        central = cursor.fetchone()
        if not central:
            await query.edit_message_text("❌ Ошибка: центральный склад не найден.")
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
            cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (price * qty, central_id))
        else:
            cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (central_id, price * qty))

        # Распределяем по pending-заявкам
        cursor.execute("""
            SELECT ri.id, ri.quantity_requested, rr.id as request_id
            FROM restock_items ri
            JOIN restock_requests rr ON ri.request_id = rr.id
            WHERE ri.product_id = ? AND rr.status = 'pending'
            ORDER BY rr.created_at ASC
        """, (product_id,))
        items = cursor.fetchall()

        remaining = qty
        for item in items:
            if remaining <= 0:
                break
            take = min(item['quantity_requested'], remaining)
            cursor.execute("UPDATE restock_items SET quantity_received = COALESCE(quantity_received, 0) + ? WHERE id = ?", (take, item['id']))
            remaining -= take

        # Закрываем полностью выполненные заявки
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

        # Записываем в историю пополнений
        cursor.execute("""
            INSERT INTO restock_history (product_id, quantity) VALUES (?, ?)
        """, (product_id, qty))

    await query.edit_message_text(
        f"✅ Пополнение выполнено!\n\n"
        f"Товар: {product_name}\n"
        f"Количество: {qty} упак\n"
        f"Добавлено на склад Р.",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Вернуться к списку товаров?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К списку", callback_data="restock_back_to_list")
        ]])
    )
    context.user_data.clear()
    return MAIN_MENU

async def change_qty(update: Update, context):
    """Изменить количество – возвращаемся к вводу."""
    query = update.callback_query
    await query.answer()
    logger.info("change_qty called")

    await query.edit_message_text(
        f"Товар: {context.user_data['product_name']}\n\n"
        f"Введите новое количество:",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите число:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_QUANTITY

async def cancel_restock(update: Update, context):
    """Отмена пополнения."""
    query = update.callback_query
    await query.answer()
    logger.info("cancel_restock called")

    await query.edit_message_text("❌ Пополнение отменено.", reply_markup=None)
    await restock_admin_start(update, context)
    return MAIN_MENU

async def restock_history(update: Update, context):
    """Показать историю пополнений (последние 20 записей)."""
    query = update.callback_query
    await query.answer()

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.product_name, rh.quantity, rh.created_at
            FROM restock_history rh
            JOIN products p ON rh.product_id = p.id
            ORDER BY rh.created_at DESC
            LIMIT 20
        """)
        history = cursor.fetchall()

    if not history:
        text = "📭 История пополнений пуста."
    else:
        text = "📜 **История пополнений склада Р**\n\n"
        for h in history:
            date = h['created_at'][:16]  # ГГГГ-ММ-ДД ЧЧ:ММ
            text += f"• {date} – {h['product_name']}: {h['quantity']} упак\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="restock_back_to_list")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
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
            CallbackQueryHandler(restock_history, pattern='^restock_history$'),
            CallbackQueryHandler(back_to_admin, pattern='^restock_back$'),
            CallbackQueryHandler(back_to_list, pattern='^restock_back_to_list$')
        ],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, quantity_entered)
        ],
        CONFIRMING: [
            CallbackQueryHandler(confirm_restock, pattern='^confirm_restock$'),
            CallbackQueryHandler(change_qty, pattern='^change_qty$'),
            CallbackQueryHandler(cancel_restock, pattern='^cancel_restock$')
        ]
    },
    fallbacks=[CommandHandler('cancel', back_to_admin)],
    allow_reentry=True
)
