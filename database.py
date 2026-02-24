#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Работа с базой данных SQLite
"""

import sqlite3
from datetime import datetime
from contextlib import contextmanager

from config import config

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def init_db(self):
        """Инициализация таблиц при первом запуске"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица товаров (общие настройки)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_name TEXT UNIQUE NOT NULL,
                    price INTEGER NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица продавцов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sellers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_code TEXT UNIQUE NOT NULL,
                    full_name TEXT NOT NULL,
                    telegram_id INTEGER UNIQUE,
                    is_active BOOLEAN DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    activated_at DATETIME
                )
            ''')
            
            # Таблица остатков продавцов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS seller_products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    seller_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER DEFAULT 0,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id),
                    FOREIGN KEY (product_id) REFERENCES products(id),
                    UNIQUE(seller_id, product_id)
                )
            ''')
            
            # Таблица долга продавца
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS seller_debt (
                    seller_id INTEGER PRIMARY KEY,
                    total_debt INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id)
                )
            ''')
            
            # Таблица суммы к переводу
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS seller_pending (
                    seller_id INTEGER PRIMARY KEY,
                    pending_amount INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id)
                )
            ''')
            
            # Таблица заявок на поставку
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_number TEXT UNIQUE NOT NULL,
                    seller_id INTEGER NOT NULL,
                    seller_code TEXT NOT NULL,
                    status TEXT DEFAULT 'new',  -- new, shipped, completed, cancelled
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    shipped_at DATETIME,
                    completed_at DATETIME,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id)
                )
            ''')
            
            # Таблица товаров в заявке на поставку
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS order_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity_ordered INTEGER NOT NULL,
                    quantity_received INTEGER,
                    price_at_order INTEGER NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            ''')
            
            # Таблица продаж
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_number TEXT UNIQUE NOT NULL,
                    seller_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            ''')
            
            # Таблица запросов на платежи
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_number TEXT UNIQUE NOT NULL,
                    seller_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',  -- pending, approved, rejected
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    approved_at DATETIME,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id)
                )
            ''')
            
            # Таблица для логов (для аудита)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_role TEXT,
                    action TEXT,
                    details TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица заявок на пополнение центрального склада
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS restock_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_number TEXT UNIQUE NOT NULL,
                    seller_id INTEGER NOT NULL,
                    seller_code TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',  -- pending, completed, cancelled
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    FOREIGN KEY (seller_id) REFERENCES sellers(id)
                )
            ''')
            
            # Таблица товаров в заявке на пополнение
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS restock_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    quantity_requested INTEGER NOT NULL,
                    quantity_received INTEGER,
                    FOREIGN KEY (request_id) REFERENCES restock_requests(id),
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            ''')
            
            # Таблица истории пополнений склада Р
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS restock_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id)
                )
            ''')
            
            # Добавляем начальные товары, если таблица пуста
            cursor.execute("SELECT COUNT(*) FROM products")
            if cursor.fetchone()[0] == 0:
                initial_products = [
                    ('Манго', 250),
                    ('Папайя', 200),
                    ('Клубника', 150),
                    ('Грецкий орех', 180),
                    ('Миндаль', 150),
                    ('Кешью', 220),
                    ('Фисташки', 200)
                ]
                for name, price in initial_products:
                    cursor.execute(
                        "INSERT INTO products (product_name, price) VALUES (?, ?)",
                        (name, price)
                    )
    
    def log_action(self, user_id, user_role, action, details=None):
        """Запись действия в лог"""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    "INSERT INTO logs (user_id, user_role, action, details) VALUES (?, ?, ?, ?)",
                    (user_id, user_role, action, details)
                )
        except Exception as e:
            print(f"Ошибка логирования: {e}")

# Создаем глобальный экземпляр БД
db = Database(config.DATABASE_PATH)
