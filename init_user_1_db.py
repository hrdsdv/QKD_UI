import sqlite3
import os

def init_db():
    # Создаем директорию для баз данных, если она не существует
    db_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'server_user_1', 'databases'))
    os.makedirs(db_dir, exist_ok=True)

    # Используем абсолютный путь к файлу базы данных
    db_path = os.path.join(db_dir, 'user_1_db.db')

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
    CREATE TABLE IF NOT EXISTS users (
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

    # Создаем таблицу raw_data
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS raw_data (
        raw_data_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sequence_id TEXT NOT NULL UNIQUE,
        bits TEXT NOT NULL,
        bases TEXT NOT NULL,
        sifted_key TEXT DEFAULT NULL,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        station TEXT NOT NULL,
        selected_by TEXT DEFAULT NULL,
        is_test BOOLEAN DEFAULT FALSE
    )
    ''')

    print("Создана таблица raw_data")

    # Создаем таблицу keys
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS keys (
        key_id TEXT PRIMARY KEY,
        key_data TEXT,
        key_hash TEXT,
        is_encrypted BOOLEAN DEFAULT FALSE,
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        length INTEGER NOT NULL,
        used_at TIMESTAMP,
        used_by INTEGER,
        FOREIGN KEY (used_by) REFERENCES users(user_id)
    )
    ''')
    print("Создана таблица keys")

    # Создаем таблицу secure_keys (защищённое хранилище ключей)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS secure_keys (
        key_id TEXT PRIMARY KEY,
        encrypted_key BLOB NOT NULL,
        nonce BLOB NOT NULL,
        tag BLOB NOT NULL,
        key_hash TEXT NOT NULL,
        status TEXT DEFAULT 'Активен',
        length INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        used_at TIMESTAMP,
        used_by INTEGER,
        FOREIGN KEY (used_by) REFERENCES users(user_id)
    )
    ''')
    print("Создана таблица secure_keys")

    # Создаем таблицу messages
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER NOT NULL,
        sender_name TEXT,
        receiver_id INTEGER NOT NULL,
        key_id TEXT NOT NULL,
        message_type TEXT NOT NULL,
        content TEXT,
        content_hash TEXT,
        file_path TEXT,
        file_name TEXT,
        file_size INTEGER,
        file_type TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_encrypted BOOLEAN DEFAULT TRUE,
        is_read BOOLEAN DEFAULT FALSE,
        read_at TIMESTAMP,
        FOREIGN KEY (sender_id) REFERENCES users(user_id),
        FOREIGN KEY (receiver_id) REFERENCES users(user_id),
        FOREIGN KEY (key_id) REFERENCES keys(key_id)
    )
    ''')
    print("Создана таблица messages")

    # Создаем таблицу logs
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

    # Создаем таблицу sessions
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

    # Добавление пользователей по умолчанию
    # Абонент А (для сервера 1)
    cursor.execute('''
    INSERT INTO users (first_name, last_name, student_id, username, password, role, email)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('Абонент', 'А', '000000001', 'UserA', 'password123', 'user', 'user_a@example.com'))
    print("Добавлен пользователь Абонент А")
    
    # Абонент Б (для приёма сообщений)
    cursor.execute('''
    INSERT INTO users (first_name, last_name, student_id, username, password, role, email)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('Абонент', 'Б', '000000002', 'UserB', 'password123', 'user', 'user_b@example.com'))
    print("Добавлен пользователь Абонент Б")
    
    # Администратор
    cursor.execute('''
    INSERT INTO users (first_name, last_name, student_id, username, password, role, email)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('Admin', 'Admin', '000000000', 'Admin', '12345678qkd', 'admin', 'admin@example.com'))
    print("Добавлен администратор")

    conn.commit()
    conn.close()
    print("База данных успешно инициализирована")

if __name__ == '__main__':
    init_db()