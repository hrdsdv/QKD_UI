"""
Утилиты для работы с московским временем (UTC+3)
"""
from datetime import datetime, timezone, timedelta
import re

# Московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

def moscow_now() -> datetime:
    """
    Возвращает текущее время по Москве (UTC+3)
    """
    return datetime.now(MOSCOW_TZ)

def moscow_now_str(format_str: str = '%Y%m%d%H%M%S') -> str:
    """
    Возвращает текущее время по Москве в виде строки
    """
    return moscow_now().strftime(format_str)

def moscow_now_iso() -> str:
    """
    Возвращает текущее время по Москве в ISO формате
    """
    return moscow_now().isoformat()

def moscow_datetime_sql() -> str:
    """
    Возвращает строку для использования в SQLite с московским временем
    SQLite не поддерживает timezone напрямую, поэтому используем datetime('now', '+3 hours')
    """
    return "datetime('now', '+3 hours')"

def format_datetime_for_display(dt_string: str) -> str:
    """
    Форматирует строку времени из базы данных для отображения в UI.
    Если время уже в формате с +3 часами, просто форматирует его.
    Если время в UTC, добавляет +3 часа.
    
    :param dt_string: Строка времени из базы данных
    :return: Отформатированная строка времени для отображения
    """
    if not dt_string:
        return ''
    
    try:
        # Пробуем разные форматы времени из SQLite
        # SQLite может возвращать время в формате: 'YYYY-MM-DD HH:MM:SS'
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%S.%f%z'
        ]
        
        dt = None
        for fmt in formats:
            try:
                dt = datetime.strptime(dt_string, fmt)
                break
            except ValueError:
                continue
        
        if dt is None:
            # Если не удалось распарсить, возвращаем как есть
            return dt_string
        
        # Если время без timezone, считаем что оно уже в московском времени (т.к. сохраняли с +3 часами)
        # Просто форматируем для отображения
        if dt.tzinfo is None:
            # Время уже в московском (сохранено с +3 часами), просто форматируем
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            # Если есть timezone, конвертируем в московское
            moscow_dt = dt.astimezone(MOSCOW_TZ)
            return moscow_dt.strftime('%Y-%m-%d %H:%M:%S')
            
    except Exception as e:
        # В случае ошибки возвращаем исходную строку
        return dt_string

