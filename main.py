#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Основной файл бота для складского учета
"""

import logging
import json
import sqlite3
import io
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.ext import ConversationHandler

from config import config
from database import db
from backup import backup
from backup_decorator import send_backup_to_admin
from keyboards import get_main_menu

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
from handlers.admin.reports import admin_reports_conv  # ← ИЗМЕНЕНО: было handler, стало conv
from handlers.admin.settings import admin_settings_conv
from handlers.admin.backup import manual_backup
from handlers.admin.restore import restore_conv

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
    
    # Экстренное восстановление (обработка файлов)
    application.add_handler(MessageHandler(filters.Document.ALL, emergency_restore))
    
    # Обработчики продавцов
    application.add_handler(orders_conv)  # Заявки на поставку
    application.add_handler(shipments_handler)  # Отгруженные поставки
    application.add_handler(sales_conv)  # Реализовано
    application.add_handler(MessageHandler(filters.Regex('^(Остатки)$'), stock_handler))
    
    # Обработчики администратора
    application.add_handler(admin_orders_conv)
    application.add_handler(admin_payments_conv)
    application.add_handler(admin_reports_conv)  # ← ИЗМЕНЕНО: было handler, стало conv
    application.add_handler(admin_settings_conv)
    
    # Обработчик всех остальных сообщений (должен быть ПОСЛЕДНИМ)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    logger.info("Бот запущен и готов к работе")
    application.run_polling()

if __name__ == '__main__':
    main()
