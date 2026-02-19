#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Упрощенный модуль для создания бэкапов
"""

import sqlite3
import json
import io
from datetime import datetime

from config import config

class SimpleBackup:
    """Класс для создания простых бэкапов"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    def create_backup_json(self):
        """
        Создает JSON-дамп базы данных и возвращает как строку
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Получаем список всех таблиц
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        data = {}
        for table in tables:
            if table != 'sqlite_sequence':  # Пропускаем системную таблицу
                cursor.execute(f"SELECT * FROM {table}")
                rows = cursor.fetchall()
                data[table] = [dict(row) for row in rows]
        
        conn.close()
        
        # Преобразуем в JSON
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    
    def create_backup_sql(self):
        """
        Создает SQL-дамп базы данных
        """
        conn = sqlite3.connect(self.db_path)
        
        # Получаем дамп в памяти
        with io.StringIO() as f:
            for line in conn.iterdump():
                f.write(f"{line}\n")
            sql_dump = f.getvalue()
        
        conn.close()
        return sql_dump
    
    def get_backup_filename(self, action):
        """
        Генерирует имя файла для бэкапа
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"backup_{timestamp}_{action}.json"

# Глобальный экземпляр
backup = SimpleBackup(config.DATABASE_PATH)
