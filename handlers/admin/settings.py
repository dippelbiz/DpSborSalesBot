#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin
import logging
import io
import json
import sqlite3
from backup import backup

logger = logging.getLogger(__name__)

# Состояния разговора (добавлены BACKUP_MENU и WAITING_FOR_BACKUP_FILE)
MAIN_MENU, ADD_SELLER_CODE, ADD_SELLER_NAME, ADD_SELLER_TG_ID, LIST_SELLERS, EDIT_SELLER, CONFIRM_DELETE, PRODUCTS_MENU, ADD_PRODUCT_NAME, ADD_PRODUCT_PRICE, ADD_PRODUCT_CONFIRM, EDIT_PRODUCT, BACKUP_MENU, WAITING_FOR_BACKUP_FILE = range(14)

async def admin_settings_start(update: Update, context):
    """Главное меню настроек"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
        [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
        [InlineKeyboardButton("🔐 Бэкапы", callback_data="settings_backup")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "⚙️ Настройки\n\n"
        "Выберите раздел:",
        reply_markup=reply_markup
    )
    
    return MAIN_MENU

# ============================================
# УПРАВЛЕНИЕ ПРОДАВЦАМИ (без изменений)
# ============================================

async def settings_sellers(update: Update, context):
    """Меню управления продавцами"""
    query = update.callback_query
    await query.answer()
    
    # Очищаем старые данные
    keys = ['new_seller_code', 'new_seller_name', 'new_seller_tg_id', 'edit_seller_id']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]
    
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
    
    # Очищаем старые данные
    keys = ['new_seller_code', 'new_seller_name', 'new_seller_tg_id']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]
    
    await query.edit_message_text(
        "➕ Добавление нового продавца - Шаг 1 из 3\n\n"
        "Введите **код** продавца:\n"
        "Код может быть из букв и цифр (например: А, А1, ТЕСТ)\n\n"
        "Или нажмите Отмена",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
        ]])
    )
    return ADD_SELLER_CODE

async def seller_add_code(update: Update, context):
    """Шаг 1: обработка ввода кода"""
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
    """Шаг 2: обработка ввода имени"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    seller_name = update.message.text.strip()
    
    if len(seller_name) < 2:
        await update.message.reply_text(
            "❌ Имя должно содержать хотя бы 2 символа\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
            ]])
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
    """Шаг 3: обработка ввода Telegram ID и подтверждение"""
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
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
            ]])
        )
        return ADD_SELLER_TG_ID
    
    # Получаем данные из контекста
    seller_code = context.user_data.get('new_seller_code')
    seller_name = context.user_data.get('new_seller_name')
    
    if not seller_code or not seller_name:
        await update.message.reply_text("❌ Ошибка: данные не найдены. Начните заново.")
        return ConversationHandler.END
    
    # Сохраняем Telegram ID в контекст
    context.user_data['new_seller_tg_id'] = seller_tg_id
    
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
        return MAIN_MENU
    
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
        
        # Очищаем данные из контекста
        keys = ['new_seller_code', 'new_seller_name', 'new_seller_tg_id']
        for key in keys:
            if key in context.user_data:
                del context.user_data[key]
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tg_text = f"Telegram ID: {seller_tg_id}" if seller_tg_id else "Telegram ID не указан"
        
        await query.edit_message_text(
            f"✅ Продавец успешно добавлен!\n\n"
            f"Код: {seller_code}\n"
            f"Имя: {seller_name}\n"
            f"{tg_text}\n\n"
            f"Выберите раздел:",
            reply_markup=reply_markup
        )
        
        return MAIN_MENU
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return MAIN_MENU

async def seller_edit_code(update: Update, context):
    """Редактирование кода продавца"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новый код продавца:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
        ]])
    )
    return ADD_SELLER_CODE

async def seller_edit_name(update: Update, context):
    """Редактирование имени продавца"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новое имя продавца:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
        ]])
    )
    return ADD_SELLER_NAME

async def seller_edit_tg(update: Update, context):
    """Редактирование Telegram ID"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новый Telegram ID продавца (или 0, если не указывать):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
        ]])
    )
    return ADD_SELLER_TG_ID

async def seller_list(update: Update, context):
    """Просмотр списка продавцов"""
    query = update.callback_query
    await query.answer()
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, seller_code, full_name, telegram_id, is_active,
                   (SELECT COUNT(*) FROM orders WHERE seller_id = sellers.id) as orders_count
            FROM sellers 
            ORDER BY seller_code
        """)
        sellers = cursor.fetchall()
    
    if not sellers:
        await query.edit_message_text(
            "📭 Продавцов пока нет",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➕ Добавить", callback_data="seller_add"),
                InlineKeyboardButton("🔙 Назад", callback_data="settings_sellers")
            ]])
        )
        return MAIN_MENU
    
    text = "📋 Список продавцов:\n\n"
    keyboard = []
    
    for seller in sellers:
        status = "🟢 Активен" if seller['is_active'] else "🔴 Заблокирован"
        tg = f"✅ {seller['telegram_id']}" if seller['telegram_id'] else "❌ не активирован"
        text += f"{seller['seller_code']} - {seller['full_name']}\n"
        text += f"   {status}, {tg}\n"
        text += f"   Заявок: {seller['orders_count']}\n\n"
        keyboard.append([InlineKeyboardButton(
            f"✏️ {seller['seller_code']} - {seller['full_name'][:15]}",
            callback_data=f"seller_edit_{seller['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="settings_sellers")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return EDIT_SELLER

async def seller_edit(update: Update, context):
    """Редактирование продавца"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith('seller_edit_'):
        seller_id = int(query.data.replace('seller_edit_', ''))
        context.user_data['edit_seller_id'] = seller_id
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sellers WHERE id = ?", (seller_id,))
            seller = cursor.fetchone()
        
        if not seller:
            await query.edit_message_text("❌ Продавец не найден")
            return MAIN_MENU
        
        keyboard = [
            [InlineKeyboardButton("🔄 Сменить статус", callback_data="seller_toggle_status")],
            [InlineKeyboardButton("❌ Удалить", callback_data="seller_delete")],
            [InlineKeyboardButton("🔙 Назад", callback_data="seller_list")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_text = "Активен" if seller['is_active'] else "Заблокирован"
        tg_text = f"Telegram ID: {seller['telegram_id']}" if seller['telegram_id'] else "❌ Не активирован"
        
        await query.edit_message_text(
            f"✏️ Редактирование продавца\n\n"
            f"Код: {seller['seller_code']}\n"
            f"Имя: {seller['full_name']}\n"
            f"Статус: {status_text}\n"
            f"{tg_text}\n\n"
            f"Выберите действие:",
            reply_markup=reply_markup
        )
        return EDIT_SELLER
    
    elif query.data == "seller_list":
        return await seller_list(update, context)

async def seller_toggle_status(update: Update, context):
    """Смена статуса продавца (активен/заблокирован)"""
    query = update.callback_query
    await query.answer()
    
    seller_id = context.user_data.get('edit_seller_id')
    
    if not seller_id:
        await query.edit_message_text("❌ Ошибка: продавец не выбран")
        return MAIN_MENU
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, seller_code FROM sellers WHERE id = ?", (seller_id,))
        current = cursor.fetchone()
        
        if current:
            new_status = 0 if current['is_active'] else 1
            cursor.execute("UPDATE sellers SET is_active = ? WHERE id = ?", (new_status, seller_id))
            status_text = "разблокирован" if new_status else "заблокирован"
            seller_code = current['seller_code']
    
    # Очищаем данные
    if 'edit_seller_id' in context.user_data:
        del context.user_data['edit_seller_id']
    
    # Возвращаемся в главное меню настроек
    keyboard = [
        [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
        [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
        [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✅ Продавец {seller_code} {status_text}\n\n"
        f"Выберите раздел:",
        reply_markup=reply_markup
    )
    
    return MAIN_MENU

async def seller_delete(update: Update, context):
    """Подтверждение удаления продавца"""
    query = update.callback_query
    await query.answer()
    
    seller_id = context.user_data.get('edit_seller_id')
    
    if not seller_id:
        await query.edit_message_text("❌ Ошибка: продавец не выбран")
        return MAIN_MENU
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data="seller_confirm_delete")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="seller_list")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚠️ Вы уверены, что хотите удалить продавца?\n"
        "Это действие необратимо!",
        reply_markup=reply_markup
    )
    return CONFIRM_DELETE

@send_backup_to_admin("удаление продавца")
async def seller_confirm_delete(update: Update, context):
    """Окончательное удаление продавца"""
    query = update.callback_query
    await query.answer()
    
    seller_id = context.user_data.get('edit_seller_id')
    
    if not seller_id:
        await query.edit_message_text("❌ Ошибка: продавец не выбран")
        return MAIN_MENU
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Получаем код продавца для сообщения
            cursor.execute("SELECT seller_code FROM sellers WHERE id = ?", (seller_id,))
            seller_code = cursor.fetchone()[0]
            
            # Удаляем связанные записи
            cursor.execute("DELETE FROM seller_products WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM seller_debt WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM seller_pending WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM sellers WHERE id = ?", (seller_id,))
        
        # Очищаем данные
        if 'edit_seller_id' in context.user_data:
            del context.user_data['edit_seller_id']
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ Продавец {seller_code} удален\n\n"
            f"Выберите раздел:",
            reply_markup=reply_markup
        )
        
        return MAIN_MENU
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {e}")
        return MAIN_MENU

async def seller_cancel(update: Update, context):
    """Отмена действия с продавцами"""
    query = update.callback_query
    if query:
        await query.answer()
        
        # Очищаем данные о продавце
        keys = ['new_seller_code', 'new_seller_name', 'new_seller_tg_id', 'edit_seller_id']
        for key in keys:
            if key in context.user_data:
                del context.user_data[key]
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "❌ Действие отменено\n\n"
            "Выберите раздел:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=get_admin_menu()
        )
    
    return MAIN_MENU

# ============================================
# ТОВАРЫ И ЦЕНЫ
# ============================================

async def settings_products(update: Update, context):
    """Меню управления товарами и ценами"""
    query = update.callback_query
    await query.answer()
    
    # Очищаем данные о товарах при входе
    keys = ['edit_product_id', 'new_product_name', 'new_product_price', 'editing_field']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]
    
    # Получаем список товаров
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, product_name, price
            FROM products 
            ORDER BY product_name
        """)
        products = cursor.fetchall()
    
    text = "🏷️ Товары и цены\n\n"
    text += "Список товаров:\n"
    
    keyboard = []
    for product in products:
        text += f"• {product['product_name']}: {product['price']} руб\n"
        keyboard.append([InlineKeyboardButton(
            f"✏️ {product['product_name']} ({product['price']} руб)",
            callback_data=f"product_edit_{product['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ Добавить товар", callback_data="product_add")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="settings_back_to_main")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return PRODUCTS_MENU

async def product_add_start(update: Update, context):
    """Начало добавления нового товара - шаг 1: название"""
    query = update.callback_query
    await query.answer()
    
    # Очищаем старые данные
    keys = ['new_product_name', 'new_product_price']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]
    
    await query.edit_message_text(
        "➕ Добавление нового товара - Шаг 1 из 2\n\n"
        "Введите **название** товара:\n"
        "Например: Ананас, Груша, Лимон\n\n"
        "Или нажмите Отмена",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
        ]])
    )
    return ADD_PRODUCT_NAME

async def product_add_name(update: Update, context):
    """Шаг 1: обработка ввода названия товара"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    product_name = update.message.text.strip()
    
    if len(product_name) < 2:
        await update.message.reply_text(
            "❌ Название должно содержать хотя бы 2 символа\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
            ]])
        )
        return ADD_PRODUCT_NAME
    
    # Проверяем уникальность названия
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM products WHERE product_name = ?", (product_name,))
        if cursor.fetchone():
            await update.message.reply_text(
                f"❌ Товар '{product_name}' уже существует\n"
                f"Введите другое название:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
                ]])
            )
            return ADD_PRODUCT_NAME
    
    # Сохраняем название в контекст
    context.user_data['new_product_name'] = product_name
    
    await update.message.reply_text(
        f"✅ Название принято: {product_name}\n\n"
        f"Шаг 2 из 2 - Введите **цену** товара (в рублях):\n"
        f"Например: 250, 300, 150",
        parse_mode='Markdown'
    )
    return ADD_PRODUCT_PRICE

async def product_add_price(update: Update, context):
    """Шаг 2: обработка ввода цены"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    price_text = update.message.text.strip()
    
    try:
        price = int(price_text)
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Цена должна быть положительным числом\n"
            "Попробуйте снова:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
            ]])
        )
        return ADD_PRODUCT_PRICE
    
    product_name = context.user_data.get('new_product_name')
    
    if not product_name:
        await update.message.reply_text(
            "❌ Ошибка: название не найдено. Начните заново.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    context.user_data['new_product_price'] = price
    
    # Показываем подтверждение
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="product_confirm")],
        [InlineKeyboardButton("✏️ Изменить название", callback_data="product_edit_name")],
        [InlineKeyboardButton("✏️ Изменить цену", callback_data="product_edit_price")],
        [InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Проверьте данные товара:\n\n"
        f"Название: {product_name}\n"
        f"Цена: {price} руб\n\n"
        f"Всё верно?",
        reply_markup=reply_markup
    )
    
    return ADD_PRODUCT_CONFIRM

@send_backup_to_admin("добавление товара")
async def product_confirm(update: Update, context):
    """Подтверждение добавления товара"""
    query = update.callback_query
    await query.answer()
    
    product_name = context.user_data.get('new_product_name')
    product_price = context.user_data.get('new_product_price')
    
    if not product_name or not product_price:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return PRODUCTS_MENU
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Добавляем товар
            cursor.execute("""
                INSERT INTO products (product_name, price, is_active)
                VALUES (?, ?, 1)
            """, (product_name, product_price))
            
            # Получаем ID нового товара
            cursor.execute("SELECT id FROM products WHERE product_name = ?", (product_name,))
            product_id = cursor.fetchone()[0]
            
            # Добавляем товар всем существующим продавцам
            cursor.execute("SELECT id FROM sellers WHERE is_active = 1")
            sellers = cursor.fetchall()
            
            for seller in sellers:
                cursor.execute("""
                    INSERT INTO seller_products (seller_id, product_id, quantity)
                    VALUES (?, ?, 0)
                """, (seller['id'], product_id))
            
            # Добавляем запись в central_stock
            cursor.execute("INSERT INTO central_stock (product_id, quantity) VALUES (?, 0)", (product_id,))
        
        # Очищаем данные
        keys = ['new_product_name', 'new_product_price']
        for key in keys:
            if key in context.user_data:
                del context.user_data[key]
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ Товар успешно добавлен!\n\n"
            f"Название: {product_name}\n"
            f"Цена: {product_price} руб\n\n"
            f"Товар добавлен всем продавцам и на центральный склад.\n\n"
            f"Выберите раздел:",
            reply_markup=reply_markup
        )
        
        return MAIN_MENU
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return MAIN_MENU

async def product_edit_name(update: Update, context):
    """Возврат к редактированию названия"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новое название товара:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
        ]])
    )
    return ADD_PRODUCT_NAME

async def product_edit_price(update: Update, context):
    """Возврат к редактированию цены"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "Введите новую цену товара (в рублях):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel")
        ]])
    )
    return ADD_PRODUCT_PRICE

async def product_edit_start(update: Update, context):
    """Редактирование товара - меню выбора действия"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.replace('product_edit_', ''))
    
    # Очищаем старые данные
    keys = ['edit_product_id', 'editing_field']
    for key in keys:
        if key in context.user_data:
            del context.user_data[key]
    
    context.user_data['edit_product_id'] = product_id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
    
    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return PRODUCTS_MENU
    
    keyboard = [
        [InlineKeyboardButton("💰 Изменить цену", callback_data="product_change_price")],
        [InlineKeyboardButton("📝 Изменить название", callback_data="product_change_name")],
        [InlineKeyboardButton("❌ Удалить", callback_data="product_delete")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_products")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✏️ Редактирование товара\n\n"
        f"Текущее название: {product['product_name']}\n"
        f"Текущая цена: {product['price']} руб\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return EDIT_PRODUCT

async def product_change_price(update: Update, context):
    """Изменение цены товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id:
        await query.edit_message_text(
            "❌ Ошибка: товар не выбран",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    # Сохраняем тип редактирования
    context.user_data['editing_field'] = 'price'
    
    await query.edit_message_text(
        f"💰 Введите новую цену товара (в рублях):\n\n"
        f"(Для отмены нажмите кнопку ниже)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
        ]])
    )
    return EDIT_PRODUCT

async def product_change_name(update: Update, context):
    """Изменение названия товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id:
        await query.edit_message_text(
            "❌ Ошибка: товар не выбран",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    # Сохраняем тип редактирования
    context.user_data['editing_field'] = 'name'
    
    await query.edit_message_text(
        f"📝 Введите новое название товара:\n\n"
        f"(Для отмены нажмите кнопку ниже)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
        ]])
    )
    return EDIT_PRODUCT

@send_backup_to_admin("изменение товара")
async def product_update_field(update: Update, context):
    """Обновление поля товара (цены или названия)"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    editing_field = context.user_data.get('editing_field')
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id or not editing_field:
        await update.message.reply_text(
            "❌ Ошибка: данные не найдены. Начните заново.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    new_value = update.message.text.strip()
    
    if editing_field == 'price':
        # Изменение цены
        try:
            price = int(new_value)
            if price <= 0:
                raise ValueError
            
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
                result = cursor.fetchone()
                
                if not result:
                    await update.message.reply_text("❌ Товар не найден")
                    return PRODUCTS_MENU
                
                product_name = result[0]
                cursor.execute("UPDATE products SET price = ? WHERE id = ?", (price, product_id))
            
            # Очищаем данные
            keys = ['edit_product_id', 'editing_field']
            for key in keys:
                if key in context.user_data:
                    del context.user_data[key]
            
            # Возвращаемся в главное меню настроек
            keyboard = [
                [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
                [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
                [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ Цена товара '{product_name}' обновлена до {price} руб\n\n"
                f"Выберите раздел:",
                reply_markup=reply_markup
            )
            
            return MAIN_MENU
            
        except ValueError:
            await update.message.reply_text(
                "❌ Цена должна быть положительным числом\n"
                "Попробуйте снова:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
                ]])
            )
            return EDIT_PRODUCT
    
    elif editing_field == 'name':
        # Изменение названия
        if len(new_value) < 2:
            await update.message.reply_text(
                "❌ Название должно содержать хотя бы 2 символа\n"
                "Попробуйте снова:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
                ]])
            )
            return EDIT_PRODUCT
        
        # Проверяем уникальность названия
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM products WHERE product_name = ? AND id != ?", (new_value, product_id))
            if cursor.fetchone():
                await update.message.reply_text(
                    f"❌ Товар с названием '{new_value}' уже существует\n"
                    f"Введите другое название:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
                    ]])
                )
                return EDIT_PRODUCT
            
            cursor.execute("UPDATE products SET product_name = ? WHERE id = ?", (new_value, product_id))
        
        # Очищаем данные
        keys = ['edit_product_id', 'editing_field']
        for key in keys:
            if key in context.user_data:
                del context.user_data[key]
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Название товара изменено на '{new_value}'\n\n"
            f"Выберите раздел:",
            reply_markup=reply_markup
        )
        
        return MAIN_MENU

async def product_delete(update: Update, context):
    """Подтверждение удаления товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id:
        await query.edit_message_text(
            "❌ Ошибка: товар не выбран",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data="product_confirm_delete")],
        [InlineKeyboardButton("❌ Нет, отмена", callback_data="settings_products")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚠️ Вы уверены, что хотите удалить товар?\n"
        "Это действие необратимо! Товар будет удален у всех продавцов.",
        reply_markup=reply_markup
    )
    return EDIT_PRODUCT

@send_backup_to_admin("удаление товара")
async def product_confirm_delete(update: Update, context):
    """Окончательное удаление товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id:
        await query.edit_message_text(
            "❌ Ошибка: товар не выбран",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
            product_name = cursor.fetchone()[0]
            
            # Удаляем связанные записи
            cursor.execute("DELETE FROM seller_products WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM order_items WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM central_stock WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        
        # Очищаем данные
        if 'edit_product_id' in context.user_data:
            del context.user_data['edit_product_id']
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ Товар '{product_name}' удален\n\n"
            f"Выберите раздел:",
            reply_markup=reply_markup
        )
        
        return MAIN_MENU
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {e}")
        return MAIN_MENU

@send_backup_to_admin("изменение статуса товара")
async def product_toggle_status(update: Update, context):
    """Смена статуса товара (активен/скрыт)"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    if not product_id:
        await query.edit_message_text(
            "❌ Ошибка: товар не выбран",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        return PRODUCTS_MENU
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, product_name FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        
        if product:
            new_status = 0 if product['is_active'] else 1
            cursor.execute("UPDATE products SET is_active = ? WHERE id = ?", (new_status, product_id))
            status_text = "активирован" if new_status else "скрыт"
            product_name = product['product_name']
    
    await query.edit_message_text(
        f"✅ Статус товара '{product_name}' изменен на '{status_text}'",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
        ]])
    )
    
    # Очищаем данные
    if 'edit_product_id' in context.user_data:
        del context.user_data['edit_product_id']
    
    return PRODUCTS_MENU

async def product_cancel(update: Update, context):
    """Отмена действия с товарами"""
    query = update.callback_query
    if query:
        await query.answer()
        
        # Очищаем данные о товаре
        keys = ['edit_product_id', 'new_product_name', 'new_product_price', 'editing_field']
        for key in keys:
            if key in context.user_data:
                del context.user_data[key]
        
        # Возвращаемся в главное меню настроек
        keyboard = [
            [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
            [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
            [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "❌ Действие отменено\n\n"
            "Выберите раздел:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=get_admin_menu()
        )
    
    return MAIN_MENU
# НОВЫЙ РАЗДЕЛ: БЭКАПЫ
# ============================================

async def settings_backup(update: Update, context):
    """Меню управления бэкапами"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📦 Создать бэкап вручную", callback_data="backup_create")],
        [InlineKeyboardButton("📤 Загрузить бэкап", callback_data="backup_upload")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🔐 Управление бэкапами\n\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )
    return BACKUP_MENU

async def backup_create(update: Update, context):
    """Создание ручного бэкапа и отправка файла администратору"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text("🔄 Создание бэкапа...")
    
    try:
        # Генерируем JSON-бэкап
        json_data = backup.create_backup_json()
        filename = backup.get_backup_filename("manual_from_settings")
        
        # Отправляем файл в текущий чат
        await context.bot.send_document(
            chat_id=update.effective_user.id,
            document=io.BytesIO(json_data.encode('utf-8')),
            filename=filename,
            caption="✅ Ручной бэкап создан"
        )
        
        # Логируем действие
        db.log_action(
            user_id=update.effective_user.id,
            user_role="admin",
            action="manual_backup",
            details=f"Backup created from settings: {filename}"
        )
        
        # Возвращаемся в меню бэкапов
        await settings_backup(update, context)
        return BACKUP_MENU
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}")
        await query.edit_message_text(f"❌ Ошибка создания бэкапа: {e}")
        return BACKUP_MENU

async def backup_upload_start(update: Update, context):
    """Начало загрузки бэкапа – просим прислать файл"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📤 Отправьте JSON-файл с бэкапом.\n\n"
        "После получения файла будет произведено восстановление базы данных (текущая БД будет сохранена как автоматический бэкап).",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="backup_cancel")
        ]])
    )
    return WAITING_FOR_BACKUP_FILE

async def backup_file_received(update: Update, context):
    """Обработка загруженного файла бэкапа"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    document = update.message.document
    if not document.file_name.endswith('.json'):
        await update.message.reply_text(
            "❌ Неверный формат. Отправьте JSON-файл.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="backup_cancel")
            ]])
        )
        return WAITING_FOR_BACKUP_FILE
    
    await update.message.reply_text("🔄 Обработка файла...")
    
    try:
        # Скачиваем файл
        file = await document.get_file()
        file_content = await file.download_as_bytearray()
        data = json.loads(file_content.decode('utf-8'))
        
        # Создаём бэкап текущей БД перед восстановлением
        current_backup = backup.create_backup_json()
        current_filename = backup.get_backup_filename("before_restore")
        await update.message.reply_document(
            document=io.BytesIO(current_backup.encode('utf-8')),
            filename=current_filename,
            caption="📦 Автоматический бэкап перед восстановлением"
        )
        
        # Восстанавливаем данные
        conn = sqlite3.connect(config.DATABASE_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = OFF")
        
        # Очищаем все таблицы
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        for table in tables:
            table_name = table[0]
            if table_name != 'sqlite_sequence':
                cursor.execute(f"DELETE FROM {table_name}")
        
        # Вставляем данные из бэкапа
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
        
        await update.message.reply_text(
            f"✅ База данных успешно восстановлена из файла {document.file_name}\n"
            f"Восстановлено записей: {restored}"
        )
        
        # Логируем действие
        db.log_action(
            user_id=user_id,
            user_role="admin",
            action="restore_backup",
            details=f"Restored from uploaded {document.file_name}"
        )
        
        # Возвращаемся в меню настроек (или бэкапов)
        keyboard = [
            [InlineKeyboardButton("🔐 Управление бэкапами", callback_data="settings_backup")],
            [InlineKeyboardButton("🔙 В главное меню", callback_data="settings_back")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Выберите дальнейшее действие:",
            reply_markup=reply_markup
        )
        return MAIN_MENU
        
    except json.JSONDecodeError:
        await update.message.reply_text(
            "❌ Ошибка: файл не является корректным JSON.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="backup_cancel")
            ]])
        )
        return WAITING_FOR_BACKUP_FILE
    except Exception as e:
        logger.error(f"Restore error: {e}")
        await update.message.reply_text(
            f"❌ Ошибка восстановления: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Назад", callback_data="backup_cancel")
            ]])
        )
        return WAITING_FOR_BACKUP_FILE

async def backup_cancel(update: Update, context):
    """Отмена загрузки бэкапа и возврат в меню бэкапов"""
    query = update.callback_query
    await query.answer()
    await settings_backup(update, context)
    return BACKUP_MENU
# ============================================
# ОБЩИЕ ФУНКЦИИ
# ============================================

async def back_to_main(update: Update, context):
    """Возврат в главное меню настроек"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.clear()
    
    keyboard = [
        [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
        [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
        [InlineKeyboardButton("🔐 Бэкапы", callback_data="settings_backup")],
        [InlineKeyboardButton("🔙 В админ-меню", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "⚙️ Настройки\n\nВыберите раздел:",
        reply_markup=reply_markup
    )
    return MAIN_MENU

async def exit_settings(update: Update, context):
    """Выход из настроек"""
    query = update.callback_query
    await query.answer()
    
    context.user_data.clear()
    
    await query.edit_message_text(
        "Выход в главное меню",
        reply_markup=get_admin_menu()
    )
    
    return ConversationHandler.END

# ============================================
# ОБНОВЛЁННЫЙ ОБРАБОТЧИК РАЗГОВОРА
# ============================================

admin_settings_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^⚙️ Настройки$'), admin_settings_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(settings_sellers, pattern='^settings_sellers$'),
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),
            CallbackQueryHandler(settings_backup, pattern='^settings_backup$'),
            CallbackQueryHandler(seller_add_start, pattern='^seller_add$'),
            CallbackQueryHandler(seller_list, pattern='^seller_list$'),
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(exit_settings, pattern='^settings_back$')
        ],
        # Состояния для продавцов (без изменений)
        ADD_SELLER_CODE: [
            CallbackQueryHandler(seller_add_start, pattern='^seller_add$'),
            CallbackQueryHandler(seller_cancel, pattern='^seller_cancel$'),
            CallbackQueryHandler(seller_edit_code, pattern='^seller_edit_code$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_code)
        ],
        ADD_SELLER_NAME: [
            CallbackQueryHandler(seller_cancel, pattern='^seller_cancel$'),
            CallbackQueryHandler(seller_edit_name, pattern='^seller_edit_name$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_name)
        ],
        ADD_SELLER_TG_ID: [
            CallbackQueryHandler(seller_confirm, pattern='^seller_confirm$'),
            CallbackQueryHandler(seller_edit_code, pattern='^seller_edit_code$'),
            CallbackQueryHandler(seller_edit_name, pattern='^seller_edit_name$'),
            CallbackQueryHandler(seller_edit_tg, pattern='^seller_edit_tg$'),
            CallbackQueryHandler(seller_cancel, pattern='^seller_cancel$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_tg_id)
        ],
        LIST_SELLERS: [
            CallbackQueryHandler(seller_list, pattern='^seller_list$'),
            CallbackQueryHandler(settings_sellers, pattern='^settings_sellers$')
        ],
        EDIT_SELLER: [
            CallbackQueryHandler(seller_edit, pattern='^seller_edit_'),
            CallbackQueryHandler(seller_edit, pattern='^seller_list$'),
            CallbackQueryHandler(seller_toggle_status, pattern='^seller_toggle_status$'),
            CallbackQueryHandler(seller_delete, pattern='^seller_delete$')
        ],
        CONFIRM_DELETE: [
            CallbackQueryHandler(seller_confirm_delete, pattern='^seller_confirm_delete$'),
            CallbackQueryHandler(seller_list, pattern='^seller_list$')
        ],
        # Состояния для товаров (без изменений)
        PRODUCTS_MENU: [
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),
            CallbackQueryHandler(product_add_start, pattern='^product_add$'),
            CallbackQueryHandler(product_edit_start, pattern='^product_edit_'),
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel_edit$')
        ],
        ADD_PRODUCT_NAME: [
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_edit_name, pattern='^product_edit_name$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_name)
        ],
        ADD_PRODUCT_PRICE: [
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_edit_price, pattern='^product_edit_price$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_price)
        ],
        ADD_PRODUCT_CONFIRM: [
            CallbackQueryHandler(product_confirm, pattern='^product_confirm$'),
            CallbackQueryHandler(product_edit_name, pattern='^product_edit_name$'),
            CallbackQueryHandler(product_edit_price, pattern='^product_edit_price$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$')
        ],
        EDIT_PRODUCT: [
            CallbackQueryHandler(product_change_price, pattern='^product_change_price$'),
            CallbackQueryHandler(product_change_name, pattern='^product_change_name$'),
            CallbackQueryHandler(product_delete, pattern='^product_delete$'),
            CallbackQueryHandler(product_confirm_delete, pattern='^product_confirm_delete$'),
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel_edit$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_update_field)
        ],
        # Новые состояния для бэкапов
        BACKUP_MENU: [
            CallbackQueryHandler(backup_create, pattern='^backup_create$'),
            CallbackQueryHandler(backup_upload_start, pattern='^backup_upload$'),
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(backup_cancel, pattern='^backup_cancel$')
        ],
        WAITING_FOR_BACKUP_FILE: [
            CallbackQueryHandler(backup_cancel, pattern='^backup_cancel$'),
            MessageHandler(filters.Document.ALL, backup_file_received)
        ]
    },
    fallbacks=[CommandHandler('cancel', exit_settings)],
    allow_reentry=True
)
