import os
from datetime import datetime
import sqlite3
from utils.timezone_utils import moscow_now_iso

def log_to_file(message: str, level: str = "INFO"):
    log_dir = os.path.join(os.path.dirname(__file__), '../logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, 'system.log')
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{moscow_now_iso()}] [{level}] {message}\n")

def log_to_db(db_path, user_id, module, level, message):
    try:
        from utils.timezone_utils import moscow_datetime_sql
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(f'INSERT INTO logs (user_id, module, level, message, created_at) VALUES (?, ?, ?, ?, {moscow_datetime_sql()})',
                           (user_id, module, level, message))
            conn.commit()
    except Exception as e:
        log_to_file(f"[DB LOGGING FAILED] {e} ({message})", level="ERROR")
