#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Обработчик для запроса выплаты (продавец)
Позволяет продавцу запросить перевод части суммы к переводу.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_main_menu, get_back_keyboard
from backup_decorator import send_backup_to_admin
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Состояния разговора
ENTERING_AMOUNT, CONFIRMING = range(2)

async def payment_request_start(update: Update, context):
    """Начало запроса выплаты – проверяем доступную сумму и запрашиваем сумму."""
    query = update.callback_query
    await query.answer()
    logger.info("payment_request_start called by user %s", update.effective_user.id)

    user_id = update.effective_user.id
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, seller_code FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
        if not seller:
            await query.edit_message_text(
                "❌ Вы не активированы как продавец. Нажмите /start для активации."
            )
            return ConversationHandler.END
        seller_id = seller['id']
        seller_code = seller['seller_code']
        context.user_data['seller_id'] = seller_id
        context.user_data['seller_code'] = seller_code

        # Получаем текущую сумму к переводу
        cursor.execute("SELECT pending_amount FROM seller_pending WHERE seller_id = ?", (seller_id,))
        pending_row = cursor.fetchone()
        pending_amount = pending_row['pending_amount'] if pending_row else 0
        context.user_data['pending_amount'] = pending_amount

    if pending_amount <= 0:
        await query.edit_message_text(
            "❌ У вас нет средств для перевода.",
            reply_markup=None
        )
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text="Выберите действие:",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    # Убираем инлайн-кнопки и запрашиваем сумму
    await query.edit_message_text(
        f"💰 Доступно для перевода: {pending_amount} руб\n\n"
        f"Введите сумму, которую хотите перевести (целое число, не больше {pending_amount}):",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите сумму:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_AMOUNT

async def amount_entered(update: Update, context):
    """Обработка введённой суммы, показываем подтверждение."""
    text = update.message.text
    logger.info("amount_entered: %s", text)

    if text == '🔙 Назад':
        # Возвращаемся в раздел "Остатки" (просто показываем главное меню, так как из остатков мы пришли)
        await update.message.reply_text(
            "Возврат в главное меню.",
            reply_markup=get_main_menu()
        )
        return ConversationHandler.END

    try:
        amount = int(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Ошибка: введите целое положительное число.\n"
            "Например: 500 или 1000",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_AMOUNT

    pending = context.user_data['pending_amount']
    if amount > pending:
        await update.message.reply_text(
            f"❌ Сумма не может превышать доступную ({pending} руб).",
            reply_markup=get_back_keyboard()
        )
        return ENTERING_AMOUNT

    context.user_data['request_amount'] = amount

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_payment")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="change_amount")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_payment")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"Сумма к переводу: {amount} руб\n\n"
        f"После подтверждения запрос будет отправлен администратору.",
        reply_markup=reply_markup
    )
    return CONFIRMING

@send_backup_to_admin("запрос выплаты")
async def confirm_payment(update: Update, context):
    """Подтверждение запроса – создаём запись в payment_requests."""
    query = update.callback_query
    await query.answer()
    logger.info("confirm_payment called")

    seller_id = context.user_data['seller_id']
    seller_code = context.user_data['seller_code']
    amount = context.user_data['request_amount']

    # Генерируем номер запроса
    today = datetime.now()
    date_str = today.strftime("%d%m")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        # Получаем количество запросов продавца за сегодня
        cursor.execute("""
            SELECT COUNT(*) FROM payment_requests
            WHERE seller_id = ? AND date(created_at) = date('now')
        """, (seller_id,))
        count = cursor.fetchone()[0] + 1
        request_number = f"В-{seller_code}-{date_str}-{count:03d}"  # В – выплата

        # Создаём запрос
        cursor.execute("""
            INSERT INTO payment_requests (request_number, seller_id, amount, status, created_at)
            VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        """, (request_number, seller_id, amount))

    await query.edit_message_text(
        f"✅ Запрос на выплату отправлен!\n\n"
        f"Номер запроса: {request_number}\n"
        f"Сумма: {amount} руб\n"
        f"Статус: ожидает подтверждения администратора."
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите следующее действие:",
        reply_markup=get_main_menu()
    )
    context.user_data.clear()
    return ConversationHandler.END

async def change_amount(update: Update, context):
    """Изменить сумму – возвращаемся к вводу."""
    query = update.callback_query
    await query.answer()
    logger.info("change_amount called")

    await query.edit_message_text(
        f"💰 Доступно для перевода: {context.user_data['pending_amount']} руб\n\n"
        f"Введите новую сумму:",
        reply_markup=None
    )
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Введите сумму:",
        reply_markup=get_back_keyboard()
    )
    return ENTERING_AMOUNT

async def cancel_payment(update: Update, context):
    """Отмена запроса."""
    query = update.callback_query
    await query.answer()
    logger.info("cancel_payment called")

    await query.edit_message_text("❌ Запрос отменён.")
    await context.bot.send_message(
        chat_id=update.effective_user.id,
        text="Выберите действие:",
        reply_markup=get_main_menu()
    )
    context.user_data.clear()
    return ConversationHandler.END

# ConversationHandler для запроса выплаты
payment_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(payment_request_start, pattern='^request_payment$')],
    states={
        ENTERING_AMOUNT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, amount_entered)
        ],
        CONFIRMING: [
            CallbackQueryHandler(confirm_payment, pattern='^confirm_payment$'),
            CallbackQueryHandler(change_amount, pattern='^change_amount$'),
            CallbackQueryHandler(cancel_payment, pattern='^cancel_payment$')
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_payment)],
    allow_reentry=True
)
