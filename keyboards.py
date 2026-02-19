#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu():
    keyboard = [
        ['📦 Заявка на поставку', '📤 Отгруженные поставки'],
        ['💰 Реализовано', '📊 Остатки'],
        ['📋 Мои заявки']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_and_cancel_keyboard():
    keyboard = [['🔙 Назад', '❌ Отмена']]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_confirm_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="edit")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)
