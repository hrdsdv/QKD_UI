import sqlite3
import os

def init_db():
    # Создаем директорию для баз данных, если она не существует
    db_dir = 'databases'
    os.makedirs(db_dir, exist_ok=True)

    # Используем абсолютный путь к файлу базы данных
    db_path = os.path.abspath(os.path.join(db_dir, 'user_2_db.db'))

    # Удаляем старую базу данных, если она существует
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"Удалена старая база данных: {db_path}")

    # Подключаемся к базе данных
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    print(f"Создана новая база данных: {db_path}")

    # Создаем таблицу users
    cursor.execute('''
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        student_id TEXT NOT NULL UNIQUE,
        username TEXT NOT NULL UNIQUE,
        password TEXT NOT NULL,
        role TEXT NOT NULL,
        email TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP
    )
    ''')
    print("Создана таблица users")

    # Создаем остальные таблицы
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS keys (
        key_id TEXT PRIMARY KEY,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        length INTEGER NOT NULL,
        used_at TIMESTAMP,
        used_by INTEGER,
        FOREIGN KEY (used_by) REFERENCES users(user_id)
    )
    ''')
    print("Создана таблица keys")

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        receiver_id INTEGER NOT NULL,
        key_id TEXT NOT NULL,
        message_type TEXT NOT NULL,
        content TEXT,
        file_path TEXT,
        file_name TEXT,
        file_size INTEGER,
        file_type TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_encrypted BOOLEAN DEFAULT TRUE,
        FOREIGN KEY (sender_id) REFERENCES users(user_id),
        FOREIGN KEY (receiver_id) REFERENCES users(user_id),
        FOREIGN KEY (key_id) REFERENCES keys(key_id)
    )
    ''')
    print("Создана таблица messages")

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        module TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    print("Создана таблица logs")

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(user_id)
    )
    ''')
    print("Создана таблица sessions")

    conn.commit()
    conn.close()
    print("База данных успешно инициализирована")

if __name__ == '__main__':
    init_db()
