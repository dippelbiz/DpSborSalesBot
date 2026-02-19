#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Основной файл бота для складского учета - версия с вебхуками для Render
"""

import logging
import json
import sqlite3
import io
import os
import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.ext import ConversationHandler

# Добавляем импорты для веб-сервера
from starlette.applications import Starlette
from starlette.responses import Response, PlainTextResponse
from starlette.routing import Route
import uvicorn

from config import config
from database import db
from backup import backup
from backup_decorator import send_backup_to_admin
from keyboards import get_main_menu, get_admin_menu

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
from handlers.admin.reports import admin_reports_conv
from handlers.admin.settings import admin_settings_conv
from handlers.admin.sellers import admin_sellers_handler
from handlers.admin.backup import manual_backup
from handlers.admin.restore import restore_conv
from handlers.admin.add_test_seller import add_seller_handler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === ЭКСТРЕННОЕ ВОССТАНОВЛЕНИЕ ===
async def emergency_restore(update: Update, context):
    """Экстренное восстановление из последнего бэкапа в чате"""
    user_id = update.effective_user.id
    
    # Проверка прав администратора
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    # Проверяем, что сообщение - это документ
    if not update.message.document:
        await update.message.reply_text(
            "❌ Отправьте JSON-файл с бэкапом"
        )
        return
    
    document = update.message.document
    
    # Проверяем расширение файла
    if not document.file_name.endswith('.json'):
        await update.message.reply_text(
            "❌ Неверный формат. Отправьте JSON-файл."
        )
        return
    
    await update.message.reply_text("🔄 Восстановление...")
    
    try:
        # Скачиваем файл
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        
        # Парсим JSON
        data = json.loads(file_content.decode('utf-8'))
        
        # Создаем бэкап текущей БД
        current_backup = backup.create_backup_json()
        current_filename = backup.get_backup_filename("before_emergency_restore")
        
        # Отправляем бэкап текущей БД
        await update.message.reply_document(
            document=io.BytesIO(current_backup.encode('utf-8')),
            filename=current_filename,
            caption="📦 Бэкап перед экстренным восстановлением"
        )
        
        # Восстанавливаем
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        
        # Отключаем проверку внешних ключей
        cursor.execute("PRAGMA foreign_keys = OFF")
        
        # Очищаем таблицы
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        for table in tables:
            table_name = table[0]
            if table_name != 'sqlite_sequence':
                cursor.execute(f"DELETE FROM {table_name}")
        
        # Вставляем данные из бэкапа
        restored = 0
        for table_name, rows in data.items():
            if table_name != 'sqlite_sequence' and rows:
                # Получаем список колонок из первой записи
                columns = list(rows[0].keys())
                placeholders = ','.join(['?'] * len(columns))
                column_names = ','.join(columns)
                
                for row in rows:
                    values = [row[col] for col in columns]
                    cursor.execute(
                        f"INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})",
                        values
                    )
                    restored += 1
        
        # Включаем обратно проверку внешних ключей
        cursor.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            f"✅ Восстановлено {restored} записей из {document.file_name}"
        )
        
        # Логируем действие
        db.log_action(
            user_id=user_id,
            user_role="admin",
            action="emergency_restore",
            details=f"Восстановлено из {document.file_name}, записей: {restored}"
        )
        
    except json.JSONDecodeError:
        await update.message.reply_text("❌ Ошибка: файл не является корректным JSON")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка восстановления: {str(e)}")
# === КОНЕЦ БЛОКА ЭКСТРЕННОГО ВОССТАНОВЛЕНИЯ ===

# === ФУНКЦИЯ ДЛЯ ЗАПУСКА С ВЕБХУКАМИ ===
async def run_webhook():
    """Запуск бота с вебхуками для Render"""
    logger.info("Запуск бота с вебхуками...")
    
    # Получаем переменные окружения
    TOKEN = config.BOT_TOKEN
    URL = os.environ.get("RENDER_EXTERNAL_URL")  # Render сам подставляет этот URL
    PORT = int(os.environ.get("PORT", 10000))  # Render использует порт 10000
    
    if not URL:
        logger.error("RENDER_EXTERNAL_URL не установлен!")
        return
    
    # Создаем приложение без встроенного Updater
    application = Application.builder().token(TOKEN).updater(None).build()
    
    # Добавляем все обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_handler))
    application.add_handler(CommandHandler("backup", manual_backup))
    application.add_handler(CommandHandler("add_seller", add_seller_handler))
    application.add_handler(restore_conv)
    application.add_handler(MessageHandler(filters.Document.ALL, emergency_restore))
    application.add_handler(orders_conv)
    application.add_handler(shipments_handler)
    application.add_handler(sales_conv)
    application.add_handler(MessageHandler(filters.Regex('^(Остатки)$'), stock_handler))
    application.add_handler(admin_orders_conv)
    application.add_handler(admin_payments_conv)
    application.add_handler(admin_reports_conv)
    application.add_handler(admin_settings_conv)
    application.add_handler(admin_sellers_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Устанавливаем вебхук
    webhook_url = f"{URL}/telegram"
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info(f"✅ Вебхук установлен на {webhook_url}")
    
    # Создаем Starlette приложение для обработки вебхуков
    async def telegram(request):
        """Обработка входящих вебхуков от Telegram"""
        update_data = await request.json()
        update = Update.de_json(update_data, application.bot)
        await application.update_queue.put(update)
        return Response()
    
    async def healthcheck(request):
        """Эндпоинт для проверки здоровья Render"""
        return PlainTextResponse("OK")
    
    starlette_app = Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ])
    
    # Запускаем веб-сервер
    logger.info(f"Запуск веб-сервера на порту {PORT}")
    server = uvicorn.Server(
        uvicorn.Config(
            app=starlette_app,
            host="0.0.0.0",
            port=PORT,
            log_level="info"
        )
    )
    
    async with application:
        await application.start()
        await server.serve()
        await application.stop()
# === КОНЕЦ ФУНКЦИИ ===

def main():
    """Запуск бота"""
    # На Render используем вебхуки
    if os.environ.get("RENDER"):
        logger.info("Запуск на Render, используем вебхуки")
        asyncio.run(run_webhook())
    else:
        # Локально используем polling
        logger.info("Запуск бота локально (polling)...")
        application = Application.builder().token(config.BOT_TOKEN).build()
        
        # Добавляем все обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("menu", menu_handler))
        application.add_handler(CommandHandler("backup", manual_backup))
        application.add_handler(CommandHandler("add_seller", add_seller_handler))
        application.add_handler(restore_conv)
        application.add_handler(MessageHandler(filters.Document.ALL, emergency_restore))
        application.add_handler(orders_conv)
        application.add_handler(shipments_handler)
        application.add_handler(sales_conv)
        application.add_handler(MessageHandler(filters.Regex('^(Остатки)$'), stock_handler))
        application.add_handler(admin_orders_conv)
        application.add_handler(admin_payments_conv)
        application.add_handler(admin_reports_conv)
        application.add_handler(admin_settings_conv)
        application.add_handler(admin_sellers_handler)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("✅ Бот запущен и готов к работе (polling)")
        application.run_polling()

if __name__ == '__main__':
    main()
