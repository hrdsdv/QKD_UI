"""
Скрипт для добавления колонки plaintext в таблицу messages существующих баз данных.
Запустите этот скрипт один раз для обновления структуры БД.
"""
import sqlite3
import os

def migrate_db(db_path):
    """Добавляет колонку plaintext в таблицу messages существующей БД."""
    if not os.path.exists(db_path):
        print(f"База данных не найдена: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Проверяем, существует ли уже колонка plaintext
        cursor.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'plaintext' not in columns:
            try:
                cursor.execute("ALTER TABLE messages ADD COLUMN plaintext TEXT")
                conn.commit()
                print(f"  ✅ Добавлена колонка plaintext в таблицу messages в {db_path}")
            except sqlite3.OperationalError as e:
                print(f"  ⚠️ Не удалось добавить колонку plaintext: {e}")
                conn.close()
                return False
        else:
            print(f"  ℹ️ Колонка plaintext уже существует в {db_path}")
        
        conn.close()
        print(f"✅ База данных {db_path} успешно обновлена")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при обновлении {db_path}: {e}")
        return False

if __name__ == '__main__':
    # Пути к базам данных
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    db_1_path = os.path.join(base_dir, 'databases', 'user_1_db.db')
    db_2_path = os.path.join(base_dir, 'databases', 'user_2_db.db')
    
    # Также проверяем альтернативные пути
    alt_db_1_path = os.path.join(base_dir, 'server_user_1', 'databases', 'user_1_db.db')
    alt_db_2_path = os.path.join(base_dir, 'server_user_2', 'databases', 'user_2_db.db')
    
    print("Миграция баз данных: добавление колонки plaintext")
    print("=" * 60)
    
    success_count = 0
    total_count = 0
    
    # Проверяем все возможные пути
    for db_path in [db_1_path, db_2_path, alt_db_1_path, alt_db_2_path]:
        if os.path.exists(db_path):
            total_count += 1
            if migrate_db(db_path):
                success_count += 1
    
    print("=" * 60)
    if total_count == 0:
        print("⚠️ Базы данных не найдены. Убедитесь, что они существуют.")
    elif success_count == total_count:
        print(f"✅ Все базы данных ({success_count}) успешно обновлены!")
    else:
        print(f"⚠️ Обновлено {success_count} из {total_count} баз данных. Проверьте ошибки выше.")











