#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin

# Состояния разговора
MAIN_MENU, ADD_SELLER_CODE, ADD_SELLER_NAME, ADD_SELLER_TG_ID, LIST_SELLERS, EDIT_SELLER, CONFIRM_DELETE = range(7)

async def admin_settings_start(update: Update, context):
    """Главное меню настроек"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    keyboard = [
        [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
        [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ Настройки\n\n"
        "Выберите раздел:",
        reply_markup=reply_markup
    )
    
    return MAIN_MENU

async def settings_sellers(update: Update, context):
    """Меню управления продавцами"""
    query = update.callback_query
    await query.answer()
    
    # Получаем список продавцов
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, seller_code, full_name, telegram_id, is_active 
            FROM sellers 
            ORDER BY seller_code
        """)
        sellers = cursor.fetchall()
    
    text = "👥 Управление продавцами\n\n"
    
    if sellers:
        text += "Список продавцов:\n"
        for seller in sellers:
            status = "🟢" if seller['is_active'] else "🔴"
            tg_status = f"✅ {seller['telegram_id']}" if seller['telegram_id'] else "❌ не активирован"
            text += f"{status} {seller['seller_code']} - {seller['full_name']} ({tg_status})\n"
    else:
        text += "Продавцов пока нет\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить продавца", callback_data="seller_add")],
        [InlineKeyboardButton("📋 Список продавцов", callback_data="seller_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    return MAIN_MENU

async def seller_add_start(update: Update, context):
    """Начало добавления продавца - шаг 1: код"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ Добавление нового продавца - Шаг 1 из 3\n\n"
        "Введите **код** продавца:\n"
        "Код может быть из букв и цифр (например: А, А1, ТЕСТ)\n\n"
        "Или нажмите Отмена",
        parse_mode='Markdown'
    )
    return ADD_SELLER_CODE

async def seller_add_code(update: Update, context):
    """Шаг 2: после ввода кода - ввод имени"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    seller_code = update.message.text.strip().upper()
    
    # Проверяем код
    if len(seller_code) < 1 or len(seller_code) > 5:
        await update.message.reply_text(
            "❌ Код должен быть от 1 до 5 символов\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
            ]])
        )
        return ADD_SELLER_CODE
    
    # Проверяем уникальность кода
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM sellers WHERE seller_code = ?", (seller_code,))
        if cursor.fetchone():
            await update.message.reply_text(
                f"❌ Код {seller_code} уже используется\n"
                f"Введите другой код:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
                ]])
            )
            return ADD_SELLER_CODE
    
    # Сохраняем код в контекст
    context.user_data['new_seller_code'] = seller_code
    
    await update.message.reply_text(
        f"✅ Код принят: {seller_code}\n\n"
        f"Шаг 2 из 3 - Введите **имя** продавца:\n"
        f"Например: Александр Петров",
        parse_mode='Markdown'
    )
    return ADD_SELLER_NAME

async def seller_add_name(update: Update, context):
    """Шаг 3: после ввода имени - ввод Telegram ID"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    seller_name = update.message.text.strip()
    
    if len(seller_name) < 2:
        await update.message.reply_text(
            "❌ Имя должно содержать хотя бы 2 символа\n"
            "Попробуйте снова:"
        )
        return ADD_SELLER_NAME
    
    # Сохраняем имя в контекст
    context.user_data['new_seller_name'] = seller_name
    
    await update.message.reply_text(
        f"✅ Имя принято: {seller_name}\n\n"
        f"Шаг 3 из 3 - Введите **Telegram ID** продавца:\n\n"
        f"Как получить ID:\n"
        f"1. Продавец пишет боту @userinfobot\n"
        f"2. Получает число (например: 123456789)\n"
        f"3. Вы вводите это число сюда\n\n"
        f"Или введите 0, если добавите ID позже",
        parse_mode='Markdown'
    )
    return ADD_SELLER_TG_ID

async def seller_add_tg_id(update: Update, context):
    """Финальный шаг: сохранение продавца"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    tg_id_text = update.message.text.strip()
    
    try:
        if tg_id_text == '0':
            seller_tg_id = None
        else:
            seller_tg_id = int(tg_id_text)
    except ValueError:
        await update.message.reply_text(
            "❌ Telegram ID должен быть числом\n"
            "Попробуйте снова:"
        )
        return ADD_SELLER_TG_ID
    
    # Получаем данные из контекста
    seller_code = context.user_data.get('new_seller_code')
    seller_name = context.user_data.get('new_seller_name')
    
    if not seller_code or not seller_name:
        await update.message.reply_text("❌ Ошибка: данные не найдены. Начните заново.")
        return ConversationHandler.END
    
    # Показываем подтверждение
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="seller_confirm")],
        [InlineKeyboardButton("✏️ Изменить код", callback_data="seller_edit_code")],
        [InlineKeyboardButton("✏️ Изменить имя", callback_data="seller_edit_name")],
        [InlineKeyboardButton("✏️ Изменить Telegram ID", callback_data="seller_edit_tg")],
        [InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    tg_display = seller_tg_id if seller_tg_id else "не указан (можно добавить позже)"
    
    await update.message.reply_text(
        f"Проверьте данные продавца:\n\n"
        f"Код: {seller_code}\n"
        f"Имя: {seller_name}\n"
        f"Telegram ID: {tg_display}\n\n"
        f"Всё верно?",
        reply_markup=reply_markup
    )
    
    # Сохраняем Telegram ID в контекст
    context.user_data['new_seller_tg_id'] = seller_tg_id
    
    return ADD_SELLER_TG_ID

@send_backup_to_admin("добавление продавца")
async def seller_confirm(update: Update, context):
    """Подтверждение добавления продавца"""
    query = update.callback_query
    await query.answer()
    
    seller_code = context.user_data.get('new_seller_code')
    seller_name = context.user_data.get('new_seller_name')
    seller_tg_id = context.user_data.get('new_seller_tg_id')
    
    if not seller_code or not seller_name:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return ConversationHandler.END
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Добавляем продавца
            cursor.execute("""
                INSERT INTO sellers (seller_code, full_name, telegram_id, is_active)
                VALUES (?, ?, ?, 1)
            """, (seller_code, seller_name, seller_tg_id))
            
            # Получаем ID нового продавца
            cursor.execute("SELECT id FROM sellers WHERE seller_code = ?", (seller_code,))
            seller_db_id = cursor.fetchone()[0]
            
            # Создаем записи в seller_products для всех товаров
            cursor.execute("SELECT id FROM products WHERE is_active = 1")
            products = cursor.fetchall()
            
            for product in products:
                cursor.execute("""
                    INSERT INTO seller_products (seller_id, product_id, quantity)
                    VALUES (?, ?, 0)
                """, (seller_db_id, product[0]))
            
            # Инициализируем долг и pending
            cursor.execute("""
                INSERT INTO seller_debt (seller_id, total_debt)
                VALUES (?, 0)
            """, (seller_db_id,))
            
            cursor.execute("""
                INSERT INTO seller_pending (seller_id, pending_amount)
                VALUES (?, 0)
            """, (seller_db_id,))
        
        tg_text = f"Telegram ID: {seller_tg_id}" if seller_tg_id else "Telegram ID не указан"
        
        await query.edit_message_text(
            f"✅ Продавец успешно добавлен!\n\n"
            f"Код: {seller_code}\n"
            f"Имя: {seller_name}\n"
            f"{tg_text}\n\n"
            f"Теперь продавец может активировать аккаунт командой /start",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К продавцам", callback_data="settings_sellers")
            ]])
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
    
    # Очищаем контекст
    context.user_data.clear()
    return MAIN_MENU

async def seller_edit_code(update: Update, context):
    """Редактирование кода продавца"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новый код продавца:"
    )
    return ADD_SELLER_CODE

async def seller_edit_name(update: Update, context):
    """Редактирование имени продавца"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новое имя продавца:"
    )
    return ADD_SELLER_NAME

async def seller_edit_tg(update: Update, context):
    """Редактирование Telegram ID"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новый Telegram ID продавца (или 0, если не указывать):"
    )
    return ADD_SELLER_TG_ID

# ... (остальные функции seller_list, seller_edit, seller_toggle_status, seller_delete, seller_confirm_delete - остаются без изменений)
