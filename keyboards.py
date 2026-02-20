#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton

def get_main_menu():
    """Главное меню для продавцов"""
    keyboard = [
        ['📦 Заявка на поставку', '📤 Отгруженные поставки'],
        ['💰 Реализовано', '📊 Остатки'],
        ['📋 Мои заявки']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_menu():
    """Главное меню для администратора"""
    keyboard = [
        ['📦 Управление поставками', '💰 Управление платежами'],
        ['📊 Отчеты', '⚙️ Настройки']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_back_and_cancel_keyboard():
    """Клавиатура с кнопками назад и отмена"""
    keyboard = [['🔙 Назад', '❌ Отмена']]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_confirm_keyboard():
    """Инлайн-клавиатура подтверждения"""
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="edit")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)
