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


class KeyManagementModule:
    def __init__(self):
        self.users = [
            {"name": "Пользователь B", "role": "Абонент", "status": "Онлайн", "last_activity": "21.11.2025 11:45:30", "data": None},
            {"name": "Пользователь C", "role": "Абонент", "status": "Онлайн", "last_activity": "21.11.2025 11:42:15", "data": None},
            {"name": "Пользователь D", "role": "Абонент", "status": "Офлайн", "last_activity": "21.11.2025 09:30:00", "data": None},
            {"name": "Пользователь E", "role": "Наблюдатель", "status": "Онлайн", "last_activity": "21.11.2025 11:40:22", "data": None},
        ]

        self.system_logs = []

    def get_users(self):
        """Возвращает список пользователей."""
        return self.users

    def get_system_logs(self):
        """Возвращает системные логи."""
        return self.system_logs


