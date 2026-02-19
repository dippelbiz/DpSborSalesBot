#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import MessageHandler, filters
from config import config
from keyboards import get_main_menu

async def admin_settings_start(update: Update, context):
    user_id = update.effective_user.id
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    await update.message.reply_text(
        "⚙️ Настройки\n\nРаздел в разработке",
        reply_markup=get_main_menu()
    )

admin_settings_conv = MessageHandler(filters.Regex('^Настройки$'), admin_settings_start)
