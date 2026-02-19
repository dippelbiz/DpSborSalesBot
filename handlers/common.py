#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Общие обработчики для всех пользователей
"""

from telegram import Update
from telegram.ext import ContextTypes

from config import config
from database import db
from keyboards import get_main_menu

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Проверяем, является ли пользователь администратором
    if user.id in config.ADMIN_IDS:
        await update.message.reply_text(
            f"🔐 Добро пожаловать, администратор {user.full_name}!\n\n"
            f"Выберите раздел в меню.",
            reply_markup=get_main_menu()
        )
    else:
        # Проверяем, зарегистрирован ли продавец
        # Здесь будет проверка по БД
        await update.message.reply_text(
            f"👋 Добро пожаловать, {user.full_name}!\n\n"
            f"Это бот для складского учета. Выберите действие в меню.",
            reply_markup=get_main_menu()
        )
    
    # Логируем действие
    db.log_action(
        user_id=user.id,
        user_role="admin" if user.id in config.ADMIN_IDS else "unknown",
        action="start",
        details=f"Запуск бота"
    )
    
    return

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений для навигации по меню"""
    text = update.message.text
    
    if text == '📦 Заявка на поставку':
        # Переход к заявкам
        from handlers.seller.orders import orders_start
        return await orders_start(update, context)
    
    elif text == '📤 Отгруженные поставки':
        # Переход к отгрузкам
        from handlers.seller.shipments import shipments_start
        return await shipments_start(update, context)
    
    elif text == '💰 Реализовано':
        # Переход к продажам
        from handlers.seller.sales import sales_start
        return await sales_start(update, context)
    
    elif text == '📊 Остатки':
        # Переход к остаткам
        from handlers.seller.stock import stock_start
        return await stock_start(update, context)
    
    elif text == '📋 Мои заявки':
        # Просмотр своих заявок
        from handlers.seller.orders import my_orders
        return await my_orders(update, context)
    
    elif text == '❌ Отмена':
        await update.message.reply_text(
            "Действие отменено. Выберите пункт меню.",
            reply_markup=get_main_menu()
        )
        return
    
    else:
        await update.message.reply_text(
            "Пожалуйста, используйте кнопки меню.",
            reply_markup=get_main_menu()
        )
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех остальных сообщений"""
    await update.message.reply_text(
        "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.",
        reply_markup=get_main_menu()
    )