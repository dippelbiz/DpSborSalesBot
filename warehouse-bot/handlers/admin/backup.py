#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import CommandHandler
import io
from datetime import datetime

from backup import backup
from config import config

async def manual_backup(update: Update, context):
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    await update.message.reply_text("🔄 Создание бэкапа...")
    
    try:
        json_data = backup.create_backup_json()
        filename = backup.get_backup_filename("manual")
        
        await update.message.reply_document(
            document=io.BytesIO(json_data.encode('utf-8')),
            filename=filename,
            caption=f"✅ Ручной бэкап\n"
                   f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")