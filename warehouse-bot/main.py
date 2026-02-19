#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Основной файл бота для складского учета
"""

import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.ext import ConversationHandler

from config import config
from database import db

# Общие обработчики
from handlers.common import start, menu_handler, handle_message

# Обработчики продавцов
from handlers.seller.orders import orders_conv
from handlers.seller.shipments import shipments_handler
from handlers.seller.sales import sales_conv
from handlers.seller.stock import stock_handler

# Обработчики администратора
from handlers.admin.orders import admin_orders_conv
from handlers.admin.payments import admin_payments_conv
from handlers.admin.reports import admin_reports_handler
from handlers.admin.settings import admin_settings_conv
from handlers.admin.backup import manual_backup
from handlers.admin.restore import restore_conv

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    """Запуск бота"""
    logger.info("Запуск бота...")
    
    # Создаем приложение
    application = Application.builder().token(config.BOT_TOKEN).build()
    
    # Базовые команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_handler))
    
    # Команды для бэкапов (только для админов)
    application.add_handler(CommandHandler("backup", manual_backup))
    application.add_handler(restore_conv)
    
    # Обработчики продавцов
    application.add_handler(orders_conv)  # Заявки на поставку
    application.add_handler(shipments_handler)  # Отгруженные поставки
    application.add_handler(sales_conv)  # Реализовано
    application.add_handler(MessageHandler(filters.Regex('^(Остатки)$'), stock_handler))
    
    # Обработчики администратора
    application.add_handler(admin_orders_conv)
    application.add_handler(admin_payments_conv)
    application.add_handler(admin_reports_handler)
    application.add_handler(admin_settings_conv)
    
    # Обработчик всех остальных сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    logger.info("Бот запущен и готов к работе")
    application.run_polling()

if __name__ == '__main__':
    main()