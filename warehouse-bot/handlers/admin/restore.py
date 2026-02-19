#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import CommandHandler
from config import config

async def restore_start(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    await update.message.reply_text(
        "🔄 Восстановление из бэкапа\n\n"
        "Используйте команду /backup для создания бэкапа.\n"
        "Функция восстановления в разработке."
    )

restore_handler = CommandHandler("restore", restore_start)
