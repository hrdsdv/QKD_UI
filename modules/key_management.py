"""
Модуль управления ключами - Хранение, ротация, уничтожение

Управление жизненным циклом всех ключей:
- Хранение: Ключи хранятся в зашифрованном виде (AES-256-GCM)
- Ротация: Политика одноразового использования
- Уничтожение: Безвозвратное удаление после использования
- Аудит: Журнал всех операций с ключами
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from utils.timezone_utils import moscow_datetime_sql, format_datetime_for_display
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from utils.logging_utils import log_to_file, log_to_db
from modules.database_utils import DatabaseManager


class SecureKeyStorage:
    """
    Защищённое хранилище ключей с шифрованием AES-256-GCM.
    
    Все ключи хранятся в зашифрованном виде.
    Мастер-ключ должен храниться в защищённом месте (HSM, TPM, или файл с ограниченными правами).
    """
    
    def __init__(self, db_path: str, master_key: bytes = None):
        """
        Инициализация хранилища.
        
        :param db_path: Путь к базе данных
        :param master_key: Мастер-ключ (32 байта). Если не указан, будет создан/загружен автоматически.
        """
        self.db_path = db_path
        if master_key:
            # Проверяем длину переданного ключа
            if len(master_key) != 32:
                raise ValueError(f"Длина мастер-ключа должна быть 32 байта (256 бит), получено {len(master_key)} байт")
            self.master_key = master_key
        else:
            self.master_key = self._load_or_create_master_key()
        
        # Дополнительная проверка перед использованием
        if self.master_key and len(self.master_key) != 32:
            raise ValueError(f"Длина мастер-ключа должна быть 32 байта (256 бит), получено {len(self.master_key)} байт")
        
        self._init_secure_table()
    
    def _load_or_create_master_key(self) -> bytes:
        """Загружает или создаёт мастер-ключ."""
        key_file = os.path.join(os.path.dirname(self.db_path), '.secure_master_key')
        
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                master_key = f.read()
            
            # Проверяем длину ключа (должно быть 32 байта для AES-256)
            if len(master_key) != 32:
                log_to_file(
                    f"Обнаружен мастер-ключ неправильной длины ({len(master_key)} байт вместо 32). "
                    "Пересоздаём ключ.",
                    level="WARNING"
                )
                # Пересоздаём ключ правильной длины
                master_key = get_random_bytes(32)
                with open(key_file, 'wb') as f:
                    f.write(master_key)
                # Устанавливаем права доступа (только владелец)
                try:
                    os.chmod(key_file, 0o600)
                except:
                    pass
                log_to_file("Создан новый мастер-ключ для хранилища", level="INFO")
            return master_key
        else:
            master_key = get_random_bytes(32)
            with open(key_file, 'wb') as f:
                f.write(master_key)
            try:
                os.chmod(key_file, 0o600)  # Только владелец может читать/писать
            except:
                pass
            log_to_file("Создан новый мастер-ключ для хранилища", level="INFO")
            return master_key
    
    def _init_secure_table(self):
        """Инициализирует таблицу для защищённого хранения ключей."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
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
                        destroyed_at TIMESTAMP
                    )
                ''')
                conn.commit()
        except Exception as e:
            log_to_file(f"Ошибка инициализации secure_keys: {e}", level="ERROR")
    
    def store_key(self, key_id: str, key_data: bytes, key_hash: str = None) -> bool:
        """
        Сохраняет ключ в зашифрованном виде.
        
        :param key_id: Идентификатор ключа
        :param key_data: Данные ключа в байтах
        :param key_hash: Хэш ключа для верификации
        :return: Успешность операции
        """
        try:
            # Шифруем ключ с помощью AES-256-GCM
            cipher = AES.new(self.master_key, AES.MODE_GCM)
            encrypted_key, tag = cipher.encrypt_and_digest(key_data)
            nonce = cipher.nonce
            
            # Вычисляем хэш если не передан
            if not key_hash:
                from modules.gost_cipher import GOSTHash
                key_hash = GOSTHash.hash_256(key_data).hex()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f'''
                    INSERT OR REPLACE INTO secure_keys 
                    (key_id, encrypted_key, nonce, tag, key_hash, status, length, created_at)
                    VALUES (?, ?, ?, ?, ?, 'Активен', ?, {moscow_datetime_sql()})
                ''', (key_id, encrypted_key, nonce, tag, key_hash, len(key_data) * 8))
                conn.commit()
            
            log_to_file(f"Ключ {key_id} сохранён в защищённое хранилище", level="INFO")
            return True
            
        except Exception as e:
            log_to_file(f"Ошибка сохранения ключа {key_id}: {e}", level="ERROR")
            return False
    
    def retrieve_key(self, key_id: str) -> Optional[bytes]:
        """
        Извлекает ключ из хранилища.
        
        :param key_id: Идентификатор ключа
        :return: Данные ключа или None
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT encrypted_key, nonce, tag, status 
                    FROM secure_keys WHERE key_id = ?
                ''', (key_id,))
                row = cursor.fetchone()
            
            if not row:
                log_to_file(f"Ключ {key_id} не найден", level="WARNING")
                return None
            
            encrypted_key, nonce, tag, status = row
            
            if status != 'Активен':
                log_to_file(f"Ключ {key_id} недоступен (статус: {status})", level="WARNING")
                return None
            
            # Расшифровываем ключ
            cipher = AES.new(self.master_key, AES.MODE_GCM, nonce=nonce)
            key_data = cipher.decrypt_and_verify(encrypted_key, tag)
            
            return key_data
            
        except Exception as e:
            log_to_file(f"Ошибка извлечения ключа {key_id}: {e}", level="ERROR")
            return None
    
    def mark_as_used(self, key_id: str, user_id: int) -> bool:
        """
        Помечает ключ как использованный (политика одноразового использования).
        
        :param key_id: Идентификатор ключа
        :param user_id: ID пользователя
        :return: Успешность операции
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f'''
                    UPDATE secure_keys 
                    SET status = 'Использован', used_at = {moscow_datetime_sql()}, used_by = ?
                    WHERE key_id = ?
                ''', (user_id, key_id))
                conn.commit()
            
            log_to_file(f"Ключ {key_id} помечен как использованный пользователем {user_id}", level="INFO")
            return True
            
        except Exception as e:
            log_to_file(f"Ошибка пометки ключа {key_id}: {e}", level="ERROR")
            return False
    
    def destroy_key(self, key_id: str, user_id: int = None) -> bool:
        """
        Безвозвратно уничтожает ключ.
        
        Процесс:
        1. Перезапись зашифрованных данных случайными байтами
        2. Обновление статуса в secure_keys и keys
        3. Логирование
        
        :param key_id: Идентификатор ключа
        :param user_id: ID пользователя
        :return: Успешность операции
        """
        try:
            # Перезаписываем данные случайными байтами
            random_data = get_random_bytes(256)
            random_nonce = get_random_bytes(16)
            random_tag = get_random_bytes(16)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Перезаписываем и помечаем как уничтоженный в secure_keys
                cursor.execute(f'''
                    UPDATE secure_keys 
                    SET encrypted_key = ?, nonce = ?, tag = ?,
                        status = 'Уничтожен', destroyed_at = {moscow_datetime_sql()}, used_by = ?
                    WHERE key_id = ?
                ''', (random_data, random_nonce, random_tag, user_id, key_id))
                
                # Обновляем статус в таблице keys на "Использован и уничтожен"
                cursor.execute(f'''
                    UPDATE keys 
                    SET status = 'Использован и уничтожен', 
                        used_by = ?, 
                        used_at = {moscow_datetime_sql()}
                    WHERE key_id = ?
                ''', (user_id, key_id))
                
                # Удаляем запись из secure_keys
                cursor.execute('DELETE FROM secure_keys WHERE key_id = ?', (key_id,))
                conn.commit()
            
            log_to_file(f"Ключ {key_id} безвозвратно уничтожен", level="INFO")
            log_to_db(self.db_path, user_id, 'SecureKeyStorage', 'INFO', f"Ключ {key_id} уничтожен")
            
            return True
            
        except Exception as e:
            log_to_file(f"Ошибка уничтожения ключа {key_id}: {e}", level="ERROR")
            return False


class KeyManagementModule:
    """
    Модуль управления жизненным циклом ключей.
    
    Функции:
    - Хранение ключей в защищённом виде
    - Ротация (одноразовое использование)
    - Уничтожение после использования
    - Аудит всех операций
    """
    
    def __init__(self, db_path: str):
        """
        Инициализация модуля.
        
        :param db_path: Путь к базе данных
        """
        self.db_manager = DatabaseManager(db_path)
        self.secure_storage = SecureKeyStorage(db_path)
        self.db_path = db_path
    
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
        """Возвращает список входящих сообщений в формате для UI."""
        messages = self.db_manager.get_incoming_messages(receiver_id)
        log_to_file(f"DEBUG: get_incoming_messages для receiver_id={receiver_id}, найдено сообщений: {len(messages)}", level="INFO")
        
        # Преобразуем данные в формат для шаблона
        formatted_messages = []
        for msg in messages:
            # Получаем username отправителя из таблицы users
            sender_username = msg.get('sender_name')
            if not sender_username:
                # Если JOIN не нашел пользователя, пытаемся получить username напрямую
                sender_query = "SELECT username FROM users WHERE user_id = ?"
                sender_result = self.db_manager.execute_query(sender_query, (msg.get('sender_id'),), fetch=True, sync=False)
                sender_username = sender_result[0]['username'] if sender_result else 'Неизвестно'
            
            formatted_msg = {
                'id': msg['message_id'],
                'sender': sender_username,  # Используем username вместо sender_name
                'timestamp': format_datetime_for_display(msg.get('sent_at', '')),
                'type': msg.get('message_type', 'text'),
                'key_id': msg.get('key_id', ''),
                'content': msg.get('content', ''),
                'content_hash': msg.get('content_hash', '')
            }
            
            if msg.get('message_type') == 'text':
                # Для текстовых сообщений
                content_length = len(msg.get('content', ''))
                formatted_msg['size'] = content_length
            else:
                # Для файлов
                formatted_msg['filename'] = msg.get('file_name', '')
                formatted_msg['size'] = msg.get('file_size', 0)
                formatted_msg['filetype'] = msg.get('file_type', '')
            
            formatted_messages.append(formatted_msg)
        
        return formatted_messages
    
    def get_sent_messages(self, sender_id: int) -> list:
        """Возвращает список отправленных сообщений в формате для UI."""
        messages = self.db_manager.get_sent_messages(sender_id)
        log_to_file(f"DEBUG: get_sent_messages для sender_id={sender_id}, найдено сообщений: {len(messages)}", level="INFO")
        
        # Преобразуем данные в формат для шаблона
        formatted_messages = []
        for msg in messages:
            # Получаем username получателя из таблицы users
            receiver_username = msg.get('receiver_name')
            if not receiver_username:
                # Если JOIN не нашел пользователя, пытаемся получить username напрямую
                receiver_query = "SELECT username FROM users WHERE user_id = ?"
                receiver_result = self.db_manager.execute_query(receiver_query, (msg.get('receiver_id'),), fetch=True, sync=False)
                receiver_username = receiver_result[0]['username'] if receiver_result else 'Неизвестно'
            
            formatted_msg = {
                'id': msg['message_id'],
                'receiver': receiver_username,
                'timestamp': format_datetime_for_display(msg.get('sent_at', '')),
                'type': msg.get('message_type', 'text'),
                'key_id': msg.get('key_id', ''),
                'content': msg.get('content', ''),
                'content_hash': msg.get('content_hash', ''),
                'plaintext': msg.get('plaintext', '')  # Оригинальный текст
            }
            
            if msg.get('message_type') == 'text':
                # Для текстовых сообщений
                content_length = len(msg.get('content', ''))
                formatted_msg['size'] = content_length
            else:
                # Для файлов
                formatted_msg['filename'] = msg.get('file_name', '')
                formatted_msg['size'] = msg.get('file_size', 0)
                formatted_msg['filetype'] = msg.get('file_type', '')
            
            formatted_messages.append(formatted_msg)
        
        return formatted_messages
    
    def get_available_keys(self) -> list:
        """Возвращает список доступных (активных) ключей."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT key_id, length, status, created_at 
                    FROM secure_keys WHERE status = 'Активен'
                    UNION
                    SELECT key_id, length, status, created_at
                    FROM keys WHERE status = 'Активен'
                    ORDER BY created_at DESC
                ''')
                keys = []
                for row in cursor.fetchall():
                    key_dict = dict(row)
                    # Форматируем время для отображения
                    if key_dict.get('created_at'):
                        key_dict['created_at'] = format_datetime_for_display(key_dict['created_at'])
                    keys.append(key_dict)
                return keys
        except Exception as e:
            log_to_file(f"Ошибка получения списка ключей: {e}", level="ERROR")
            return []
    
    def store_quantum_key(self, key_id: str, key_bits: str) -> bool:
        """
        Сохраняет квантовый ключ в защищённое хранилище.
        
        :param key_id: Идентификатор ключа
        :param key_bits: Ключ в виде строки бит
        :return: Успешность операции
        """
        try:
            # Преобразуем биты в байты
            padded = key_bits + '0' * (8 - len(key_bits) % 8) if len(key_bits) % 8 != 0 else key_bits
            key_bytes = int(padded, 2).to_bytes(len(padded) // 8, byteorder='big')
            
            return self.secure_storage.store_key(key_id, key_bytes)
            
        except Exception as e:
            log_to_file(f"Ошибка сохранения квантового ключа: {e}", level="ERROR")
            return False
    
    def get_key_for_encryption(self, key_id: str) -> Optional[bytes]:
        """
        Получает ключ для шифрования.
        
        :param key_id: Идентификатор ключа
        :return: Ключ в байтах (32 байта для ГОСТ)
        """
        key_data = self.secure_storage.retrieve_key(key_id)
        
        if key_data:
            # Дополняем или обрезаем до 32 байт
            if len(key_data) < 32:
                key_data = key_data + b'\x00' * (32 - len(key_data))
            elif len(key_data) > 32:
                key_data = key_data[:32]
        
        return key_data
    
    def use_key(self, key_id: str, user_id: int) -> bool:
        """
        Использует ключ (помечает как использованный).
        
        :param key_id: Идентификатор ключа
        :param user_id: ID пользователя
        :return: Успешность операции
        """
        success = self.secure_storage.mark_as_used(key_id, user_id)
        
        if success:
            self.add_log(user_id, 'KeyManagement', 'INFO', f"Ключ {key_id} использован")
        
        return success
    
    def destroy_key(self, key_id: str, user_id: int = None) -> bool:
        """
        Уничтожает ключ после использования.
        
        :param key_id: Идентификатор ключа
        :param user_id: ID пользователя
        :return: Успешность операции
        """
        success = self.secure_storage.destroy_key(key_id, user_id)
        
        if success:
            self.add_log(user_id, 'KeyManagement', 'INFO', f"Ключ {key_id} уничтожен")
        
        return success
    
    def get_key_audit_log(self, key_id: str = None) -> list:
        """
        Получает журнал аудита для ключа или всех ключей.
        
        :param key_id: Идентификатор ключа (опционально)
        :return: Список записей аудита
        """
        try:
            if key_id:
                query = """
                    SELECT * FROM logs 
                    WHERE message LIKE ? 
                    ORDER BY created_at DESC
                """
                return self.db_manager.execute_query(query, (f'%{key_id}%',), fetch=True) or []
            else:
                query = """
                    SELECT * FROM logs 
                    WHERE module IN ('KeyManagement', 'KeyRecoveryModule', 'SecureKeyStorage')
                    ORDER BY created_at DESC
                """
                return self.db_manager.execute_query(query, fetch=True) or []
                
        except Exception as e:
            log_to_file(f"Ошибка получения аудита: {e}", level="ERROR")
            return []
