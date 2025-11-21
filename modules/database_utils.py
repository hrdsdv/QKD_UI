import sqlite3
import os
from typing import Optional, List, Dict, Any

class DatabaseManager:
    def __init__(self, db_path: str):
        # Преобразуем относительный путь в абсолютный
        self.db_path = os.path.abspath(db_path)
        # Убедимся, что директория существует
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def execute_query(self, query: str, parameters: tuple = (), fetch: bool = False) -> Optional[List[Dict[str, Any]]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, parameters)
            if fetch:
                columns = [column[0] for column in cursor.description]
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                return results
            conn.commit()

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM users WHERE username = ?"
        result = self.execute_query(query, (username,), fetch=True)
        return result[0] if result else None

    def register_user(self, first_name: str, last_name: str, student_id: str, username: str, password: str,
                      role: str = 'user') -> int:
        """Регистрирует нового пользователя."""
        query = "INSERT INTO users (first_name, last_name, student_id, username, password, role) VALUES (?, ?, ?, ?, ?, ?)"
        self.execute_query(query, (first_name, last_name, student_id, username, password, role))
        return self.get_last_insert_id()

    def get_last_insert_id(self) -> int:
        """Возвращает ID последней добавленной записи."""
        query = "SELECT last_insert_rowid()"
        result = self.execute_query(query, fetch=True)
        return result[0][0]

    def add_user(self, username: str, password: str, role: str, email: str) -> int:
        query = "INSERT INTO users (username, password, role, email) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (username, password, role, email))
        return self.get_last_insert_id()

    def get_last_insert_id(self) -> int:
        query = "SELECT last_insert_rowid()"
        result = self.execute_query(query, fetch=True)
        return result[0][0]

    def add_key(self, key_id: str, status: str, length: int) -> None:
        query = "INSERT INTO keys (key_id, status, length) VALUES (?, ?, ?)"
        self.execute_query(query, (key_id, status, length))

    def get_available_keys(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM keys WHERE status = 'Активен'"
        return self.execute_query(query, fetch=True) or []

    def update_key_status(self, key_id: str, status: str, used_by: Optional[int] = None) -> None:
        query = "UPDATE keys SET status = ?, used_by = ?, used_at = CURRENT_TIMESTAMP WHERE key_id = ?"
        self.execute_query(query, (status, used_by, key_id))

    def add_message(self, sender_id: int, receiver_id: int, key_id: str, message_type: str,
                   content: Optional[str] = None, file_path: Optional[str] = None,
                   file_name: Optional[str] = None, file_size: Optional[int] = None,
                   file_type: Optional[str] = None) -> int:
        query = '''
        INSERT INTO messages (sender_id, receiver_id, key_id, message_type, content, file_path, file_name, file_size, file_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        self.execute_query(query, (sender_id, receiver_id, key_id, message_type, content, file_path, file_name, file_size, file_type))
        return self.get_last_insert_id()

    def get_incoming_messages(self, receiver_id: int) -> List[Dict[str, Any]]:
        query = '''
        SELECT m.*, u.username as sender_name
        FROM messages m
        JOIN users u ON m.sender_id = u.user_id
        WHERE m.receiver_id = ? AND m.is_encrypted = 1
        ORDER BY m.sent_at DESC
        '''
        return self.execute_query(query, (receiver_id,), fetch=True) or []

    def add_log(self, user_id: Optional[int], module: str, level: str, message: str) -> None:
        query = "INSERT INTO logs (user_id, module, level, message) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (user_id, module, level, message))

    def get_system_logs(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM logs ORDER BY created_at DESC"
        return self.execute_query(query, fetch=True) or []
