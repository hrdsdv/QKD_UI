"""
Скрипт для добавления таблицы test_messages в существующие базы данных.
Запустите этот скрипт, если у вас уже есть работающие БД и вы не хотите их пересоздавать.
"""
import sqlite3
import os

def update_db(db_path):
    """Добавляет таблицу test_messages в существующую БД."""
    if not os.path.exists(db_path):
        print(f"База данных не найдена: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем, существует ли уже таблица
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='test_messages'
        """)
        
        table_exists = cursor.fetchone() is not None
        
        if not table_exists:
            # Создаем таблицу test_messages
            cursor.execute('''
            CREATE TABLE test_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                receiver TEXT NOT NULL,
                message_text TEXT NOT NULL,
                direction TEXT NOT NULL DEFAULT 'received',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            print(f"  ✅ Создана таблица test_messages в {db_path}")
        else:
            print(f"  ℹ️ Таблица test_messages уже существует в {db_path}")
            # Проверяем и добавляем поле direction если его нет
            cursor.execute("PRAGMA table_info(test_messages)")
            columns = [column[1] for column in cursor.fetchall()]
            
            if 'direction' not in columns:
                try:
                    cursor.execute("ALTER TABLE test_messages ADD COLUMN direction TEXT DEFAULT 'received'")
                    # Обновляем существующие записи, устанавливая direction='received' по умолчанию
                    cursor.execute("UPDATE test_messages SET direction = 'received' WHERE direction IS NULL")
                    print(f"  ✅ Добавлено поле direction в существующую таблицу {db_path}")
                except sqlite3.OperationalError as e:
                    print(f"  ⚠️ Не удалось добавить поле direction: {e}")
            else:
                print(f"  ℹ️ Поле direction уже существует в {db_path}")
        
        conn.commit()
        conn.close()
        print(f"✅ Таблица test_messages успешно добавлена в {db_path}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при обновлении {db_path}: {e}")
        return False

if __name__ == '__main__':
    # Пути к базам данных
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    db_1_path = os.path.join(base_dir, 'server_user_1', 'databases', 'user_1_db.db')
    db_2_path = os.path.join(base_dir, 'server_user_2', 'databases', 'user_2_db.db')
    
    print("Обновление баз данных...")
    print("=" * 50)
    
    success_1 = update_db(db_1_path)
    success_2 = update_db(db_2_path)
    
    print("=" * 50)
    if success_1 and success_2:
        print("✅ Все базы данных успешно обновлены!")
    else:
        print("⚠️ Некоторые базы данных не были обновлены. Проверьте ошибки выше.")

