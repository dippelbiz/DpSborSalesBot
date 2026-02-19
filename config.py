#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Конфигурация бота, переменные окружения
"""

import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла (для локальной разработки)
load_dotenv()

class Config:
    """Класс конфигурации"""
    
    # Токен бота (обязательно)
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения")
    
    # ID администраторов (через запятую)
    ADMIN_IDS = []
    admin_ids_str = os.getenv('ADMIN_IDS', '')
    if admin_ids_str:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_str.split(',') if id.strip()]
    
    # Путь к базе данных
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'warehouse_bot.db')
    
    # Режим отладки
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

config = Config()
