#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin

# Состояния разговора (расширяем)
MAIN_MENU, ADD_SELLER_CODE, ADD_SELLER_NAME, ADD_SELLER_TG_ID, LIST_SELLERS, EDIT_SELLER, CONFIRM_DELETE, PRODUCTS_MENU, ADD_PRODUCT, EDIT_PRODUCT_PRICE = range(10)

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

# === УПРАВЛЕНИЕ ПРОДАВЦАМИ (уже есть) ===
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
    return ADD_SELLER_CODE

# === НОВЫЙ РАЗДЕЛ: ТОВАРЫ И ЦЕНЫ ===
async def settings_products(update: Update, context):
    """Меню управления товарами и ценами"""
    query = update.callback_query
    await query.answer()
    
    # Получаем список товаров
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, product_name, price, is_active 
            FROM products 
            ORDER BY product_name
        """)
        products = cursor.fetchall()
    
    text = "🏷️ Товары и цены\n\n"
    text += "Текущие товары:\n"
    
    keyboard = []
    for product in products:
        status = "✅" if product['is_active'] else "❌"
        text += f"{status} {product['product_name']}: {product['price']} руб\n"
        keyboard.append([InlineKeyboardButton(
            f"✏️ {product['product_name']} ({product['price']} руб)",
            callback_data=f"product_edit_{product['id']}"
        )])
    
    keyboard.append([InlineKeyboardButton("➕ Добавить товар", callback_data="product_add")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="settings_back_to_main")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return PRODUCTS_MENU

async def product_add_start(update: Update, context):
    """Начало добавления нового товара"""
    query = update.callback_query
    await query.answer()
    
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
    return ADD_PRODUCT

async def product_add_name(update: Update, context):
    """Шаг 1: ввод названия товара"""
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
        return ADD_PRODUCT
    
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
            return ADD_PRODUCT
    
    # Сохраняем название в контекст
    context.user_data['new_product_name'] = product_name
    
    await update.message.reply_text(
        f"✅ Название принято: {product_name}\n\n"
        f"Шаг 2 из 2 - Введите **цену** товара (в рублях):\n"
        f"Например: 250, 300, 150",
        parse_mode='Markdown'
    )
    return EDIT_PRODUCT_PRICE

async def product_add_price(update: Update, context):
    """Шаг 2: ввод цены и сохранение товара"""
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
            "Попробуйте снова:"
        )
        return EDIT_PRODUCT_PRICE
    
    product_name = context.user_data.get('new_product_name')
    
    if not product_name:
        await update.message.reply_text("❌ Ошибка: название не найдено")
        return ConversationHandler.END
    
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
    
    context.user_data['new_product_price'] = price
    return EDIT_PRODUCT_PRICE

@send_backup_to_admin("добавление товара")
async def product_confirm(update: Update, context):
    """Подтверждение добавления товара"""
    query = update.callback_query
    await query.answer()
    
    product_name = context.user_data.get('new_product_name')
    product_price = context.user_data.get('new_product_price')
    
    if not product_name or not product_price:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return ConversationHandler.END
    
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
        
        await query.edit_message_text(
            f"✅ Товар успешно добавлен!\n\n"
            f"Название: {product_name}\n"
            f"Цена: {product_price} руб\n\n"
            f"Товар добавлен всем продавцам.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
    
    context.user_data.clear()
    return PRODUCTS_MENU

async def product_edit_start(update: Update, context):
    """Редактирование товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = int(query.data.replace('product_edit_', ''))
    context.user_data['edit_product_id'] = product_id
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
    
    if not product:
        await query.edit_message_text("❌ Товар не найден")
        return PRODUCTS_MENU
    
    status_text = "Активен" if product['is_active'] else "Скрыт"
    
    keyboard = [
        [InlineKeyboardButton("💰 Изменить цену", callback_data="product_change_price")],
        [InlineKeyboardButton("🔄 Сменить статус", callback_data="product_toggle_status")],
        [InlineKeyboardButton("❌ Удалить", callback_data="product_delete")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_products")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"✏️ Редактирование товара\n\n"
        f"Название: {product['product_name']}\n"
        f"Цена: {product['price']} руб\n"
        f"Статус: {status_text}\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )
    return EDIT_PRODUCT_PRICE

async def product_change_price(update: Update, context):
    """Изменение цены товара"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💰 Введите новую цену товара (в рублях):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="product_cancel_edit")
        ]])
    )
    return EDIT_PRODUCT_PRICE

async def product_update_price(update: Update, context):
    """Обновление цены товара"""
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
            "Попробуйте снова:"
        )
        return EDIT_PRODUCT_PRICE
    
    product_id = context.user_data.get('edit_product_id')
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET price = ? WHERE id = ?", (price, product_id))
        cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
        product_name = cursor.fetchone()[0]
    
    await update.message.reply_text(
        f"✅ Цена товара '{product_name}' обновлена до {price} руб",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
        ]])
    )
    
    context.user_data.clear()
    return PRODUCTS_MENU

async def product_toggle_status(update: Update, context):
    """Смена статуса товара (активен/скрыт)"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active, product_name FROM products WHERE id = ?", (product_id,))
        product = cursor.fetchone()
        
        if product:
            new_status = 0 if product['is_active'] else 1
            cursor.execute("UPDATE products SET is_active = ? WHERE id = ?", (new_status, product_id))
            status_text = "активирован" if new_status else "скрыт"
    
    await query.edit_message_text(
        f"✅ Статус товара '{product['product_name']}' изменен на '{status_text}'",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
        ]])
    )
    
    context.user_data.clear()
    return PRODUCTS_MENU

async def product_delete(update: Update, context):
    """Подтверждение удаления товара"""
    query = update.callback_query
    await query.answer()
    
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
    return EDIT_PRODUCT_PRICE

@send_backup_to_admin("удаление товара")
async def product_confirm_delete(update: Update, context):
    """Окончательное удаление товара"""
    query = update.callback_query
    await query.answer()
    
    product_id = context.user_data.get('edit_product_id')
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT product_name FROM products WHERE id = ?", (product_id,))
            product_name = cursor.fetchone()[0]
            
            # Удаляем связанные записи
            cursor.execute("DELETE FROM seller_products WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM order_items WHERE product_id = ?", (product_id,))
            cursor.execute("DELETE FROM products WHERE id = ?", (product_id,))
        
        await query.edit_message_text(
            f"✅ Товар '{product_name}' удален",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {e}")
    
    context.user_data.clear()
    return PRODUCTS_MENU

# === ОСТАЛЬНЫЕ ФУНКЦИИ (без изменений) ===
# ... (все функции для управления продавцами остаются без изменений)

async def product_cancel(update: Update, context):
    """Отмена действия с товарами"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "❌ Действие отменено",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К товарам", callback_data="settings_products")
            ]])
        )
    else:
        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=get_admin_menu()
        )
    
    context.user_data.clear()
    return PRODUCTS_MENU

async def back_to_main(update: Update, context):
    """Возврат в главное меню настроек"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("👥 Управление продавцами", callback_data="settings_sellers")],
        [InlineKeyboardButton("🏷️ Товары и цены", callback_data="settings_products")],
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
    
    await query.edit_message_text(
        "Выход в главное меню",
        reply_markup=get_admin_menu()
    )
    
    return ConversationHandler.END

# ===== ОБНОВЛЕННЫЙ ConversationHandler =====
admin_settings_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^⚙️ Настройки$'), admin_settings_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(settings_sellers, pattern='^settings_sellers$'),
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),  # ← НОВОЕ
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(exit_settings, pattern='^settings_back$')
        ],
        ADD_SELLER_CODE: [
            CallbackQueryHandler(settings_sellers, pattern='^seller_add$'),
            CallbackQueryHandler(settings_sellers, pattern='^seller_list$'),
            CallbackQueryHandler(settings_sellers, pattern='^seller_cancel$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_code)
        ],
        ADD_SELLER_NAME: [
            CallbackQueryHandler(settings_sellers, pattern='^seller_cancel$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_name)
        ],
        ADD_SELLER_TG_ID: [
            CallbackQueryHandler(seller_confirm, pattern='^seller_confirm$'),
            CallbackQueryHandler(settings_sellers, pattern='^seller_cancel$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_tg_id)
        ],
        LIST_SELLERS: [
            CallbackQueryHandler(seller_list, pattern='^seller_list$'),
            CallbackQueryHandler(settings_sellers, pattern='^settings_sellers$')
        ],
        EDIT_SELLER: [
            CallbackQueryHandler(seller_edit, pattern='^seller_edit_'),
            CallbackQueryHandler(seller_toggle_status, pattern='^seller_toggle_status$'),
            CallbackQueryHandler(seller_delete, pattern='^seller_delete$'),
            CallbackQueryHandler(settings_sellers, pattern='^seller_list$')
        ],
        CONFIRM_DELETE: [
            CallbackQueryHandler(seller_confirm_delete, pattern='^seller_confirm_delete$'),
            CallbackQueryHandler(settings_sellers, pattern='^seller_list$')
        ],
        # НОВЫЕ СОСТОЯНИЯ ДЛЯ ТОВАРОВ
        PRODUCTS_MENU: [
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),
            CallbackQueryHandler(product_add_start, pattern='^product_add$'),
            CallbackQueryHandler(product_edit_start, pattern='^product_edit_'),
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel_edit$')
        ],
        ADD_PRODUCT: [
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_name)
        ],
        EDIT_PRODUCT_PRICE: [
            CallbackQueryHandler(product_confirm, pattern='^product_confirm$'),
            CallbackQueryHandler(product_edit_start, pattern='^product_edit_name$'),
            CallbackQueryHandler(product_change_price, pattern='^product_change_price$'),
            CallbackQueryHandler(product_toggle_status, pattern='^product_toggle_status$'),
            CallbackQueryHandler(product_delete, pattern='^product_delete$'),
            CallbackQueryHandler(product_confirm_delete, pattern='^product_confirm_delete$'),
            CallbackQueryHandler(settings_products, pattern='^settings_products$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel$'),
            CallbackQueryHandler(product_cancel, pattern='^product_cancel_edit$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_add_price),
            MessageHandler(filters.TEXT & ~filters.COMMAND, product_update_price)
        ]
    },
    fallbacks=[CommandHandler('cancel', exit_settings)]
)

# ... (все функции для управления продавцами seller_add_code, seller_add_name, seller_add_tg_id, 
# seller_confirm, seller_list, seller_edit, seller_toggle_status, seller_delete, seller_confirm_delete
# остаются без изменений из предыдущей версии)
