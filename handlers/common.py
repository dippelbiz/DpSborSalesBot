#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Общие обработчики для всех пользователей
"""

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes

from config import config
from database import db
from keyboards import get_main_menu, get_admin_menu

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
        
        # Логируем действие
        db.log_action(
            user_id=user.id,
            user_role="admin",
            action="start",
            details=f"Запуск бота (админ)"
        )
    else:
        # Для обычных пользователей
        await update.message.reply_text(
            f"👋 Добро пожаловать, {user.full_name}!\n\n"
            f"Это бот для складского учета. Для работы необходимо активировать аккаунт.\n"
            f"Обратитесь к администратору для получения кода активации.",
            reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
        )
        
        db.log_action(
            user_id=user.id,
            user_role="unknown",
            action="start",
            details=f"Запуск бота (неактивированный пользователь)"
        )
    
    return

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений для навигации по меню"""
    text = update.message.text
    user_id = update.effective_user.id
    
    # Проверка для админа
    is_admin = user_id in config.ADMIN_IDS
    
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
    
    # Админские кнопки
    elif is_admin and text == '📦 Управление поставками':
        from handlers.admin.orders import admin_orders_start
        return await admin_orders_start(update, context)
    
    elif is_admin and text == '💰 Управление платежами':
        from handlers.admin.payments import admin_payments_start
        return await admin_payments_start(update, context)
    
    elif is_admin and text == '📊 Отчеты':
        from handlers.admin.reports import admin_reports_start
        return await admin_reports_start(update, context)
    
    elif is_admin and text == '⚙️ Настройки':
        from handlers.admin.settings import admin_settings_start
        return await admin_settings_start(update, context)
    
    elif is_admin and text == '👥 Управление продавцами':
        from handlers.admin.sellers import admin_sellers_start
        return await admin_sellers_start(update, context)
    
    elif text == 'Ввести код активации':
        await update.message.reply_text(
            "🔑 Введите ваш персональный код, полученный от администратора:"
        )
        # Здесь будет обработчик активации
        return
    
    elif text == '❌ Отмена':
        if is_admin:
            await update.message.reply_text(
                "Действие отменено.",
                reply_markup=get_admin_menu()
            )
        else:
            await update.message.reply_text(
                "Действие отменено.",
                reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
            )
        return
    
    else:
        if is_admin:
            await update.message.reply_text(
                "Пожалуйста, используйте кнопки меню.",
                reply_markup=get_admin_menu()
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
    
    if is_admin:
        await update.message.reply_text(
            "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
            reply_markup=get_admin_menu()
        )
    else:
        await update.message.reply_text(
            "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
            reply_markup=ReplyKeyboardMarkup([['Ввести код активации']], resize_keyboard=True)
        )
