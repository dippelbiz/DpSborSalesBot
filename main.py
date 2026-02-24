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
from handlers.common import start, menu_handler, handle_message, activation_conv

# Обработчики продавцов
from handlers.seller.orders import orders_conv, my_orders_handler
from handlers.seller.shipments import shipments_conv
from handlers.seller.sales import sales_conv
from handlers.seller.stock import stock_handler, back_to_main_handler
from handlers.seller.payment import payment_conv
from handlers.seller.restock import restock_conv          # новый импорт

# Обработчики администратора
from handlers.admin.orders import admin_orders_conv
from handlers.admin.payments import admin_payments_conv
from handlers.admin.reports import admin_reports_conv
from handlers.admin.settings import admin_settings_conv
from handlers.admin.backup import manual_backup
from handlers.admin.restore import restore_conv
from handlers.admin.add_test_seller import add_seller_handler
from handlers.admin.restock import restock_admin_conv    # новый импорт

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
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ Отправьте JSON-файл с бэкапом")
        return
    
    document = update.message.document
    if not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ Неверный формат. Отправьте JSON-файл.")
        return
    
    await update.message.reply_text("🔄 Восстановление...")
    
    try:
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        data = json.loads(file_content.decode('utf-8'))
        
        current_backup = backup.create_backup_json()
        current_filename = backup.get_backup_filename("before_emergency_restore")
        await update.message.reply_document(
            document=io.BytesIO(current_backup.encode('utf-8')),
            filename=current_filename,
            caption="📦 Бэкап перед экстренным восстановлением"
        )
        
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        for table in tables:
            table_name = table[0]
            if table_name != 'sqlite_sequence':
                cursor.execute(f"DELETE FROM {table_name}")
        
        restored = 0
        for table_name, rows in data.items():
            if table_name != 'sqlite_sequence' and rows:
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
        
        cursor.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        conn.close()
        
        await update.message.reply_text(f"✅ Восстановлено {restored} записей из {document.file_name}")
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

# === ОТЛАДОЧНЫЙ ОБРАБОТЧИК ВСЕХ КОЛБЭКОВ ===
async def debug_callback(update: Update, context):
    if update.callback_query:
        logger.info(f"🔥 GLOBAL CALLBACK: {update.callback_query.data}")
        await update.callback_query.answer()
    return

# === ФУНКЦИЯ ДЛЯ ЗАПУСКА С ВЕБХУКАМИ ===
async def run_webhook():
    logger.info("Запуск бота с вебхуками...")
    TOKEN = config.BOT_TOKEN
    URL = os.environ.get("RENDER_EXTERNAL_URL")
    PORT = int(os.environ.get("PORT", 10000))
    if not URL:
        logger.error("RENDER_EXTERNAL_URL не установлен!")
        return
    
    application = Application.builder().token(TOKEN).updater(None).build()
    
    # Добавляем отладочный обработчик с самым высоким приоритетом
    application.add_handler(CallbackQueryHandler(debug_callback), group=-1)
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_handler))
    application.add_handler(CommandHandler("backup", manual_backup))
    application.add_handler(CommandHandler("add_seller", add_seller_handler))
    application.add_handler(restore_conv)
    application.add_handler(activation_conv)
    application.add_handler(MessageHandler(filters.Document.ALL, emergency_restore))
    
    # ConversationHandler'ы продавцов
    application.add_handler(orders_conv)
    application.add_handler(shipments_conv)
    application.add_handler(sales_conv)
    application.add_handler(payment_conv)
    application.add_handler(restock_conv)               # заявки на пополнение (продавец)
    
    # ConversationHandler'ы администратора
    application.add_handler(admin_orders_conv)
    application.add_handler(admin_payments_conv)
    application.add_handler(admin_reports_conv)
    application.add_handler(admin_settings_conv)
    application.add_handler(restock_admin_conv)         # обработка заявок (админ)
    
    # Обычные обработчики (MessageHandler и CallbackQueryHandler)
    application.add_handler(my_orders_handler)
    application.add_handler(stock_handler)
    application.add_handler(back_to_main_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    webhook_url = f"{URL}/telegram"
    await application.bot.set_webhook(webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info(f"✅ Вебхук установлен на {webhook_url}")
    
    async def telegram(request):
        try:
            body = await request.json()
            logger.info(f"🔥 Webhook received: {body}")
            update = Update.de_json(body, application.bot)
            await application.update_queue.put(update)
            return Response()
        except Exception as e:
            logger.error(f"Error in webhook: {e}")
            return Response(status=500)
    
    async def healthcheck(request):
        return PlainTextResponse("OK")
    
    starlette_app = Starlette(routes=[
        Route("/telegram", telegram, methods=["POST"]),
        Route("/healthcheck", healthcheck, methods=["GET"]),
    ])
    
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

def main():
    if os.environ.get("RENDER"):
        logger.info("Запуск на Render, используем вебхуки")
        asyncio.run(run_webhook())
    else:
        logger.info("Запуск бота локально (polling)...")
        application = Application.builder().token(config.BOT_TOKEN).build()
        
        application.add_handler(CallbackQueryHandler(debug_callback), group=-1)
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("menu", menu_handler))
        application.add_handler(CommandHandler("backup", manual_backup))
        application.add_handler(CommandHandler("add_seller", add_seller_handler))
        application.add_handler(restore_conv)
        application.add_handler(activation_conv)
        application.add_handler(MessageHandler(filters.Document.ALL, emergency_restore))
        application.add_handler(orders_conv)
        application.add_handler(shipments_conv)
        application.add_handler(sales_conv)
        application.add_handler(payment_conv)
        application.add_handler(restock_conv)
        application.add_handler(admin_orders_conv)
        application.add_handler(admin_payments_conv)
        application.add_handler(admin_reports_conv)
        application.add_handler(admin_settings_conv)
        application.add_handler(restock_admin_conv)
        application.add_handler(my_orders_handler)
        application.add_handler(stock_handler)
        application.add_handler(back_to_main_handler)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        logger.info("✅ Бот запущен и готов к работе (polling)")
        application.run_polling()

if __name__ == '__main__':
    main()
