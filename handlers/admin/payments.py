#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчики для раздела "Управление платежами" (администратор)
Просмотр запросов на выплату, подтверждение/отклонение, уведомление продавцов.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin
import logging

logger = logging.getLogger(__name__)

# Состояния разговора
MAIN_MENU, VIEW_REQUEST, CONFIRM_PAYMENT = range(3)

async def admin_payments_start(update: Update, context):
    """Главное меню управления платежами"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    # Получаем статистику
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
        pending_count = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_requests WHERE status = 'pending'")
        pending_sum = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'approved' AND date(approved_at) = date('now')")
        approved_today = cursor.fetchone()[0]
    
    keyboard = [
        [InlineKeyboardButton(f"🟡 Ожидающие запросы ({pending_count})", callback_data="payments_pending")],
        [InlineKeyboardButton("📋 История платежей", callback_data="payments_history")],
        [InlineKeyboardButton("📊 Статистика", callback_data="payments_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="payments_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"💰 Управление платежами\n\n"
        f"🟡 Ожидают подтверждения: {pending_count}\n"
        f"💵 Сумма к выплате: {pending_sum} руб\n"
        f"✅ Подтверждено сегодня: {approved_today}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return MAIN_MENU

async def payments_pending(update: Update, context):
    """Показать список ожидающих запросов"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.id, pr.request_number, pr.amount, pr.created_at,
                   s.seller_code, s.full_name
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            WHERE pr.status = 'pending'
            ORDER BY pr.created_at ASC
        """)
        requests = cursor.fetchall()
    
    if not requests:
        await query.edit_message_text(
            "📭 Нет ожидающих запросов",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")
            ]])
        )
        return MAIN_MENU
    
    text = "🟡 Ожидающие запросы на выплату:\n\n"
    keyboard = []
    total_sum = 0
    for req in requests:
        text += f"📋 {req['request_number']}\n"
        text += f"   Продавец: {req['seller_code']} - {req['full_name'][:20]}\n"
        text += f"   Сумма: {req['amount']} руб\n"
        text += f"   от {req['created_at'][:16]}\n\n"
        total_sum += req['amount']
        keyboard.append([InlineKeyboardButton(
            f"✅ {req['request_number']} - {req['amount']} руб",
            callback_data=f"payment_view_{req['id']}"
        )])
    
    text += f"💵 Всего к выплате: {total_sum} руб\n\n"
    text += "Выберите запрос для обработки:"
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return VIEW_REQUEST

async def payment_view(update: Update, context):
    """Детальный просмотр конкретного запроса"""
    query = update.callback_query
    await query.answer()
    
    payment_id = int(query.data.replace('payment_view_', ''))
    context.user_data['current_payment_id'] = payment_id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.*, s.seller_code, s.full_name,
                   COALESCE(sd.total_debt, 0) as total_debt,
                   COALESCE(sp.pending_amount, 0) as pending_amount
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            LEFT JOIN seller_debt sd ON s.id = sd.seller_id
            LEFT JOIN seller_pending sp ON s.id = sp.seller_id
            WHERE pr.id = ?
        """, (payment_id,))
        payment = cursor.fetchone()
    
    if not payment:
        await query.edit_message_text("❌ Запрос не найден")
        return MAIN_MENU
    
    text = f"📋 Запрос на выплату\n\n"
    text += f"Номер: {payment['request_number']}\n"
    text += f"Продавец: {payment['seller_code']} - {payment['full_name']}\n"
    text += f"Сумма: {payment['amount']} руб\n"
    text += f"Дата запроса: {payment['created_at'][:16]}\n\n"
    text += f"💰 Текущее состояние продавца:\n"
    text += f"• Общий долг за товар: {payment['total_debt']} руб\n"
    text += f"• Сумма к переводу: {payment['pending_amount']} руб\n\n"
    text += f"После подтверждения:\n"
    text += f"• Долг уменьшится на {payment['amount']} руб\n"
    text += f"• Сумма к переводу уменьшится на {payment['amount']} руб"
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить выплату", callback_data="payment_confirm")],
        [InlineKeyboardButton("❌ Отклонить", callback_data="payment_reject")],
        [InlineKeyboardButton("🔙 Назад", callback_data="payments_pending")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    return CONFIRM_PAYMENT

@send_backup_to_admin("подтверждение выплаты")
async def payment_confirm(update: Update, context):
    """Подтверждение выплаты – обновление БД и уведомление продавца"""
    query = update.callback_query
    await query.answer()
    
    payment_id = context.user_data.get('current_payment_id')
    if not payment_id:
        await query.edit_message_text("❌ Ошибка: запрос не найден")
        return MAIN_MENU
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            # Получаем данные запроса
            cursor.execute("""
                SELECT pr.*, s.telegram_id, s.seller_code
                FROM payment_requests pr
                JOIN sellers s ON pr.seller_id = s.id
                WHERE pr.id = ?
            """, (payment_id,))
            payment = cursor.fetchone()
            if not payment:
                await query.edit_message_text("❌ Запрос не найден")
                return MAIN_MENU
            
            if payment['status'] != 'pending':
                await query.edit_message_text("❌ Запрос уже обработан")
                return MAIN_MENU
            
            # Обновляем статус запроса
            cursor.execute("""
                UPDATE payment_requests
                SET status = 'approved', approved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (payment_id,))
            
            # Уменьшаем сумму к переводу
            cursor.execute("""
                UPDATE seller_pending
                SET pending_amount = pending_amount - ?
                WHERE seller_id = ?
            """, (payment['amount'], payment['seller_id']))
            
            # Уменьшаем общий долг за товар
            cursor.execute("""
                UPDATE seller_debt
                SET total_debt = total_debt - ?
                WHERE seller_id = ?
            """, (payment['amount'], payment['seller_id']))
            
            # Получаем новые значения для уведомления
            cursor.execute("SELECT pending_amount FROM seller_pending WHERE seller_id = ?", (payment['seller_id'],))
            new_pending = cursor.fetchone()[0]
            cursor.execute("SELECT total_debt FROM seller_debt WHERE seller_id = ?", (payment['seller_id'],))
            new_debt = cursor.fetchone()[0]
        
        # Уведомляем админа (сообщение уже отредактировано)
        await query.edit_message_text(
            f"✅ Выплата подтверждена!\n\n"
            f"Запрос: {payment['request_number']}\n"
            f"Сумма: {payment['amount']} руб\n"
            f"Продавец: {payment['seller_code']}\n"
            f"Новый долг продавца: {new_debt} руб\n"
            f"Новая сумма к переводу: {new_pending} руб",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К запросам", callback_data="payments_pending")
            ]])
        )
        
        # Уведомляем продавца
        if payment['telegram_id']:
            try:
                await context.bot.send_message(
                    chat_id=payment['telegram_id'],
                    text=f"✅ Администратор подтвердил получение денег!\n\n"
                         f"Сумма: {payment['amount']} руб\n"
                         f"Номер запроса: {payment['request_number']}\n"
                         f"Ваш новый долг: {new_debt} руб\n"
                         f"Сумма к переводу: {new_pending} руб"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить продавца {payment['telegram_id']}: {e}")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")
    
    context.user_data.clear()
    return MAIN_MENU

async def payment_reject(update: Update, context):
    """Отклонение запроса на выплату"""
    query = update.callback_query
    await query.answer()
    
    payment_id = context.user_data.get('current_payment_id')
    if not payment_id:
        await query.edit_message_text("❌ Ошибка: запрос не найден")
        return MAIN_MENU
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.request_number, s.telegram_id, s.seller_code
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            WHERE pr.id = ?
        """, (payment_id,))
        payment = cursor.fetchone()
        
        cursor.execute("""
            UPDATE payment_requests
            SET status = 'rejected'
            WHERE id = ?
        """, (payment_id,))
    
    await query.edit_message_text(
        f"❌ Запрос {payment['request_number']} отклонён",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К запросам", callback_data="payments_pending")
        ]])
    )
    
    if payment['telegram_id']:
        try:
            await context.bot.send_message(
                chat_id=payment['telegram_id'],
                text=f"❌ Ваш запрос на выплату {payment['request_number']} был отклонён администратором.\n"
                     f"Обратитесь к администратору для уточнения причин."
            )
        except Exception as e:
            logger.error(f"Не удалось уведомить продавца {payment['telegram_id']}: {e}")
    
    context.user_data.clear()
    return MAIN_MENU

async def payments_history(update: Update, context):
    """История платежей (последние 20)"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.request_number, pr.amount, pr.status, pr.created_at, pr.approved_at,
                   s.seller_code, s.full_name
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            ORDER BY pr.created_at DESC
            LIMIT 20
        """)
        history = cursor.fetchall()
    
    if not history:
        await query.edit_message_text(
            "📭 История пуста",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")
            ]])
        )
        return MAIN_MENU
    
    text = "📋 Последние 20 запросов:\n\n"
    for h in history:
        status_emoji = {
            'pending': '🟡',
            'approved': '✅',
            'rejected': '❌'
        }.get(h['status'], '⚪')
        date = h['approved_at'][:16] if h['approved_at'] else h['created_at'][:16]
        text += f"{status_emoji} {h['request_number']} - {h['seller_code']}: {h['amount']} руб ({date})\n"
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")
        ]])
    )
    return MAIN_MENU

async def payments_stats(update: Update, context):
    """Статистика по платежам"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END) as total_approved
            FROM payment_requests
        """)
        stats = cursor.fetchone()
        
        cursor.execute("""
            SELECT s.seller_code, s.full_name,
                   COUNT(pr.id) as requests,
                   SUM(pr.amount) as total
            FROM sellers s
            LEFT JOIN payment_requests pr ON s.id = pr.seller_id AND pr.status = 'approved'
            GROUP BY s.id
            HAVING requests > 0
            ORDER BY total DESC
            LIMIT 5
        """)
        top_sellers = cursor.fetchall()
    
    text = "📊 Статистика платежей\n\n"
    text += f"Всего запросов: {stats['total']}\n"
    text += f"✅ Подтверждено: {stats['approved']}\n"
    text += f"🟡 Ожидает: {stats['pending']}\n"
    text += f"❌ Отклонено: {stats['rejected']}\n"
    text += f"💵 Выплачено всего: {stats['total_approved']} руб\n\n"
    
    if top_sellers:
        text += "🏆 Топ продавцов по выплатам:\n"
        for s in top_sellers:
            text += f"• {s['seller_code']} - {s['full_name'][:15]}: {s['total']} руб\n"
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")
        ]])
    )
    return MAIN_MENU

async def back_to_menu(update: Update, context):
    """Возврат в главное меню платежей"""
    query = update.callback_query
    await query.answer()
    
    # Получаем свежую статистику
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
        pending_count = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM payment_requests WHERE status = 'pending'")
        pending_sum = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'approved' AND date(approved_at) = date('now')")
        approved_today = cursor.fetchone()[0]
    
    keyboard = [
        [InlineKeyboardButton(f"🟡 Ожидающие запросы ({pending_count})", callback_data="payments_pending")],
        [InlineKeyboardButton("📋 История платежей", callback_data="payments_history")],
        [InlineKeyboardButton("📊 Статистика", callback_data="payments_stats")],
        [InlineKeyboardButton("🔙 В админ-меню", callback_data="payments_exit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"💰 Управление платежами\n\n"
        f"🟡 Ожидают подтверждения: {pending_count}\n"
        f"💵 Сумма к выплате: {pending_sum} руб\n"
        f"✅ Подтверждено сегодня: {approved_today}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return MAIN_MENU

async def exit_payments(update: Update, context):
    """Выход в главное админское меню"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Выход в главное меню",
        reply_markup=get_admin_menu()
    )
    return ConversationHandler.END

# ConversationHandler для управления платежами
admin_payments_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^💰 Управление платежами$'), admin_payments_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(payments_pending, pattern='^payments_pending$'),
            CallbackQueryHandler(payments_history, pattern='^payments_history$'),
            CallbackQueryHandler(payments_stats, pattern='^payments_stats$'),
            CallbackQueryHandler(back_to_menu, pattern='^payments_back_to_menu$'),
            CallbackQueryHandler(exit_payments, pattern='^payments_back$'),
            CallbackQueryHandler(exit_payments, pattern='^payments_exit$')
        ],
        VIEW_REQUEST: [
            CallbackQueryHandler(payment_view, pattern='^payment_view_'),
            CallbackQueryHandler(payments_pending, pattern='^payments_pending$'),
            CallbackQueryHandler(back_to_menu, pattern='^payments_back_to_menu$')
        ],
        CONFIRM_PAYMENT: [
            CallbackQueryHandler(payment_confirm, pattern='^payment_confirm$'),
            CallbackQueryHandler(payment_reject, pattern='^payment_reject$'),
            CallbackQueryHandler(payments_pending, pattern='^payments_pending$')
        ]
    },
    fallbacks=[CommandHandler('cancel', exit_payments)],
    allow_reentry=True
)
