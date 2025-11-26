import sqlite3
import os
from typing import Optional, List, Dict, Any
from utils.logging_utils import log_to_file, log_to_db

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        # Убираем автоматическую синхронизацию между базами - каждый абонент работает со своей БД
        self.second_db_path = None
        log_to_file(f"Подключение к базе данных: {self.db_path}", level="INFO")

    def execute_query(self, query: str, parameters: tuple = (), fetch: bool = False, sync: bool = False) -> Optional[List[Dict[str, Any]]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, parameters)
                if fetch:
                    columns = [column[0] for column in cursor.description]
                    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                else:
                    results = None
                conn.commit()
                # Синхронизация отключена - каждая БД независима
                return results
        except sqlite3.Error as e:
            err_msg = f"Ошибка при выполнении запроса: {query}, параметры: {parameters}, ошибка: {e}"
            log_to_file(err_msg, level="ERROR")
            try:
                log_to_db(self.db_path, None, 'DatabaseManager', 'ERROR', err_msg)
            except Exception:
                pass
            raise

    # Метод sync_query больше не используется - синхронизация через REST API
    def sync_query(self, query: str, parameters: tuple):
        pass

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM users WHERE username = ?"
        result = self.execute_query(query, (username,), fetch=True, sync=False)
        return result[0] if result else None

    def register_user(self, first_name: str, last_name: str, student_id: str, username: str, password: str,
                      role: str = 'user') -> int:
        try:
            query = "INSERT INTO users (first_name, last_name, student_id, username, password, role) VALUES (?, ?, ?, ?, ?, ?)"
            self.execute_query(query, (first_name, last_name, student_id, username, password, role), sync=False)
            return self.get_last_insert_id()
        except Exception as e:
            log_to_file(f"Ошибка при регистрации пользователя: {e}", level="ERROR")
            raise

    def get_last_insert_id(self) -> int:
        try:
            query = "SELECT last_insert_rowid()"
            result = self.execute_query(query, fetch=True, sync=False)
            if result and result[0]:
                return result[0]['last_insert_rowid()']
            else:
                raise ValueError("Не удалось получить ID последней добавленной записи.")
        except Exception as e:
            log_to_file(f"Ошибка при получении ID последней добавленной записи: {e}", level="ERROR")
            raise

    def add_key(self, key_id: str, status: str, length: int) -> None:
        query = "INSERT INTO keys (key_id, status, length) VALUES (?, ?, ?)"
        self.execute_query(query, (key_id, status, length), sync=False)

    def get_available_keys(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM keys WHERE status = 'Активен'"
        return self.execute_query(query, fetch=True, sync=False) or []

    def update_key_status(self, key_id: str, status: str, used_by: Optional[int] = None) -> None:
        query = "UPDATE keys SET status = ?, used_by = ?, used_at = CURRENT_TIMESTAMP WHERE key_id = ?"
        self.execute_query(query, (status, used_by, key_id), sync=False)

    def add_message(self, sender_id: int, receiver_id: int, key_id: str, message_type: str,
                   content: Optional[str] = None, file_path: Optional[str] = None,
                   file_name: Optional[str] = None, file_size: Optional[int] = None,
                   file_type: Optional[str] = None) -> int:
        query = '''
        INSERT INTO messages (sender_id, receiver_id, key_id, message_type, content, file_path, file_name, file_size, file_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        self.execute_query(query, (sender_id, receiver_id, key_id, message_type, content, file_path, file_name, file_size, file_type), sync=False)
        return self.get_last_insert_id()

    def get_incoming_messages(self, receiver_id: int) -> List[Dict[str, Any]]:
        query = '''
        SELECT m.*
        FROM messages m
        WHERE m.receiver_id = ? AND m.is_encrypted = 1
        ORDER BY m.sent_at DESC
        '''
        return self.execute_query(query, (receiver_id,), fetch=True, sync=False) or []

    def add_log(self, user_id: Optional[int], module: str, level: str, message: str) -> None:
        query = "INSERT INTO logs (user_id, module, level, message) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (user_id, module, level, message), sync=False)

    def get_system_logs(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM logs ORDER BY created_at DESC"
        return self.execute_query(query, fetch=True, sync=False) or []
