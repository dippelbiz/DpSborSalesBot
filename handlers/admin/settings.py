#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import db
from config import config
from keyboards import get_admin_menu
from backup_decorator import send_backup_to_admin

# Состояния разговора
MAIN_MENU, ADD_SELLER, LIST_SELLERS, EDIT_SELLER, CONFIRM_DELETE = range(5)

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
            tg_status = "✅" if seller['telegram_id'] else "❌"
            text += f"{status} {seller['seller_code']} - {seller['full_name']} {tg_status}\n"
    else:
        text += "Продавцов пока нет\n"
    
    keyboard = [
        [InlineKeyboardButton("➕ Добавить продавца", callback_data="seller_add")],
        [InlineKeyboardButton("📋 Список продавцов", callback_data="seller_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="settings_back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup)
    return ADD_SELLER

async def seller_add_start(update: Update, context):
    """Начало добавления продавца"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ Добавление нового продавца\n\n"
        "Введите данные в формате:\n"
        "<код> <имя>\n\n"
        "Например: ТЕСТ ТестовыйПродавец\n\n"
        "Код может быть из букв и цифр (например: А, А1, ТЕСТ)",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
        ]])
    )
    return ADD_SELLER

async def seller_add_process(update: Update, context):
    """Обработка ввода данных продавца"""
    user_id = update.effective_user.id
    
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return ConversationHandler.END
    
    text = update.message.text.strip()
    
    # Разбираем ввод
    parts = text.split(' ', 1)
    if len(parts) < 2:
        await update.message.reply_text(
            "❌ Неверный формат. Введите: код имя\n"
            "Например: ТЕСТ ТестовыйПродавец",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
            ]])
        )
        return ADD_SELLER
    
    seller_code = parts[0].upper()
    seller_name = parts[1]
    
    # Проверяем код
    if len(seller_code) < 1 or len(seller_code) > 5:
        await update.message.reply_text(
            "❌ Код должен быть от 1 до 5 символов",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
            ]])
        )
        return ADD_SELLER
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Проверяем уникальность кода
        cursor.execute("SELECT id FROM sellers WHERE seller_code = ?", (seller_code,))
        if cursor.fetchone():
            await update.message.reply_text(
                f"❌ Код {seller_code} уже используется",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")
                ]])
            )
            return ADD_SELLER
        
        # Сохраняем в контекст
        context.user_data['new_seller_code'] = seller_code
        context.user_data['new_seller_name'] = seller_name
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="seller_confirm")],
        [InlineKeyboardButton("✏️ Изменить", callback_data="seller_edit")],
        [InlineKeyboardButton("❌ Отмена", callback_data="seller_cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Проверьте данные:\n\n"
        f"Код: {seller_code}\n"
        f"Имя: {seller_name}\n\n"
        f"Всё верно?",
        reply_markup=reply_markup
    )
    return ADD_SELLER

@send_backup_to_admin("добавление продавца")
async def seller_confirm(update: Update, context):
    """Подтверждение добавления продавца"""
    query = update.callback_query
    await query.answer()
    
    seller_code = context.user_data.get('new_seller_code')
    seller_name = context.user_data.get('new_seller_name')
    
    if not seller_code or not seller_name:
        await query.edit_message_text("❌ Ошибка: данные не найдены")
        return ConversationHandler.END
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Добавляем продавца
            cursor.execute("""
                INSERT INTO sellers (seller_code, full_name, is_active)
                VALUES (?, ?, 1)
            """, (seller_code, seller_name))
            
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
        
        await query.edit_message_text(
            f"✅ Продавец успешно добавлен!\n\n"
            f"Код: {seller_code}\n"
            f"Имя: {seller_name}\n\n"
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
        tg = "✅" if seller['telegram_id'] else "❌ не активирован"
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
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM sellers WHERE id = ?", (seller_id,))
        current = cursor.fetchone()
        
        if current:
            new_status = 0 if current['is_active'] else 1
            cursor.execute("UPDATE sellers SET is_active = ? WHERE id = ?", (new_status, seller_id))
    
    await query.edit_message_text(
        "✅ Статус обновлен",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Назад", callback_data="seller_list")
        ]])
    )
    return MAIN_MENU

async def seller_delete(update: Update, context):
    """Подтверждение удаления продавца"""
    query = update.callback_query
    await query.answer()
    
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
    
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Удаляем связанные записи
            cursor.execute("DELETE FROM seller_products WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM seller_debt WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM seller_pending WHERE seller_id = ?", (seller_id,))
            cursor.execute("DELETE FROM sellers WHERE id = ?", (seller_id,))
        
        await query.edit_message_text(
            "✅ Продавец удален",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К продавцам", callback_data="settings_sellers")
            ]])
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка удаления: {e}")
    
    context.user_data.clear()
    return MAIN_MENU

async def seller_cancel(update: Update, context):
    """Отмена действия"""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "❌ Действие отменено",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 К продавцам", callback_data="settings_sellers")
            ]])
        )
    else:
        await update.message.reply_text(
            "❌ Действие отменено",
            reply_markup=get_admin_menu()
        )
    
    context.user_data.clear()
    return MAIN_MENU

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

# Обработчик разговора для настроек
admin_settings_conv = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex('^⚙️ Настройки$'), admin_settings_start)],
    states={
        MAIN_MENU: [
            CallbackQueryHandler(settings_sellers, pattern='^settings_sellers$'),
            CallbackQueryHandler(back_to_main, pattern='^settings_back_to_main$'),
            CallbackQueryHandler(exit_settings, pattern='^settings_back$')
        ],
        ADD_SELLER: [
            CallbackQueryHandler(seller_add_start, pattern='^seller_add$'),
            CallbackQueryHandler(seller_confirm, pattern='^seller_confirm$'),
            CallbackQueryHandler(seller_cancel, pattern='^seller_cancel$'),
            CallbackQueryHandler(seller_list, pattern='^seller_list$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seller_add_process)
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
        ]
    },
    fallbacks=[CommandHandler('cancel', seller_cancel)]
)
