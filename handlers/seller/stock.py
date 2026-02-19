#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import MessageHandler, filters
from keyboards import get_main_menu

async def stock_start(update: Update, context):
    await update.message.reply_text(
        "📊 Раздел в разработке",
        reply_markup=get_main_menu()
    )

stock_handler = MessageHandler(filters.Regex('^📊 Остатки$'), stock_start)