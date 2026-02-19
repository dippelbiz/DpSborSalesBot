#!/usr/bin/env python
# -*- coding: utf-8 -*-

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

WAITING_FOR_FILE, CONFIRM_RESTORE = range(2)

async def restore_start(update: Update, context):
    user_id = update.effective_user.id
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
        "⚠️ **ВНИМАНИЕ!** Это заменит текущую базу данных.\n\n"
        "Продолжить?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return WAITING_FOR_FILE

async def restore_continue(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Восстановление отменено")
        return ConversationHandler.END
    
    await query.edit_message_text(
        "📤 **Отправьте JSON-файл с бэкапом**\n\n"
        "Файл должен быть в формате: backup_ГГГГММДД_ЧЧММСС_действие.json"
    )
    return WAITING_FOR_FILE

async def receive_backup_file(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    if not update.message.document:
        await update.message.reply_text("❌ Отправьте JSON-файл")
        return WAITING_FOR_FILE
    
    document = update.message.document
    if not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ Неверный формат. Ожидается JSON-файл.")
        return WAITING_FOR_FILE
    
    await update.message.reply_text("📥 Скачиваю файл...")
    
    try:
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        data = json.loads(file_content.decode('utf-8'))
        
        context.user_data['restore_data'] = data
        context.user_data['restore_filename'] = document.file_name
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, восстановить", callback_data="confirm_restore")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Файл загружен. Восстановить?",
            reply_markup=reply_markup
        )
        return CONFIRM_RESTORE
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        return WAITING_FOR_FILE

@send_backup_to_admin("восстановление базы данных")
async def confirm_restore(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_text("❌ Восстановление отменено")
        return ConversationHandler.END
    
    data = context.user_data.get('restore_data')
    if not data:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return ConversationHandler.END
    
    await query.edit_message_text("🔄 Восстановление...")
    
    try:
        current_backup = backup.create_backup_json()
        current_filename = backup.get_backup_filename("before_restore")
        await query.message.reply_document(
            document=io.BytesIO(current_backup.encode('utf-8')),
            filename=current_filename,
            caption="📦 Бэкап перед восстановлением"
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
        
        await query.edit_message_text(f"✅ Восстановлено {restored} записей!")
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {str(e)}")
    
    return ConversationHandler.END

restore_conv = ConversationHandler(
    entry_points=[CommandHandler("restore", restore_start)],
    states={
        WAITING_FOR_FILE: [
            CallbackQueryHandler(restore_continue, pattern='^(continue|cancel)$'),
            MessageHandler(filters.Document.ALL, receive_backup_file)
        ],
        CONFIRM_RESTORE: [
            CallbackQueryHandler(confirm_restore, pattern='^(confirm_restore|cancel)$')
        ]
    },
    fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
)
