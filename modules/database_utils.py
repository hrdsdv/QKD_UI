import sqlite3
import os
from typing import Optional, List, Dict, Any
from utils.logging_utils import log_to_file, log_to_db
from utils.timezone_utils import moscow_datetime_sql

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
        """Добавляет или обновляет ключ в БД."""
        query = f"INSERT OR REPLACE INTO keys (key_id, status, length, created_at) VALUES (?, ?, ?, {moscow_datetime_sql()})"
        self.execute_query(query, (key_id, status, length), sync=False)

    def get_available_keys(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM keys WHERE status = 'Активен'"
        return self.execute_query(query, fetch=True, sync=False) or []

    def update_key_status(self, key_id: str, status: str, used_by: Optional[int] = None) -> None:
        query = f"UPDATE keys SET status = ?, used_by = ?, used_at = {moscow_datetime_sql()} WHERE key_id = ?"
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
        """
        Возвращает входящие сообщения для получателя.
        Изначальная версия: просто получает все зашифрованные сообщения для получателя.
        """
        from utils.logging_utils import log_to_file
        
        # Убеждаемся, что поле plaintext существует в таблице (может использоваться в других запросах)
        try:
            self.execute_query(
                "ALTER TABLE messages ADD COLUMN plaintext TEXT",
                sync=False
            )
        except:
            pass  # Поле уже существует
        
        # Сначала получаем ВСЕ сообщения для получателя для отладки
        debug_query = '''
        SELECT m.message_id, m.sender_id, m.receiver_id, m.is_encrypted
        FROM messages m
        WHERE m.receiver_id = ?
        ORDER BY m.sent_at DESC
        '''
        debug_result = self.execute_query(debug_query, (receiver_id,), fetch=True, sync=False) or []
        log_to_file(f"DEBUG: database_utils.get_incoming_messages: Все сообщения для receiver_id={receiver_id}: {len(debug_result)}", level="INFO")
        for msg in debug_result[:5]:  # Показываем первые 5 для отладки
            log_to_file(f"DEBUG: message_id={msg.get('message_id')}, sender_id={msg.get('sender_id')}, receiver_id={msg.get('receiver_id')}, is_encrypted={msg.get('is_encrypted')}", level="INFO")
        
        # Получаем все зашифрованные сообщения для получателя
        # Используем COALESCE для обработки NULL значений is_encrypted
        query = '''
        SELECT m.*, u.username as sender_name
        FROM messages m
        LEFT JOIN users u ON m.sender_id = u.user_id
        WHERE m.receiver_id = ? AND (m.is_encrypted = 1 OR m.is_encrypted = '1')
        ORDER BY m.sent_at DESC
        '''
        result = self.execute_query(query, (receiver_id,), fetch=True, sync=False) or []
        log_to_file(f"DEBUG: database_utils.get_incoming_messages: Найдено с фильтром is_encrypted=1: {len(result)}", level="INFO")
        if result:
            log_to_file(f"DEBUG: Первое сообщение из БД: message_id={result[0].get('message_id')}, sender_name={result[0].get('sender_name')}, receiver_id={result[0].get('receiver_id')}, is_encrypted={result[0].get('is_encrypted')}", level="INFO")
        return result

    def add_log(self, user_id: Optional[int], module: str, level: str, message: str) -> None:
        query = "INSERT INTO logs (user_id, module, level, message) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (user_id, module, level, message), sync=False)

    def get_system_logs(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM logs ORDER BY created_at DESC"
        return self.execute_query(query, fetch=True, sync=False) or []

    def get_sent_messages(self, sender_id: int) -> List[Dict[str, Any]]:
        """
        Возвращает отправленные сообщения только для пользователя, который действительно существует
        в локальной БД (т.е. сообщения, отправленные с этого сервера).
        Критерий: сообщение считается отправленным, если у него есть поле plaintext (оно сохраняется только при отправке).
        """
        # Проверяем, что sender_id существует в локальной БД users
        user_check = self.execute_query(
            "SELECT user_id FROM users WHERE user_id = ?",
            (sender_id,),
            fetch=True,
            sync=False
        )
        
        if not user_check:
            # Пользователь не найден в локальной БД - возвращаем пустой список
            from utils.logging_utils import log_to_file
            log_to_file(f"DEBUG: database_utils.get_sent_messages: sender_id={sender_id} не найден в локальной БД, возвращаем пустой список", level="INFO")
            return []
        
        # Убеждаемся, что поле plaintext существует в таблице
        try:
            self.execute_query(
                "ALTER TABLE messages ADD COLUMN plaintext TEXT",
                sync=False
            )
        except:
            pass  # Поле уже существует
        
        # Получаем только сообщения, которые были отправлены с этого сервера
        # Критерий: наличие поля plaintext (оно сохраняется только при отправке через encrypt_message)
        query = '''
        SELECT m.*, u.username as receiver_name
        FROM messages m
        LEFT JOIN users u ON m.receiver_id = u.user_id
        WHERE m.sender_id = ? AND m.is_encrypted = 1 
        AND COALESCE(m.plaintext, '') != ''
        ORDER BY m.sent_at DESC
        '''
        result = self.execute_query(query, (sender_id,), fetch=True, sync=False) or []
        from utils.logging_utils import log_to_file
        log_to_file(f"DEBUG: database_utils.get_sent_messages для sender_id={sender_id}, найдено отправленных: {len(result)}", level="INFO")
        return result

    def add_log(self, user_id: Optional[int], module: str, level: str, message: str) -> None:
        query = "INSERT INTO logs (user_id, module, level, message) VALUES (?, ?, ?, ?)"
        self.execute_query(query, (user_id, module, level, message), sync=False)

    def get_system_logs(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM logs ORDER BY created_at DESC"
        return self.execute_query(query, fetch=True, sync=False) or []
