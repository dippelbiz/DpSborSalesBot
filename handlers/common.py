#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Общие обработчики для всех пользователей
"""

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from config import config
from database import db
from keyboards import get_main_menu, get_admin_menu, get_seller_menu, get_back_keyboard

# Состояние для активации
ENTERING_CODE = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    if context.user_data:
        context.user_data.clear()
    
    if user.id in config.ADMIN_IDS:
        await update.message.reply_text(
            f"🔐 Добро пожаловать, администратор {user.full_name}!",
            reply_markup=get_admin_menu()
        )
        db.log_action(user_id=user.id, user_role="admin", action="start")
        return ConversationHandler.END
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user.id,))
        seller = cursor.fetchone()
    
    if seller:
        # Продавец уже активирован
        await update.message.reply_text(
            f"👋 С возвращением, {seller['full_name']}!",
            reply_markup=get_seller_menu(seller['seller_code'])
        )
        db.log_action(user_id=user.id, user_role="seller", action="start", details=seller['seller_code'])
    else:
        # Новый пользователь – просим код активации (на самом деле не должен появляться, но на всякий случай оставим)
        await update.message.reply_text(
            "👋 Добро пожаловать! Для активации введите код, полученный от администратора.",
            reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
        )
    return ConversationHandler.END

async def activate_seller_start(update: Update, context):
    """Начало процесса активации (оставлено для обратной совместимости)"""
    await update.message.reply_text(
        "🔑 Введите ваш код активации:",
        reply_markup=ReplyKeyboardMarkup([['❌ Отмена']], resize_keyboard=True)
    )
    return ENTERING_CODE

async def activate_seller(update: Update, context):
    """Активация продавца по коду"""
    user = update.effective_user
    code = update.message.text.strip().upper()
    if code in ('❌ ОТМЕНА', '❌ Отмена'):
        await update.message.reply_text("❌ Активация отменена.", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
        return ConversationHandler.END

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE seller_code = ?", (code,))
        seller = cursor.fetchone()
        if not seller:
            await update.message.reply_text(f"❌ Код '{code}' не найден.", reply_markup=ReplyKeyboardMarkup([['❌ Отмена']], resize_keyboard=True))
            return ENTERING_CODE
        if seller['telegram_id'] and seller['telegram_id'] != user.id:
            await update.message.reply_text("❌ Код уже привязан к другому аккаунту.")
            return ConversationHandler.END
        if not seller['is_active']:
            await update.message.reply_text("❌ Ваш аккаунт заблокирован.")
            return ConversationHandler.END
        cursor.execute("UPDATE sellers SET telegram_id = ? WHERE id = ?", (user.id, seller['id']))

    await update.message.reply_text(
        f"✅ Активация успешна!\nДобро пожаловать, {seller['full_name']}!",
        reply_markup=get_seller_menu(seller['seller_code'])
    )
    db.log_action(user_id=user.id, user_role="seller", action="activate", details=seller['seller_code'])
    return ConversationHandler.END

async def cancel_activation(update: Update, context):
    await update.message.reply_text("❌ Активация отменена.", reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True))
    return ConversationHandler.END

async def menu_handler(update: Update, context):
    text = update.message.text
    user_id = update.effective_user.id
    if context.user_data:
        context.user_data.clear()

    is_admin = user_id in config.ADMIN_IDS
    if is_admin:
        # Админские кнопки
        if text == '📦 Управление поставками':
            from handlers.admin.orders import admin_orders_start
            return await admin_orders_start(update, context)
        elif text == '💰 Управление платежами':
            from handlers.admin.payments import admin_payments_start
            return await admin_payments_start(update, context)
        elif text == '📊 Отчеты':
            from handlers.admin.reports import admin_reports_start
            return await admin_reports_start(update, context)
        elif text == '⚙️ Настройки':
            from handlers.admin.settings import admin_settings_start
            return await admin_settings_start(update, context)
        elif text == '🆘 Пополнение склада':
            from handlers.admin.restock import restock_admin_start
            return await restock_admin_start(update, context)
        # ... остальные кнопки админа
        else:
            await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=get_admin_menu())
        return ConversationHandler.END

    # Для продавцов – проверяем активацию
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
    if not seller:
        # Если не активирован – предлагаем активацию
        if text == 'Ввести код активации':
            return await activate_seller_start(update, context)
        else:
            await update.message.reply_text("❌ Для работы необходимо активировать аккаунт.", reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True))
            return ConversationHandler.END

    # Обработка кнопок продавца
    if text == '📦 Заявка на поставку':
        from handlers.seller.orders import orders_start
        return await orders_start(update, context)
    elif text == '📤 Отгруженные поставки':
        from handlers.seller.shipments import shipments_start
        return await shipments_start(update, context)
    elif text == '💰 Реализовано':
        from handlers.seller.sales import sales_start
        return await sales_start(update, context)
    elif text == '📊 Остатки':
        from handlers.seller.stock import stock_start
        return await stock_start(update, context)
    elif text == '📋 Мои заявки':
        from handlers.seller.orders import my_orders
        return await my_orders(update, context)
    elif text == '📦 Заявка на пополнение склада':
        from handlers.seller.restock import restock_start
        return await restock_start(update, context)
    elif text == '❌ Отмена':
        await update.message.reply_text("Действие отменено.", reply_markup=get_seller_menu(seller['seller_code']))
        return ConversationHandler.END
    else:
        await update.message.reply_text("Пожалуйста, используйте кнопки меню.", reply_markup=get_seller_menu(seller['seller_code']))
        return ConversationHandler.END

async def handle_message(update: Update, context):
    """Общий обработчик для любых других сообщений"""
    user_id = update.effective_user.id
    is_admin = user_id in config.ADMIN_IDS
    if is_admin:
        await update.message.reply_text("Я не понимаю эту команду.", reply_markup=get_admin_menu())
    else:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT seller_code FROM sellers WHERE telegram_id = ?", (user_id,))
            res = cursor.fetchone()
        if res:
            await update.message.reply_text("Я не понимаю эту команду.", reply_markup=get_seller_menu(res['seller_code']))
        else:
            await update.message.reply_text("Я не понимаю эту команду.", reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True))
    return ConversationHandler.END

activation_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^Ввести код активации$'), activate_seller_start)],
    states={ENTERING_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, activate_seller)]},
    fallbacks=[CommandHandler('cancel', cancel_activation)],
    allow_reentry=True
)
