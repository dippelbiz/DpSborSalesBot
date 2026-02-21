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
MAIN_MENU, VIEW_REQUESTS, CONFIRM_PAYMENT, EDITING_AMOUNT = range(4)

async def admin_payments_start(update: Update, context):
    """Главное меню управления платежами"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    # Получаем статистику по платежам
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
        pending_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(amount) FROM payment_requests WHERE status = 'pending'")
        pending_sum = cursor.fetchone()[0] or 0
        
        cursor.execute("""
            SELECT COUNT(*) FROM payment_requests 
            WHERE status = 'approved' AND date(approved_at) = date('now')
        """)
        approved_today = cursor.fetchone()[0]
    
    keyboard = [
        [InlineKeyboardButton(f"🟡 Новые запросы ({pending_count})", callback_data="payments_pending")],
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
    """Просмотр ожидающих запросов на выплату"""
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
            "📭 Нет ожидающих запросов на выплату",
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
        text += f"   Продавец: {req['seller_code']} - {req['full_name']}\n"
        text += f"   Сумма: {req['amount']} руб\n"
        text += f"   от {req['created_at'][:16]}\n\n"
        total_sum += req['amount']
        keyboard.append([InlineKeyboardButton(
            f"✅ {req['request_number']} - {req['amount']} руб",
            callback_data=f"payment_view_{req['id']}"
        )])
    
    text += f"💵 Всего к выплате: {total_sum} руб\n\n"
    text += "Выберите запрос для просмотра:"
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return VIEW_REQUESTS

async def payment_view(update: Update, context):
    """Просмотр деталей конкретного запроса"""
    query = update.callback_query
    await query.answer()
    
    payment_id = int(query.data.replace('payment_view_', ''))
    context.user_data['current_payment_id'] = payment_id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.*, s.seller_code, s.full_name,
                   sd.total_debt, sp.pending_amount
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            JOIN seller_debt sd ON s.id = sd.seller_id
            JOIN seller_pending sp ON s.id = sp.seller_id
            WHERE pr.id = ?
        """, (payment_id,))
        payment = cursor.fetchone()
    
    if not payment:
        await query.edit_message_text(
            "❌ Запрос не найден",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="payments_pending")
            ]])
        )
        return VIEW_REQUESTS
    
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
        [InlineKeyboardButton("✏️ Редактировать сумму", callback_data="payment_edit")],  # Новая кнопка
        [InlineKeyboardButton("❌ Отклонить", callback_data="payment_reject")],
        [InlineKeyboardButton("🔙 Назад", callback_data="payments_pending")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    return CONFIRM_PAYMENT

async def payment_edit_start(update: Update, context):
    """Начало редактирования суммы выплаты"""
    query = update.callback_query
    await query.answer()
    logger.info("payment_edit_start called")
    
    payment_id = context.user_data.get('current_payment_id')
    if not payment_id:
        await query.edit_message_text("❌ Ошибка: запрос не найден")
        return VIEW_REQUESTS
    
    # Получаем информацию о запросе и продавце для отображения
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.amount, s.pending_amount, s.seller_code
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            WHERE pr.id = ?
        """, (payment_id,))
        data = cursor.fetchone()
        if not data:
            await query.edit_message_text("❌ Запрос не найден")
            return VIEW_REQUESTS
    
    context.user_data['edit_original_amount'] = data['amount']
    max_amount = data['pending_amount']  # сумма к переводу (максимум для редактирования)
    context.user_data['edit_max_amount'] = max_amount
    
    # Убираем клавиатуру и просим ввести новую сумму
    await query.edit_message_text(
        f"💰 Редактирование суммы выплаты\n\n"
        f"Исходная сумма: {data['amount']} руб\n"
        f"Доступно у продавца: {max_amount} руб\n\n"
        f"Введите новую сумму (целое положительное число, не больше {max_amount}):",
        reply_markup=None
    )
    return EDITING_AMOUNT

async def payment_edit_amount(update: Update, context):
    """Обработка ввода новой суммы"""
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    try:
        amount = int(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое положительное число.\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="payment_cancel_edit")
            ]])
        )
        return EDITING_AMOUNT
    
    max_amount = context.user_data.get('edit_max_amount')
    if amount > max_amount:
        await update.message.reply_text(
            f"❌ Сумма не может превышать доступную ({max_amount} руб).\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="payment_cancel_edit")
            ]])
        )
        return EDITING_AMOUNT
    
    context.user_data['edit_new_amount'] = amount
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="payment_edit_confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="payment_edit_again")],
        [InlineKeyboardButton("❌ Отмена", callback_data="payment_cancel_edit")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Проверьте новую сумму:\n\n"
        f"Сумма: {amount} руб\n\n"
        f"После подтверждения исходный запрос будет отклонён,\n"
        f"и продавцу будет зачислена новая сумма.",
        reply_markup=reply_markup
    )
    return EDITING_AMOUNT

@send_backup_to_admin("редактирование и подтверждение выплаты")
async def payment_edit_confirm(update: Update, context):
    """Подтверждение отредактированной суммы – отклоняем старый запрос, создаём новую выплату."""
    query = update.callback_query
    await query.answer()
    logger.info("payment_edit_confirm called")
    
    payment_id = context.user_data.get('current_payment_id')
    new_amount = context.user_data.get('edit_new_amount')
    original_amount = context.user_data.get('edit_original_amount')
    
    if not payment_id or not new_amount:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return MAIN_MENU
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем информацию о запросе и продавце
            cursor.execute("""
                SELECT pr.seller_id, pr.request_number, s.seller_code, s.full_name,
                       sd.total_debt, sp.pending_amount
                FROM payment_requests pr
                JOIN sellers s ON pr.seller_id = s.id
                JOIN seller_debt sd ON s.id = sd.seller_id
                JOIN seller_pending sp ON s.id = sp.seller_id
                WHERE pr.id = ?
            """, (payment_id,))
            data = cursor.fetchone()
            if not data:
                await query.edit_message_text("❌ Запрос не найден")
                return MAIN_MENU
            
            seller_id = data['seller_id']
            seller_code = data['seller_code']
            full_name = data['full_name']
            old_request_number = data['request_number']
            
            # Отклоняем старый запрос (меняем статус)
            cursor.execute("""
                UPDATE payment_requests
                SET status = 'rejected'
                WHERE id = ?
            """, (payment_id,))
            
            # Создаём новый подтверждённый запрос на новую сумму
            from datetime import datetime
            date_str = datetime.now().strftime("%d%m")
            cursor.execute("""
                SELECT COUNT(*) FROM payment_requests
                WHERE seller_id = ? AND date(created_at) = date('now')
            """, (seller_id,))
            count = cursor.fetchone()[0] + 1
            new_request_number = f"В-{seller_code}-{date_str}-{count:03d}"
            
            cursor.execute("""
                INSERT INTO payment_requests (request_number, seller_id, amount, status, created_at, approved_at)
                VALUES (?, ?, ?, 'approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """, (new_request_number, seller_id, new_amount))
            
            # Уменьшаем общий долг продавца
            cursor.execute("""
                UPDATE seller_debt
                SET total_debt = total_debt - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE seller_id = ?
            """, (new_amount, seller_id))
            
            # Уменьшаем сумму к переводу
            cursor.execute("""
                UPDATE seller_pending
                SET pending_amount = pending_amount - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE seller_id = ?
            """, (new_amount, seller_id))
            
            # Получаем обновлённые данные для уведомления
            cursor.execute("""
                SELECT total_debt, pending_amount
                FROM seller_debt sd
                JOIN seller_pending sp ON sd.seller_id = sp.seller_id
                WHERE sd.seller_id = ?
            """, (seller_id,))
            new_state = cursor.fetchone()
        
        # Отправляем уведомление админу
        await query.edit_message_text(
            f"✅ Выплата подтверждена с изменённой суммой!\n\n"
            f"Исходный запрос: {old_request_number} (сумма {original_amount} руб) отклонён.\n"
            f"Новый запрос: {new_request_number}\n"
            f"Сумма: {new_amount} руб\n"
            f"Продавец: {seller_code} - {full_name}\n\n"
            f"💰 Новое состояние продавца:\n"
            f"• Долг за товар: {new_state['total_debt']} руб\n"
            f"• Сумма к переводу: {new_state['pending_amount']} руб",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К запросам", callback_data="payments_pending")
            ]])
        )
        
        # Отправляем уведомление продавцу (если у него есть telegram_id)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id FROM sellers WHERE id = ?", (seller_id,))
            seller_tg = cursor.fetchone()
            if seller_tg and seller_tg['telegram_id']:
                try:
                    await context.bot.send_message(
                        chat_id=seller_tg['telegram_id'],
                        text=f"✅ Администратор подтвердил выплату!\n\n"
                             f"Сумма: {new_amount} руб\n"
                             f"Номер запроса: {new_request_number}\n\n"
                             f"Ваш текущий долг: {new_state['total_debt']} руб\n"
                             f"Сумма к переводу: {new_state['pending_amount']} руб"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify seller {seller_id}: {e}")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
    
    context.user_data.clear()
    return MAIN_MENU

async def payment_edit_again(update: Update, context):
    """Вернуться к вводу суммы (изменить ещё раз)"""
    query = update.callback_query
    await query.answer()
    logger.info("payment_edit_again called")
    
    max_amount = context.user_data.get('edit_max_amount')
    await query.edit_message_text(
        f"💰 Введите новую сумму (не больше {max_amount}):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="payment_cancel_edit")
        ]])
    )
    return EDITING_AMOUNT

async def payment_cancel_edit(update: Update, context):
    """Отмена редактирования, возврат к деталям запроса"""
    query = update.callback_query
    await query.answer()
    logger.info("payment_cancel_edit called")
    
    # Возвращаемся к просмотру запроса
    await payment_view(update, context)
    return CONFIRM_PAYMENT

@send_backup_to_admin("подтверждение выплаты")
async def payment_confirm(update: Update, context):
    """Подтверждение выплаты без изменений"""
    query = update.callback_query
    await query.answer()
    
    payment_id = context.user_data.get('current_payment_id')
    
    if not payment_id:
        await query.edit_message_text("❌ Ошибка: запрос не найден")
        return ConversationHandler.END
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT pr.*, s.id as seller_id, s.seller_code, s.full_name, s.telegram_id
                FROM payment_requests pr
                JOIN sellers s ON pr.seller_id = s.id
                WHERE pr.id = ?
            """, (payment_id,))
            payment = cursor.fetchone()
            
            if not payment:
                await query.edit_message_text("❌ Запрос не найден")
                return ConversationHandler.END
            
            cursor.execute("""
                UPDATE payment_requests 
                SET status = 'approved', approved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (payment_id,))
            
            cursor.execute("""
                UPDATE seller_debt 
                SET total_debt = total_debt - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE seller_id = ?
            """, (payment['amount'], payment['seller_id']))
            
            cursor.execute("""
                UPDATE seller_pending 
                SET pending_amount = pending_amount - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE seller_id = ?
            """, (payment['amount'], payment['seller_id']))
            
            cursor.execute("""
                SELECT total_debt, pending_amount 
                FROM seller_debt sd
                JOIN seller_pending sp ON sd.seller_id = sp.seller_id
                WHERE sd.seller_id = ?
            """, (payment['seller_id'],))
            new_state = cursor.fetchone()
        
        await query.edit_message_text(
            f"✅ Выплата подтверждена!\n\n"
            f"Номер запроса: {payment['request_number']}\n"
            f"Сумма: {payment['amount']} руб\n"
            f"Продавец: {payment['seller_code']} - {payment['full_name']}\n\n"
            f"💰 Новое состояние продавца:\n"
            f"• Долг за товар: {new_state['total_debt']} руб\n"
            f"• Сумма к переводу: {new_state['pending_amount']} руб",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К запросам", callback_data="payments_pending")
            ]])
        )
        
        # Уведомляем продавца
        if payment['telegram_id']:
            try:
                await context.bot.send_message(
                    chat_id=payment['telegram_id'],
                    text=f"✅ Администратор подтвердил выплату!\n\n"
                         f"Сумма: {payment['amount']} руб\n"
                         f"Номер запроса: {payment['request_number']}\n\n"
                         f"Ваш текущий долг: {new_state['total_debt']} руб\n"
                         f"Сумма к переводу: {new_state['pending_amount']} руб"
                )
            except Exception as e:
                logger.error(f"Failed to notify seller {payment['seller_id']}: {e}")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
    
    context.user_data.clear()
    return MAIN_MENU

async def payment_reject(update: Update, context):
    """Отклонение запроса на выплату"""
    query = update.callback_query
    await query.answer()
    
    payment_id = context.user_data.get('current_payment_id')
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE payment_requests 
            SET status = 'rejected'
            WHERE id = ?
        """, (payment_id,))
        
        cursor.execute("SELECT request_number, seller_id FROM payment_requests WHERE id = ?", (payment_id,))
        data = cursor.fetchone()
        req_number = data['request_number']
        seller_id = data['seller_id']
    
    await query.edit_message_text(
        f"❌ Запрос {req_number} отклонен",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К запросам", callback_data="payments_pending")
        ]])
    )
    
    # Уведомляем продавца (опционально)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM sellers WHERE id = ?", (seller_id,))
        seller_tg = cursor.fetchone()
        if seller_tg and seller_tg['telegram_id']:
            try:
                await context.bot.send_message(
                    chat_id=seller_tg['telegram_id'],
                    text=f"❌ Ваш запрос на выплату №{req_number} отклонён администратором."
                )
            except Exception as e:
                logger.error(f"Failed to notify seller {seller_id}: {e}")
    
    context.user_data.clear()
    return MAIN_MENU

async def payments_history(update: Update, context):
    """История платежей"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pr.request_number, pr.amount, pr.status, pr.created_at,
                   pr.approved_at, s.seller_code, s.full_name
            FROM payment_requests pr
            JOIN sellers s ON pr.seller_id = s.id
            ORDER BY pr.created_at DESC
            LIMIT 20
        """)
        history = cursor.fetchall()
    
    if not history:
        await query.edit_message_text(
            "📭 История платежей пуста",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="payments_back_to_menu")
            ]])
        )
        return MAIN_MENU
    
    text = "📋 История платежей (последние 20):\n\n"
    
    for item in history:
        status_emoji = {
            'pending': '🟡',
            'approved': '✅',
            'rejected': '❌'
        }.get(item['status'], '⚪')
        
        date_str = item['approved_at'][:16] if item['approved_at'] else item['created_at'][:16]
        text += f"{status_emoji} {item['request_number']}\n"
        text += f"   Продавец: {item['seller_code']} - {item['full_name'][:15]}\n"
        text += f"   Сумма: {item['amount']} руб\n"
        text += f"   {date_str}\n\n"
    
    text += "✅ - подтвержден, 🟡 - ожидает, ❌ - отклонен"
    
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
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) as approved_count,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_count,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected_count,
                SUM(CASE WHEN status = 'approved' THEN amount ELSE 0 END) as total_approved
            FROM payment_requests
        """)
        stats = cursor.fetchone()
        
        cursor.execute("""
            SELECT s.seller_code, s.full_name,
                   COUNT(pr.id) as requests_count,
                   SUM(CASE WHEN pr.status = 'approved' THEN pr.amount ELSE 0 END) as total_paid
            FROM sellers s
            LEFT JOIN payment_requests pr ON s.id = pr.seller_id
            GROUP BY s.id
            HAVING requests_count > 0
            ORDER BY total_paid DESC
            LIMIT 5
        """)
        top_sellers = cursor.fetchall()
    
    text = "📊 Статистика платежей\n\n"
    text += f"Всего запросов: {stats['total_requests'] or 0}\n"
    text += f"✅ Подтверждено: {stats['approved_count'] or 0}\n"
    text += f"🟡 Ожидает: {stats['pending_count'] or 0}\n"
    text += f"❌ Отклонено: {stats['rejected_count'] or 0}\n"
    text += f"💵 Выплачено всего: {stats['total_approved'] or 0} руб\n\n"
    
    if top_sellers:
        text += "🏆 Топ продавцов по выплатам:\n"
        for seller in top_sellers:
            text += f"• {seller['seller_code']} - {seller['full_name'][:15]}: {seller['total_paid']} руб\n"
    
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
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM payment_requests WHERE status = 'pending'")
        pending_count = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(amount) FROM payment_requests WHERE status = 'pending'")
        pending_sum = cursor.fetchone()[0] or 0
        cursor.execute("""
            SELECT COUNT(*) FROM payment_requests 
            WHERE status = 'approved' AND date(approved_at) = date('now')
        """)
        approved_today = cursor.fetchone()[0]
    
    keyboard = [
        [InlineKeyboardButton(f"🟡 Новые запросы ({pending_count})", callback_data="payments_pending")],
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
        VIEW_REQUESTS: [
            CallbackQueryHandler(payment_view, pattern='^payment_view_'),
            CallbackQueryHandler(payments_pending, pattern='^payments_pending$'),
            CallbackQueryHandler(back_to_menu, pattern='^payments_back_to_menu$')
        ],
        CONFIRM_PAYMENT: [
            CallbackQueryHandler(payment_confirm, pattern='^payment_confirm$'),
            CallbackQueryHandler(payment_edit_start, pattern='^payment_edit$'),
            CallbackQueryHandler(payment_reject, pattern='^payment_reject$'),
            CallbackQueryHandler(payments_pending, pattern='^payments_pending$'),
            CallbackQueryHandler(payment_cancel_edit, pattern='^payment_cancel_edit$')
        ],
        EDITING_AMOUNT: [
            CallbackQueryHandler(payment_edit_confirm, pattern='^payment_edit_confirm$'),
            CallbackQueryHandler(payment_edit_again, pattern='^payment_edit_again$'),
            CallbackQueryHandler(payment_cancel_edit, pattern='^payment_cancel_edit$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, payment_edit_amount)
        ]
    },
    fallbacks=[CommandHandler('cancel', exit_payments)],
    allow_reentry=True
)
