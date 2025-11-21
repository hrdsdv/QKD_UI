import sqlite3
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

class KeyManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Инициализирует базу данных."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS keys (
                    key_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key_data BLOB NOT NULL,
                    is_used BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()

    def store_key(self, key_data: bytes) -> int:
        """Хранит ключ в базе данных."""
        cipher = AES.new(get_random_bytes(32), AES.MODE_GCM)
        encrypted_key, tag = cipher.encrypt_and_digest(key_data)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO keys (key_data, is_used)
                VALUES (?, ?)
            ''', (encrypted_key, False))
            conn.commit()
            return cursor.lastrowid

    def get_key(self, key_id: int) -> bytes:
        """Получает ключ из базы данных."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key_data FROM keys WHERE key_id = ?', (key_id,))
            encrypted_key = cursor.fetchone()[0]

        cipher = AES.new(get_random_bytes(32), AES.MODE_GCM, nonce=get_random_bytes(16))
        return cipher.decrypt(encrypted_key)

from .database_utils import DatabaseManager

class KeyManagementModule:
    def __init__(self, db_path: str):
        self.db_manager = DatabaseManager(db_path)

    def get_users(self) -> list:
        """Возвращает список пользователей."""
        return self.db_manager.execute_query("SELECT * FROM users", fetch=True) or []

    def get_system_logs(self) -> list:
        """Возвращает системные логи."""
        return self.db_manager.get_system_logs()

    def add_log(self, user_id: int, module: str, level: str, message: str) -> None:
        """Добавляет запись в системный журнал."""
        self.db_manager.add_log(user_id, module, level, message)

    def get_incoming_messages(self, receiver_id: int) -> list:
        """Возвращает список входящих сообщений."""
        return self.db_manager.get_incoming_messages(receiver_id)


