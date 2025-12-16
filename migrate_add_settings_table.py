"""
Миграция для добавления таблицы settings для хранения IP-адреса удаленного сервера.
Запустите этот скрипт один раз для каждой базы данных.
"""
import sqlite3
import os

def migrate_db(db_path):
    """Добавляет таблицу settings в базу данных, если её нет."""
    if not os.path.exists(db_path):
        print(f"База данных не найдена: {db_path}")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Проверяем, существует ли таблица settings
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if cursor.fetchone():
            print(f"Таблица settings уже существует в {db_path}")
        else:
            # Создаем таблицу settings
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            print(f"Создана таблица settings в {db_path}")
            
            # Добавляем начальные значения из config.py (если доступен)
            try:
                from config import REMOTE_SERVER_B, REMOTE_SERVER_A
                # Определяем, какая база данных (user_1 или user_2)
                if 'user_1' in db_path:
                    # Для server_user_1 сохраняем REMOTE_SERVER_B
                    cursor.execute('''
                    INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
                    VALUES ('remote_server_url', ?, CURRENT_TIMESTAMP)
                    ''', (REMOTE_SERVER_B,))
                    print(f"Добавлено начальное значение remote_server_url = {REMOTE_SERVER_B}")
                elif 'user_2' in db_path:
                    # Для server_user_2 сохраняем REMOTE_SERVER_A
                    cursor.execute('''
                    INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
                    VALUES ('remote_server_url', ?, CURRENT_TIMESTAMP)
                    ''', (REMOTE_SERVER_A,))
                    print(f"Добавлено начальное значение remote_server_url = {REMOTE_SERVER_A}")
            except ImportError:
                # Если config.py не найден, используем значения по умолчанию
                if 'user_1' in db_path:
                    default_url = 'http://localhost:5001'
                else:
                    default_url = 'http://localhost:5000'
                cursor.execute('''
                INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
                VALUES ('remote_server_url', ?, CURRENT_TIMESTAMP)
                ''', (default_url,))
                print(f"Добавлено значение по умолчанию remote_server_url = {default_url}")
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Ошибка при миграции {db_path}: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == '__main__':
    # Миграция для обеих баз данных
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # База данных server_user_1
    db_path_1 = os.path.join(base_dir, 'server_user_1', 'databases', 'user_1_db.db')
    if os.path.exists(db_path_1):
        print(f"\nМиграция базы данных server_user_1...")
        migrate_db(db_path_1)
    else:
        print(f"База данных не найдена: {db_path_1}")
    
    # База данных server_user_2
    db_path_2 = os.path.join(base_dir, 'server_user_2', 'databases', 'user_2_db.db')
    if os.path.exists(db_path_2):
        print(f"\nМиграция базы данных server_user_2...")
        migrate_db(db_path_2)
    else:
        print(f"База данных не найдена: {db_path_2}")
    
    print("\nМиграция завершена!")
