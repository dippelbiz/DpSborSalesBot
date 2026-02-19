#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Декоратор для автоматической отправки бэкапов при действиях
"""

from functools import wraps
import io
from datetime import datetime

from telegram import Update

from backup import backup
from database import db
from config import config

def send_backup_to_admin(action_description):
    """
    Декоратор, который после выполнения функции отправляет бэкап админу
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            # Выполняем основную функцию
            result = await func(update, context, *args, **kwargs)
            
            try:
                # Получаем информацию о пользователе
                if update.effective_user:
                    user = update.effective_user
                    user_id = user.id
                    user_name = user.full_name or user.username or str(user_id)
                    
                    # Определяем роль
                    if user_id in config.ADMIN_IDS:
                        role = "администратор"
                    else:
                        role = "продавец"
                    
                    # Создаем JSON-бэкап
                    json_data = backup.create_backup_json()
                    filename = backup.get_backup_filename(action_description)
                    
                    # Отправляем каждому админу
                    for admin_id in config.ADMIN_IDS:
                        try:
                            # Создаем файл в памяти и отправляем
                            await context.bot.send_document(
                                chat_id=admin_id,
                                document=io.BytesIO(json_data.encode('utf-8')),
                                filename=filename,
                                caption=f"🔄 Бэкап после действия: {action_description}\n"
                                       f"👤 Пользователь: {user_name} (ID: {user_id})\n"
                                       f"👑 Роль: {role}\n"
                                       f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
                            )
                        except Exception as e:
                            print(f"Не удалось отправить бэкап админу {admin_id}: {e}")
                    
                    # Логируем действие
                    db.log_action(
                        user_id=user_id,
                        user_role=role,
                        action=action_description,
                        details=f"Бэкап отправлен админу"
                    )
                    
            except Exception as e:
                print(f"Ошибка при создании бэкапа: {e}")
            
            return result
        return wrapper
    return decorator
