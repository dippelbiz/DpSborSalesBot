#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Общие обработчики для всех пользователей
"""

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from config import config
from database import db
from keyboards import get_main_menu, get_admin_menu

# Состояние для активации
ENTERING_CODE = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Проверяем, является ли пользователь администратором
    if user.id in config.ADMIN_IDS:
        # Для админа специальное меню
        await update.message.reply_text(
            f"🔐 Добро пожаловать, администратор {user.full_name}!\n\n"
            f"Выберите раздел в меню:",
            reply_markup=get_admin_menu()
        )
        
        db.log_action(
            user_id=user.id,
            user_role="admin",
            action="start",
            details=f"Запуск бота (админ)"
        )
        return ConversationHandler.END
    
    # Проверяем, есть ли продавец с таким Telegram ID
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user.id,))
        seller = cursor.fetchone()
    
    if seller:
        # Продавец уже активирован
        await update.message.reply_text(
            f"👋 С возвращением, {seller['full_name']}!\n\n"
            f"Ваш код: {seller['seller_code']}\n"
            f"Выберите действие:",
            reply_markup=get_main_menu()
        )
        
        db.log_action(
            user_id=user.id,
            user_role="seller",
            action="start",
            details=f"Возврат продавца {seller['seller_code']}"
        )
    else:
        # Новый пользователь - просим код активации
        await update.message.reply_text(
            f"👋 Добро пожаловать, {user.full_name}!\n\n"
            f"Для активации аккаунта введите код, полученный от администратора.\n"
            f"Код должен быть в формате: А, А1, ТЕСТ и т.д.",
            reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
        )
    
    return ConversationHandler.END

async def activate_seller_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало процесса активации"""
    await update.message.reply_text(
        "🔑 Введите ваш код активации:\n\n"
        "Код должен быть в формате: А, А1, ТЕСТ и т.д.\n"
        "Или нажмите '❌ Отмена' для выхода",
        reply_markup=ReplyKeyboardMarkup([['❌ Отмена']], resize_keyboard=True)
    )
    return ENTERING_CODE

async def activate_seller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Активация продавца по коду"""
    user = update.effective_user
    code = update.message.text.strip().upper()
    
    if code == '❌ ОТМЕНА' or code == '❌ Отмена':
        await update.message.reply_text(
            "❌ Активация отменена.\n"
            "Для повторной попытки отправьте /start",
            reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True)
        )
        return ConversationHandler.END
    
    # Ищем продавца с таким кодом
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE seller_code = ?", (code,))
        seller = cursor.fetchone()
        
        if not seller:
            await update.message.reply_text(
                f"❌ Код '{code}' не найден.\n"
                f"Проверьте код или обратитесь к администратору.\n\n"
                f"Попробуйте снова или нажмите '❌ Отмена':",
                reply_markup=ReplyKeyboardMarkup([['❌ Отмена']], resize_keyboard=True)
            )
            return ENTERING_CODE
        
        if seller['telegram_id'] and seller['telegram_id'] != user.id:
            await update.message.reply_text(
                f"❌ Этот код уже привязан к другому аккаунту.\n"
                f"Обратитесь к администратору.",
                reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True)
            )
            return ConversationHandler.END
        
        if not seller['is_active']:
            await update.message.reply_text(
                f"❌ Ваш аккаунт заблокирован.\n"
                f"Обратитесь к администратору.",
                reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True)
            )
            return ConversationHandler.END
        
        # Привязываем Telegram ID к продавцу
        cursor.execute("""
            UPDATE sellers 
            SET telegram_id = ?
            WHERE id = ?
        """, (user.id, seller['id']))
    
    await update.message.reply_text(
        f"✅ Активация успешна!\n\n"
        f"Добро пожаловать, {seller['full_name']}!\n"
        f"Ваш код: {seller['seller_code']}\n\n"
        f"Теперь вы можете пользоваться ботом.",
        reply_markup=get_main_menu()
    )
    
    db.log_action(
        user_id=user.id,
        user_role="seller",
        action="activate",
        details=f"Продавец {seller['seller_code']} активирован"
    )
    
    return ConversationHandler.END

async def cancel_activation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отмена активации"""
    await update.message.reply_text(
        "❌ Активация отменена.\n"
        "Для повторной попытки отправьте /start",
        reply_markup=ReplyKeyboardMarkup([['/start']], resize_keyboard=True)
    )
    return ConversationHandler.END

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений для навигации по меню"""
    text = update.message.text
    user_id = update.effective_user.id
    
    # Проверка для админа
    is_admin = user_id in config.ADMIN_IDS
    
    # Админские кнопки
    if is_admin:
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
        
        elif text == '👥 Управление продавцами':
            from handlers.admin.sellers import admin_sellers_start
            return await admin_sellers_start(update, context)
    
    # Проверяем, активирован ли продавец
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
    
    if not seller and not is_admin:
        # Неактивированный пользователь
        if text == 'Ввести код активации':
            return await activate_seller_start(update, context)
        else:
            await update.message.reply_text(
                "❌ Для начала работы необходимо активировать аккаунт.\n"
                "Нажмите 'Ввести код активации'",
                reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
            )
            return
    
    # Обычные кнопки для активированных продавцов
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
    
    elif text == '❌ Отмена':
        if is_admin:
            await update.message.reply_text(
                "Действие отменено.",
                reply_markup=get_admin_menu()
            )
        else:
            await update.message.reply_text(
                "Действие отменено.",
                reply_markup=get_main_menu()
            )
        return
    
    else:
        if is_admin:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки меню.",
                reply_markup=get_admin_menu()
            )
        elif seller:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки меню.",
                reply_markup=get_main_menu()
            )
        else:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки меню.",
                reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
            )
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех остальных сообщений"""
    user_id = update.effective_user.id
    is_admin = user_id in config.ADMIN_IDS
    
    # Проверяем, активирован ли продавец
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sellers WHERE telegram_id = ?", (user_id,))
        seller = cursor.fetchone()
    
    if is_admin:
        await update.message.reply_text(
            "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
            reply_markup=get_admin_menu()
        )
    elif seller:
        await update.message.reply_text(
            "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
            reply_markup=get_main_menu()
        )
    else:
        await update.message.reply_text(
            "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
            reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
        )

# Создаем ConversationHandler для активации
activation_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^Ввести код активации$'), activate_seller_start)],
    states={
        ENTERING_CODE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, activate_seller)
        ]
    },
    fallbacks=[CommandHandler('cancel', cancel_activation)]
)
