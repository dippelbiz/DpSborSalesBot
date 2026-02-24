#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

# Состояния разговора
SELECTING_ORDER, CONFIRMING_SHIPMENT = range(2)

async def admin_orders_start(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'shipped'")
        shipped_count = cursor.fetchone()[0]
        cursor.execute("""
            SELECT COUNT(*) FROM orders
            WHERE status = 'completed' AND date(completed_at) = date('now')
        """)
        completed_today = cursor.fetchone()[0]

    keyboard = [
        [InlineKeyboardButton(f"🟡 Новые заявки ({new_count})", callback_data="admin_orders_new")],
        [InlineKeyboardButton(f"🔵 В пути ({shipped_count})", callback_data="admin_orders_shipped")],
        [InlineKeyboardButton("📋 Все заявки", callback_data="admin_orders_all")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_orders_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📦 Управление поставками\n\n"
        f"🟡 Новых: {new_count}\n"
        f"🔵 В пути: {shipped_count}\n"
        f"🟢 Завершено сегодня: {completed_today}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return SELECTING_ORDER

async def admin_orders_new(update: Update, context):
    query = update.callback_query
    await query.answer()

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.id, o.order_number, o.seller_code, o.created_at,
                   GROUP_CONCAT(p.product_name || ' ' || oi.quantity_ordered || ' упак') as items,
                   SUM(oi.quantity_ordered * oi.price_at_order) as total
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            JOIN products p ON oi.product_id = p.id
            WHERE o.status = 'new'
            GROUP BY o.id
            ORDER BY o.created_at ASC
        """)
        orders = cursor.fetchall()

    if not orders:
        await query.edit_message_text(
            "📭 Нет новых заявок",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="admin_orders_back_to_menu")
            ]])
        )
        return SELECTING_ORDER

    text = "🟡 Новые заявки:\n\n"
    keyboard = []
    for order in orders:
        text += f"📋 {order['order_number']} ({order['seller_code']})\n"
        text += f"   {order['items']}\n"
        text += f"   Сумма: {order['total']} руб\n"
        text += f"   от {order['created_at'][:16]}\n\n"
        keyboard.append([InlineKeyboardButton(
            f"✅ {order['order_number']}",
            callback_data=f"admin_order_view_{order['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_orders_back_to_menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_ORDER

async def admin_order_view(update: Update, context):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace('admin_order_view_', ''))
    context.user_data['current_order_id'] = order_id

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT o.*, s.full_name, s.telegram_id
            FROM orders o
            JOIN sellers s ON o.seller_id = s.id
            WHERE o.id = ?
        """, (order_id,))
        order = cursor.fetchone()

        cursor.execute("""
            SELECT p.product_name, oi.quantity_ordered, oi.price_at_order,
                   oi.quantity_ordered * oi.price_at_order as total
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = ?
        """, (order_id,))
        items = cursor.fetchall()

    status_emoji = {
        'new': '🟡',
        'shipped': '🔵',
        'completed': '🟢',
        'cancelled': '⚫'
    }.get(order['status'], '⚪')

    text = f"{status_emoji} Заявка: {order['order_number']}\n"
    text += f"Продавец: {order['seller_code']} - {order['full_name']}\n"
    text += f"Дата: {order['created_at'][:16]}\n"
    text += f"Статус: {order['status']}\n\n"
    text += "Товары:\n"
    for item in items:
        text += f"• {item['product_name']}: {item['quantity_ordered']} упак × {item['price_at_order']} = {item['total']} руб\n"

    keyboard = []
    if order['status'] == 'new':
        keyboard.append([InlineKeyboardButton("✅ Подтвердить отгрузку", callback_data=f"admin_order_ship_{order_id}")])
        keyboard.append([InlineKeyboardButton("❌ Отменить заявку", callback_data=f"admin_order_cancel_{order_id}")])
    elif order['status'] == 'shipped':
        keyboard.append([InlineKeyboardButton("📦 Отметить как получено", callback_data=f"admin_order_complete_{order_id}")])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_orders_back_to_new")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING_ORDER

@send_backup_to_admin("подтверждение отгрузки")
async def admin_order_ship(update: Update, context):
    """Подтверждение заявки: для Р – пополнение склада, для других – перераспределение"""
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.replace('admin_order_ship_', ''))

    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем данные заявки
        cursor.execute("""
            SELECT o.seller_id, o.seller_code, o.order_number,
                   oi.product_id, oi.quantity_ordered, oi.price_at_order
            FROM orders o
            JOIN order_items oi ON o.id = oi.order_id
            WHERE o.id = ?
        """, (order_id,))
        rows = cursor.fetchall()
        if not rows:
            await query.edit_message_text("❌ Заявка не найдена")
            return

        seller_id = rows[0]['seller_id']
        seller_code = rows[0]['seller_code']
        order_number = rows[0]['order_number']

        # Получаем ID продавца Р (центральный склад)
        cursor.execute("SELECT id FROM sellers WHERE seller_code = 'Р'")
        res = cursor.fetchone()
        if not res:
            await query.edit_message_text("❌ Продавец Р (центральный склад) не найден в БД.")
            return
        central_seller_id = res['id']

        # Если заявка от самого Р – это пополнение его склада
        if seller_id == central_seller_id:
            # Просто добавляем товар на склад Р, увеличиваем его долг
            for row in rows:
                product_id = row['product_id']
                qty = row['quantity_ordered']
                price = row['price_at_order']
                # Добавляем на склад Р
                cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (central_seller_id, product_id))
                existing = cursor.fetchone()
                if existing:
                    cursor.execute("UPDATE seller_products SET quantity = quantity + ? WHERE seller_id = ? AND product_id = ?", (qty, central_seller_id, product_id))
                else:
                    cursor.execute("INSERT INTO seller_products (seller_id, product_id, quantity) VALUES (?, ?, ?)", (central_seller_id, product_id, qty))
                # Увеличиваем долг Р
                cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (central_seller_id,))
                debt = cursor.fetchone()
                if debt:
                    cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (price * qty, central_seller_id))
                else:
                    cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (central_seller_id, price * qty))
            # Обновляем статус заявки
            cursor.execute("UPDATE orders SET status = 'shipped', shipped_at = CURRENT_TIMESTAMP WHERE id = ?", (order_id,))
            await query.edit_message_text(
                f"✅ Заявка на пополнение склада Р подтверждена! Товар добавлен на склад Р.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 К заявкам", callback_data="admin_orders_back_to_menu")
                ]])
            )
            # Уведомляем продавца Р (если есть telegram_id)
            cursor.execute("SELECT telegram_id FROM sellers WHERE id = ?", (central_seller_id,))
            res_tg = cursor.fetchone()
            if res_tg and res_tg['telegram_id']:
                try:
                    await context.bot.send_message(
                        chat_id=res_tg['telegram_id'],
                        text=f"✅ Ваш склад пополнен!\n\nЗаявка №{order_number} подтверждена. Товар добавлен."
                    )
                except Exception as e:
                    logger.error(f"Не удалось уведомить продавца Р: {e}")
            return

        # Иначе заявка от другого продавца – списываем со склада Р
        # Проверяем наличие всех товаров на складе Р
        for row in rows:
            product_id = row['product_id']
            qty = row['quantity_ordered']
            cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (central_seller_id, product_id))
            stock_row = cursor.fetchone()
            if not stock_row or stock_row['quantity'] < qty:
                await query.edit_message_text(
                    f"❌ Недостаточно товара на центральном складе (продавца Р) для продукта {row['product_id']}.\n"
                    f"Доступно: {stock_row['quantity'] if stock_row else 0}, требуется: {qty}"
                )
                return

        # Списываем со склада Р и добавляем на склад заказчика
        for row in rows:
            product_id = row['product_id']
            qty = row['quantity_ordered']
            price = row['price_at_order']

            # Списываем со склада Р
            cursor.execute("UPDATE seller_products SET quantity = quantity - ? WHERE seller_id = ? AND product_id = ?", (qty, central_seller_id, product_id))

            # Добавляем на склад заказчика
            cursor.execute("SELECT quantity FROM seller_products WHERE seller_id = ? AND product_id = ?", (seller_id, product_id))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE seller_products SET quantity = quantity + ? WHERE seller_id = ? AND product_id = ?", (qty, seller_id, product_id))
            else:
                cursor.execute("INSERT INTO seller_products (seller_id, product_id, quantity) VALUES (?, ?, ?)", (seller_id, product_id, qty))

            # Увеличиваем долг заказчика
            cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (seller_id,))
            debt = cursor.fetchone()
            if debt:
                cursor.execute("UPDATE seller_debt SET total_debt = total_debt + ? WHERE seller_id = ?", (price * qty, seller_id))
            else:
                cursor.execute("INSERT INTO seller_debt (seller_id, total_debt) VALUES (?, ?)", (seller_id, price * qty))

        # Обновляем статус заявки
        cursor.execute("UPDATE orders SET status = 'shipped', shipped_at = CURRENT_TIMESTAMP WHERE id = ?", (order_id,))

        # Уведомляем заказчика
        cursor.execute("SELECT telegram_id FROM sellers WHERE id = ?", (seller_id,))
        res_tg = cursor.fetchone()
        if res_tg and res_tg['telegram_id']:
            try:
                await context.bot.send_message(
                    chat_id=res_tg['telegram_id'],
                    text=f"🚚 Ваша заявка №{order_number} переведена в статус «В пути»."
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить продавца {seller_id}: {e}")

    await query.edit_message_text(
        f"✅ Отгрузка подтверждена! Товар списан со склада Р и добавлен продавцу {seller_code}.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К заявкам", callback_data="admin_orders_back_to_menu")
        ]])
    )

async def admin_orders_back_to_menu(update: Update, context):
    query = update.callback_query
    await query.answer()
    # Возврат в главное меню поставок
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'")
        new_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'shipped'")
        shipped_count = cursor.fetchone()[0]
        cursor.execute("""
            SELECT COUNT(*) FROM orders
            WHERE status = 'completed' AND date(completed_at) = date('now')
        """)
        completed_today = cursor.fetchone()[0]

    keyboard = [
        [InlineKeyboardButton(f"🟡 Новые заявки ({new_count})", callback_data="admin_orders_new")],
        [InlineKeyboardButton(f"🔵 В пути ({shipped_count})", callback_data="admin_orders_shipped")],
        [InlineKeyboardButton("📋 Все заявки", callback_data="admin_orders_all")],
        [InlineKeyboardButton("🔙 В админ-меню", callback_data="admin_orders_exit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📦 Управление поставками\n\n"
        f"🟡 Новых: {new_count}\n"
        f"🔵 В пути: {shipped_count}\n"
        f"🟢 Завершено сегодня: {completed_today}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return SELECTING_ORDER

async def admin_orders_exit(update: Update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Выход в главное меню", reply_markup=get_admin_menu())
    return ConversationHandler.END

admin_orders_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📦 Управление поставками$'), admin_orders_start)],
    states={
        SELECTING_ORDER: [
            CallbackQueryHandler(admin_orders_new, pattern='^admin_orders_new$'),
            CallbackQueryHandler(admin_orders_back_to_menu, pattern='^admin_orders_back_to_menu$'),
            CallbackQueryHandler(admin_orders_exit, pattern='^admin_orders_exit$'),
            CallbackQueryHandler(admin_order_view, pattern='^admin_order_view_'),
            CallbackQueryHandler(admin_order_ship, pattern='^admin_order_ship_'),
        ]
    },
    fallbacks=[CommandHandler('cancel', admin_orders_exit)],
    allow_reentry=True
)
