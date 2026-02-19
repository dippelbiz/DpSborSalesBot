#!/usr/bin/env python
# -*- coding: utf-8 -*-

from telegram import Update
from telegram.ext import CommandHandler
from database import db
from config import config

async def add_test_seller(update: Update, context):
    """Команда для добавления тестового продавца /add_seller"""
    user_id = update.effective_user.id
    
    # Только админ может добавлять продавцов
    if user_id not in config.ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещен")
        return
    
    # Проверяем, передан ли аргумент
    if not context.args:
        await update.message.reply_text(
            "❌ Использование: /add_seller <telegram_id> <код> <имя>\n"
            "Пример: /add_seller 123456789 ТЕСТ Тестовый"
        )
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("❌ Нужно указать ID, код и имя")
        return
    
    try:
        seller_tg_id = int(context.args[0])
        seller_code = context.args[1].upper()
        seller_name = ' '.join(context.args[2:])
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Проверяем, не занят ли код
            cursor.execute("SELECT id FROM sellers WHERE seller_code = ?", (seller_code,))
            if cursor.fetchone():
                await update.message.reply_text(f"❌ Код {seller_code} уже используется")
                return
            
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
        
        await update.message.reply_text(
            f"✅ Продавец успешно добавлен!\n\n"
            f"Код: {seller_code}\n"
            f"Имя: {seller_name}\n"
            f"Telegram ID: {seller_tg_id}\n\n"
            f"Теперь продавец может активировать аккаунт командой /start"
        )
        
    except ValueError:
        await update.message.reply_text("❌ Telegram ID должен быть числом")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ВАЖНО: создаем обработчик
add_seller_handler = CommandHandler("add_seller", add_test_seller)
