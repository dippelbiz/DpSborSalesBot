#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

# Состояния разговора
MAIN_MENU, PERIOD_REPORT, SELLER_STOCK = range(3)

async def reports_start(update: Update, context):
    """Главное меню отчетов"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("👥 По всем продавцам", callback_data="report_all_sellers")],
        [InlineKeyboardButton("💰 По продажам", callback_data="report_sales")],
        [InlineKeyboardButton("💳 По платежам", callback_data="report_payments")],
        [InlineKeyboardButton("📦 Остатки по складам", callback_data="report_stock")],
        [InlineKeyboardButton("🔙 Назад", callback_data="report_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📊 Отчеты\n\n"
        "Выберите тип отчета:",
        reply_markup=reply_markup
    )
    
    return MAIN_MENU

# ==== ОТЧЕТ ПО ВСЕМ ПРОДАВЦАМ (остаётся без изменений) ====
async def report_all_sellers(update: Update, context):
    """Сводка по всем продавцам"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                s.id,
                s.seller_code,
                s.full_name,
                s.is_active,
                COALESCE(sd.total_debt, 0) as total_debt,
                COALESCE(sp.pending_amount, 0) as pending_amount,
                (SELECT COUNT(*) FROM orders WHERE seller_id = s.id AND status = 'new') as new_orders,
                (SELECT COUNT(*) FROM orders WHERE seller_id = s.id AND status = 'shipped') as shipped_orders,
                (SELECT COUNT(*) FROM orders WHERE seller_id = s.id AND status = 'completed') as completed_orders
            FROM sellers s
            LEFT JOIN seller_debt sd ON s.id = sd.seller_id
            LEFT JOIN seller_pending sp ON s.id = sp.seller_id
            ORDER BY s.seller_code
        """)
        sellers = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_sellers,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_sellers,
                SUM(COALESCE(sd.total_debt, 0)) as total_debt_sum,
                SUM(COALESCE(sp.pending_amount, 0)) as total_pending_sum
            FROM sellers s
            LEFT JOIN seller_debt sd ON s.id = sd.seller_id
            LEFT JOIN seller_pending sp ON s.id = sp.seller_id
        """)
        totals = cursor.fetchone()
    
    if not sellers:
        await query.edit_message_text(
            "📭 Нет зарегистрированных продавцов",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")
            ]])
        )
        return MAIN_MENU
    
    text = "📊 **Отчет по всем продавцам**\n\n"
    text += f"👥 Всего продавцов: {totals['total_sellers']} (активных: {totals['active_sellers']})\n"
    text += f"💰 Общий долг за товар: {totals['total_debt_sum']} руб\n"
    text += f"💵 Общая сумма к переводу: {totals['total_pending_sum']} руб\n\n"
    
    text += "**Детализация:**\n"
    for seller in sellers:
        status = "🟢" if seller['is_active'] else "🔴"
        text += f"{status} {seller['seller_code']} - {seller['full_name']}\n"
        text += f"   Долг: {seller['total_debt']} руб, к переводу: {seller['pending_amount']} руб\n"
        text += f"   Заявки: 🟡{seller['new_orders']} 🔵{seller['shipped_orders']} 🟢{seller['completed_orders']}\n\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return MAIN_MENU

# ==== ОТЧЕТ ПО ПРОДАЖАМ (остаётся без изменений) ====
async def report_sales(update: Update, context):
    """Меню выбора периода для отчета по продажам"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📅 Сегодня", callback_data="sales_today")],
        [InlineKeyboardButton("📅 Вчера", callback_data="sales_yesterday")],
        [InlineKeyboardButton("📅 Эта неделя", callback_data="sales_week")],
        [InlineKeyboardButton("📅 Этот месяц", callback_data="sales_month")],
        [InlineKeyboardButton("📅 Все время", callback_data="sales_all")],
        [InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "💰 Отчет по продажам\n\n"
        "Выберите период:",
        reply_markup=reply_markup
    )
    return PERIOD_REPORT

async def sales_period(update: Update, context):
    """Показать отчет по продажам за выбранный период"""
    query = update.callback_query
    await query.answer()
    
    period = query.data.replace('sales_', '')
    
    today = datetime.now().date()
    if period == 'today':
        start_date = today
        end_date = today + timedelta(days=1)
        period_name = "сегодня"
    elif period == 'yesterday':
        start_date = today - timedelta(days=1)
        end_date = today
        period_name = "вчера"
    elif period == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = today + timedelta(days=1)
        period_name = "эту неделю"
    elif period == 'month':
        start_date = today.replace(day=1)
        if today.month == 12:
            end_date = today.replace(year=today.year+1, month=1, day=1)
        else:
            end_date = today.replace(month=today.month+1, day=1)
        period_name = "этот месяц"
    else:  # all
        start_date = datetime(2000, 1, 1).date()
        end_date = today + timedelta(days=1)
        period_name = "все время"
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total_sales,
                COALESCE(SUM(quantity), 0) as total_quantity,
                COALESCE(SUM(amount), 0) as total_amount
            FROM sales
            WHERE date(created_at) >= ? AND date(created_at) < ?
        """, (start_date, end_date))
        totals = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                s.seller_code,
                s.full_name,
                COUNT(*) as sales_count,
                COALESCE(SUM(sa.quantity), 0) as total_quantity,
                COALESCE(SUM(sa.amount), 0) as total_amount
            FROM sales sa
            JOIN sellers s ON sa.seller_id = s.id
            WHERE date(sa.created_at) >= ? AND date(sa.created_at) < ?
            GROUP BY s.id
            ORDER BY total_amount DESC
        """, (start_date, end_date))
        sellers_sales = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                p.product_name,
                COUNT(*) as sales_count,
                COALESCE(SUM(sa.quantity), 0) as total_quantity,
                COALESCE(SUM(sa.amount), 0) as total_amount
            FROM sales sa
            JOIN products p ON sa.product_id = p.id
            WHERE date(sa.created_at) >= ? AND date(sa.created_at) < ?
            GROUP BY p.id
            ORDER BY total_amount DESC
        """, (start_date, end_date))
        products_sales = cursor.fetchall()
    
    text = f"💰 **Отчет по продажам за {period_name}**\n\n"
    text += f"📊 Всего продаж: {totals['total_sales']}\n"
    text += f"📦 Продано упаковок: {totals['total_quantity']}\n"
    text += f"💵 Сумма: {totals['total_amount']} руб\n\n"
    
    if sellers_sales:
        text += "**По продавцам:**\n"
        for s in sellers_sales:
            text += f"• {s['seller_code']} - {s['full_name']}: {s['total_amount']} руб ({s['total_quantity']} упак)\n"
        text += "\n"
    
    if products_sales:
        text += "**По товарам:**\n"
        for p in products_sales:
            text += f"• {p['product_name']}: {p['total_amount']} руб ({p['total_quantity']} упак)\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return MAIN_MENU

# ==== ОТЧЕТ ПО ПЛАТЕЖАМ (остаётся без изменений) ====
async def report_payments(update: Update, context):
    """Отчет по платежам"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved_count,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected_count,
                COALESCE(SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END), 0) as total_approved_amount
            FROM payment_requests
        """)
        stats = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                pr.request_number,
                pr.amount,
                pr.status,
                pr.created_at,
                pr.approved_at,
                s.seller_code,
                s.full_name
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            ORDER BY pr.created_at DESC
            LIMIT 10
        """)
        recent = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                s.seller_code,
                s.full_name,
                COUNT(pr.id) as requests_count,
                COALESCE(SUM(pr.amount), 0) as total_amount
            FROM sellers s
            LEFT JOIN payment_requests pr ON s.id = pr.seller_id AND pr.status = 'approved'
            GROUP BY s.id
            HAVING requests_count > 0
            ORDER BY total_amount DESC
        """)
        sellers_payments = cursor.fetchall()
    
    text = "💳 **Отчет по платежам**\n\n"
    text += f"📊 Всего запросов: {stats['total_requests']}\n"
    text += f"🟡 Ожидает: {stats['pending_count']}\n"
    text += f"✅ Подтверждено: {stats['approved_count']}\n"
    text += f"❌ Отклонено: {stats['rejected_count']}\n"
    text += f"💵 Выплачено всего: {stats['total_approved_amount']} руб\n\n"
    
    if sellers_payments:
        text += "**Топ продавцов по выплатам:**\n"
        for s in sellers_payments:
            text += f"• {s['seller_code']} - {s['full_name']}: {s['total_amount']} руб ({s['requests_count']} платежей)\n"
        text += "\n"
    
    if recent:
        text += "**Последние платежи:**\n"
        for r in recent:
            status_emoji = '🟡' if r['status'] == 'pending' else '✅' if r['status'] == 'approved' else '❌'
            date_str = r['approved_at'][:16] if r['approved_at'] else r['created_at'][:16]
            text += f"{status_emoji} {r['request_number']} - {r['seller_code']}: {r['amount']} руб ({date_str})\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return MAIN_MENU

# ==== НОВЫЙ ОТЧЕТ: ОСТАТКИ ПО СКЛАДАМ ====
async def report_stock(update: Update, context):
    """Меню выбора: список продавцов или общий остаток"""
    query = update.callback_query
    await query.answer()
    
    # Получаем список всех активных продавцов
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, seller_code, full_name
            FROM sellers
            WHERE is_active = 1
            ORDER BY seller_code
        """)
        sellers = cursor.fetchall()
    
    text = "📦 **Остатки по складам**\n\nВыберите продавца для просмотра его остатков:"
    keyboard = []
    
    for s in sellers:
        keyboard.append([InlineKeyboardButton(
            f"{s['seller_code']} - {s['full_name']}",
            callback_data=f"seller_stock_{s['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("📊 Всего остатков по складам", callback_data="total_stock")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="report_back_to_menu")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return SELLER_STOCK

async def seller_stock(update: Update, context):
    """Показать остатки конкретного продавца"""
    query = update.callback_query
    await query.answer()
    
    seller_id = int(query.data.replace('seller_stock_', ''))
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем информацию о продавце
        cursor.execute("SELECT seller_code, full_name FROM sellers WHERE id = ?", (seller_id,))
        seller = cursor.fetchone()
        if not seller:
            await query.edit_message_text("❌ Продавец не найден")
            return SELLER_STOCK
        
        # Получаем остатки товаров для этого продавца
        cursor.execute("""
            SELECT p.product_name, sp.quantity
            FROM seller_products sp
            JOIN products p ON sp.product_id = p.id
            WHERE sp.seller_id = ? AND p.is_active = 1
            ORDER BY p.product_name
        """, (seller_id,))
        products = cursor.fetchall()
    
    text = f"📦 **Остатки продавца {seller['seller_code']}**\n\n"
    if products:
        for p in products:
            text += f"• {p['product_name']} — {p['quantity']} упак\n"
    else:
        text += "У продавца нет товаров на складе.\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="report_stock")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return SELLER_STOCK

async def total_stock(update: Update, context):
    """Показать общие остатки по всем продавцам (суммарно по каждому товару)"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT p.product_name, COALESCE(SUM(sp.quantity), 0) as total_quantity
            FROM products p
            LEFT JOIN seller_products sp ON p.id = sp.product_id
            WHERE p.is_active = 1
            GROUP BY p.id
            ORDER BY p.product_name
        """)
        totals = cursor.fetchall()
    
    text = "📊 **Общие остатки по всем складам**\n\n"
    for t in totals:
        text += f"• {t['product_name']} — {t['total_quantity']} упак\n"
    
    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="report_stock")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return SELLER_STOCK

# ==== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====
async def back_to_main_menu(update: Update, context):
    """Возврат в главное меню отчетов"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("👥 По всем продавцам", callback_data="report_all_sellers")],
        [InlineKeyboardButton("💰 По продажам", callback_data="report_sales")],
        [InlineKeyboardButton("💳 По платежам", callback_data="report_payments")],
        [InlineKeyboardButton("📦 Остатки по складам", callback_data="report_stock")],
        [InlineKeyboardButton("🔙 Назад", callback_data="report_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📊 Отчеты\n\n"
        "Выберите тип отчета:",
        reply_markup=reply_markup
    )
    return MAIN_MENU

async def exit_reports(update: Update, context):
    """Выход в главное админское меню"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Выход в главное меню",
        reply_markup=get_admin_menu()
    )
    
    return ConversationHandler.END

# Обработчик разговора для отчетов
admin_reports_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^📊 Отчеты$'), reports_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(report_all_sellers, pattern='^report_all_sellers$'),
            CallbackQueryHandler(report_sales, pattern='^report_sales$'),
            CallbackQueryHandler(report_payments, pattern='^report_payments$'),
            CallbackQueryHandler(report_stock, pattern='^report_stock$'),
            CallbackQueryHandler(back_to_main_menu, pattern='^report_back_to_menu$'),
            CallbackQueryHandler(exit_reports, pattern='^report_back$')
        ],
        PERIOD_REPORT: [
            CallbackQueryHandler(sales_period, pattern='^sales_'),
            CallbackQueryHandler(back_to_main_menu, pattern='^report_back_to_menu$')
        ],
        SELLER_STOCK: [
            CallbackQueryHandler(seller_stock, pattern='^seller_stock_'),
            CallbackQueryHandler(total_stock, pattern='^total_stock$'),
            CallbackQueryHandler(report_stock, pattern='^report_stock$'),
            CallbackQueryHandler(back_to_main_menu, pattern='^report_back_to_menu$')
        ]
    },
    fallbacks=[CommandHandler('cancel', exit_reports)],
    allow_reentry=True
)
