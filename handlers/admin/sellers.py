#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import MessageHandler, filters
from config import config
from keyboards import get_admin_menu

async def admin_sellers_start(update: Update, context):
    """Управление продавцами"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    await update.message.reply_text(
        "👥 Управление продавцами\n\n"
        "Раздел в разработке. Здесь можно будет:\n"
        "• Добавлять новых продавцов\n"
        "• Назначать коды продавцам\n"
        "• Блокировать/разблокировать продавцов\n"
        "• Просматривать список продавцов",
        reply_markup=get_admin_menu()
    )

admin_sellers_handler = MessageHandler(filters.Regex('^👥 Управление продавцами$'), admin_sellers_start)
