#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Восстановление базы данных из JSON-бэкапа
"""

import json
import sqlite3
import io
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from database import db
from backup import backup
from config import config
from backup_decorator import send_backup_to_admin

# Состояния разговора
WAITING_FOR_FILE, CONFIRM_RESTORE = range(2)

async def restore_start(update: Update, context):
    """Начать процесс восстановления"""
    user_id = update.effective_user.id
    
    # Проверка прав администратора
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("⚠️ Я понимаю риск, продолжить", callback_data="continue")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🔄 **Восстановление базы данных из бэкапа**\n\n"
        "⚠️ **ВНИМАНИЕ!**\n"
        "• Это заменит текущую базу данных\n"
        "• Все несохраненные данные будут потеряны\n"
        "• Перед восстановлением будет создан автоматический бэкап\n\n"
        "Продолжить?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_FILE

async def restore_continue(update: Update, context):
    """Подтверждение восстановления"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Восстановление отменено")
        return ConversationHandler.END
    
    await query.edit_message_text(
        "📤 **Отправьте JSON-файл с бэкапом**\n\n"
        "Это должен быть файл, который бот отправляет при каждом действии.\n"
        "Формат файла: `backup_ГГГГММДД_ЧЧММСС_действие.json`\n\n"
        "Или нажмите /cancel для отмены.",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_FILE

async def receive_backup_file(update: Update, context):
    """Получение и обработка файла бэкапа"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    # Проверяем, что прислали документ
    if not update.message.document:
        await update.message.reply_text(
            "❌ Пожалуйста, отправьте JSON-файл",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Попробовать снова", callback_data="retry")
            ]])
        )
        return WAITING_FOR_FILE
    
    document = update.message.document
    
    # Проверяем расширение файла
    if not document.file_name.endswith('.json'):
        await update.message.reply_text(
            "❌ Неверный формат файла. Ожидается JSON-файл.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Попробовать снова", callback_data="retry")
            ]])
        )
        return WAITING_FOR_FILE
    
    await update.message.reply_text("📥 Скачиваю файл...")
    
    try:
        # Скачиваем файл
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        
        # Парсим JSON
        data = json.loads(file_content.decode('utf-8'))
        
        # Показываем информацию о бэкапе
        info = "📋 **Информация о бэкапе:**\n\n"
        
        # Определяем дату из имени файла или содержимого
        filename_parts = document.file_name.replace('.json', '').split('_')
        if len(filename_parts) >= 3:
            date_str = filename_parts[1] + '_' + filename_parts[2]
            try:
                backup_date = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                info += f"📅 Дата бэкапа: {backup_date.strftime('%d.%m.%Y %H:%M:%S')}\n"
            except:
                pass
        
        # Считаем записи в таблицах
        total_records = 0
        table_counts = {}
        for table_name, rows in data.items():
            if isinstance(rows, list) and table_name != 'sqlite_sequence':
                count = len(rows)
                table_counts[table_name] = count
                total_records += count
        
        info += f"📊 Таблиц: {len(table_counts)}\n"
        info += f"📈 Всего записей: {total_records}\n\n"
        
        # Показываем первые несколько таблиц
        info += "**Таблицы:**\n"
        for table, count in list(table_counts.items())[:10]:
            info += f"• {table}: {count} записей\n"
        
        # Сохраняем данные в контексте
        context.user_data['restore_data'] = data
        context.user_data['restore_filename'] = document.file_name
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, восстановить", callback_data="confirm_restore")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            info + "\n⚠️ **Восстановить базу данных из этого бэкапа?**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        return CONFIRM_RESTORE
        
    except json.JSONDecodeError:
        await update.message.reply_text(
            "❌ Ошибка: файл не является корректным JSON",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Попробовать снова", callback_data="retry")
            ]])
        )
        return WAITING_FOR_FILE
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при чтении файла: {str(e)}")
        return WAITING_FOR_FILE

@send_backup_to_admin("восстановление базы данных")
async def confirm_restore(update: Update, context):
    """Подтверждение восстановления"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Восстановление отменено")
        context.user_data.clear()
        return ConversationHandler.END
    
    if query.data == "confirm_restore":
        data = context.user_data.get('restore_data')
        filename = context.user_data.get('restore_filename', 'unknown')
        
        if not data:
            await query.edit_message_text("❌ Ошибка: данные не найдены")
            return ConversationHandler.END
        
        await query.edit_message_text("🔄 Восстановление базы данных...")
        
        try:
            # Создаем бэкап текущей БД перед восстановлением
            current_backup = backup.create_backup_json()
            current_filename = backup.get_backup_filename("before_restore")
            
            # Отправляем бэкап текущей БД админу
            await query.message.reply_document(
                document=io.BytesIO(current_backup.encode('utf-8')),
                filename=current_filename,
                caption="📦 Автоматический бэкап перед восстановлением"
            )
            
            # Подключаемся к БД
            conn = sqlite3.connect(config.DATABASE_PATH)
            cursor = conn.cursor()
            
            # Отключаем проверку внешних ключей временно
            cursor.execute("PRAGMA foreign_keys = OFF")
            
            # Получаем список всех таблиц
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            # Очищаем все таблицы (кроме sqlite_sequence)
            for table in tables:
                table_name = table[0]
                if table_name != 'sqlite_sequence':
                    cursor.execute(f"DELETE FROM {table_name}")
            
            # Восстанавливаем данные из бэкапа
            restored_count = 0
            for table_name, rows in data.items():
                if table_name != 'sqlite_sequence' and rows:
                    # Получаем список колонок
                    columns = list(rows[0].keys())
                    placeholders = ','.join(['?'] * len(columns))
                    column_names = ','.join(columns)
                    
                    for row in rows:
                        values = [row[col] for col in columns]
                        try:
                            cursor.execute(
                                f"INSERT INTO {table_name} ({column_names}) VALUES ({placeholders})",
                                values
                            )
                            restored_count += 1
                        except sqlite3.IntegrityError as e:
                            # Пропускаем дубликаты
                            print(f"Ошибка вставки в {table_name}: {e}")
                            continue
            
            # Включаем обратно проверку внешних ключей
            cursor.execute("PRAGMA foreign_keys = ON")
            
            conn.commit()
            conn.close()
            
            # Логируем действие
            db.log_action(
                user_id=update.effective_user.id,
                user_role="admin",
                action="restore_backup",
                details=f"Восстановлено из {filename}, записей: {restored_count}"
            )
            
            await query.edit_message_text(
                f"✅ **База данных успешно восстановлена!**\n\n"
                f"📁 Файл: {filename}\n"
                f"📊 Восстановлено записей: {restored_count}\n"
                f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка восстановления: {str(e)}")
        
        # Очищаем данные
        context.user_data.clear()
        
        return ConversationHandler.END

async def retry(update: Update, context):
    """Повторить попытку"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📤 **Отправьте JSON-файл с бэкапом**\n\n"
        "Файл должен быть в формате: `backup_ГГГГММДД_ЧЧММСС_действие.json`",
        parse_mode='Markdown'
    )
    
    return WAITING_FOR_FILE

async def cancel(update: Update, context):
    """Отмена восстановления"""
    await update.message.reply_text("❌ Восстановление отменено")
    context.user_data.clear()
    return ConversationHandler.END

# Обработчик разговора для восстановления
restore_conv = ConversationHandler(
    entry_points=[CommandHandler("restore", restore_start)],
    states={
        WAITING_FOR_FILE: [
            CallbackQueryHandler(restore_continue, pattern='^(continue|retry|cancel)$'),
            MessageHandler(filters.Document.ALL, receive_backup_file),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_backup_file)
        ],
        CONFIRM_RESTORE: [
            CallbackQueryHandler(confirm_restore, pattern='^(confirm_restore|cancel)$')
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)
