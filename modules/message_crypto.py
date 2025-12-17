"""
Модуль шифрования/дешифрования сообщений и файлов.

Использует ГОСТ Р 34.12-2018 (Кузнечик) в режиме CTR для шифрования данных
с квантовым ключом, полученным от QKD системы.

Полный цикл:
1. Получение квантового ключа из защищённого хранилища
2. Шифрование текста/файла с использованием ГОСТ
3. Отправка зашифрованных данных получателю
4. Пометка ключа как использованного
5. Дешифрование на стороне получателя
6. Уничтожение ключа после использования
"""

import os
import base64
import time
import json
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from modules.gost_cipher import GOSTCipher, GOSTHash
from modules.key_management import KeyManagementModule
from modules.database_utils import DatabaseManager
from utils.logging_utils import log_to_file, log_to_db


class MessageCrypto:
    """
    Класс для шифрования и дешифрования сообщений/файлов
    с использованием квантовых ключей.
    """
    
    def __init__(self, db_path: str):
        """
        Инициализация модуля.
        
        :param db_path: Путь к базе данных
        """
        self.db_manager = DatabaseManager(db_path)
        self.key_manager = KeyManagementModule(db_path)
        self.station_name = "A" if "user_1" in db_path else "B"
    
    def encrypt_message(self, plaintext: str, key_id: str, sender_id: int, 
                       receiver_id: int) -> Dict[str, Any]:
        """
        Шифрует текстовое сообщение с использованием квантового ключа.
        
        :param plaintext: Исходный текст сообщения
        :param key_id: ID квантового ключа
        :param sender_id: ID отправителя
        :param receiver_id: ID получателя
        :return: Словарь с результатом операции
        """
        start_time = time.perf_counter()
        
        try:
            # 1. Получаем квантовый ключ из защищённого хранилища
            key_bytes = self.key_manager.get_key_for_encryption(key_id)
            if not key_bytes:
                return {
                    'status': 'error',
                    'message': f'Ключ {key_id} не найден или недоступен'
                }
            
            # 2. Создаём шифр ГОСТ
            cipher = GOSTCipher(key_bytes)
            
            # 3. Шифруем сообщение
            plaintext_bytes = plaintext.encode('utf-8')
            ciphertext = cipher.encrypt(plaintext_bytes)
            
            # 4. Вычисляем хэш для проверки целостности
            message_hash = GOSTHash.hash_256(plaintext_bytes).hex()
            
            # 5. Кодируем в base64 для передачи
            ciphertext_b64 = base64.b64encode(ciphertext).decode('ascii')
            
            # 6. Помечаем ключ как использованный
            self.key_manager.use_key(key_id, sender_id)
            
            # 7. Сохраняем сообщение в БД (с оригинальным текстом для отправителя)
            message_id = self._save_encrypted_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
                key_id=key_id,
                message_type='text',
                encrypted_content=ciphertext_b64,
                content_hash=message_hash,
                plaintext=plaintext  # Сохраняем оригинальный текст
            )
            
            end_time = time.perf_counter()
            encryption_time = round((end_time - start_time) * 1000, 2)
            
            log_to_file(
                f"Сообщение зашифровано: key={key_id}, size={len(plaintext)} байт, "
                f"time={encryption_time}мс",
                level="INFO"
            )
            
            return {
                'status': 'success',
                'message_id': message_id,
                'key_id': key_id,
                'ciphertext': ciphertext_b64,
                'hash': message_hash,
                'encryption_time_ms': encryption_time,
                'original_size': len(plaintext),
                'encrypted_size': len(ciphertext)
            }
            
        except Exception as e:
            log_to_file(f"Ошибка шифрования сообщения: {e}", level="ERROR")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def encrypt_file(self, file_content: bytes, file_name: str, key_id: str, 
                    sender_id: int, receiver_id: int) -> Dict[str, Any]:
        """
        Шифрует файл с использованием квантового ключа.
        
        :param file_content: Содержимое файла в байтах
        :param file_name: Имя файла
        :param key_id: ID квантового ключа
        :param sender_id: ID отправителя
        :param receiver_id: ID получателя
        :return: Словарь с результатом операции
        """
        start_time = time.perf_counter()
        
        try:
            # 1. Получаем квантовый ключ из защищённого хранилища
            key_bytes = self.key_manager.get_key_for_encryption(key_id)
            if not key_bytes:
                return {
                    'status': 'error',
                    'message': f'Ключ {key_id} не найден или недоступен'
                }
            
            # 2. Создаём шифр ГОСТ
            cipher = GOSTCipher(key_bytes)
            
            # 3. Шифруем файл
            ciphertext = cipher.encrypt(file_content)
            
            # 4. Вычисляем хэш для проверки целостности
            file_hash = GOSTHash.hash_256(file_content).hex()
            
            # 5. Кодируем в base64 для передачи
            ciphertext_b64 = base64.b64encode(ciphertext).decode('ascii')
            
            # 6. Помечаем ключ как использованный
            self.key_manager.use_key(key_id, sender_id)
            
            # 7. Сохраняем сообщение в БД
            file_size = len(file_content)
            message_id = self._save_encrypted_message(
                sender_id=sender_id,
                receiver_id=receiver_id,
                key_id=key_id,
                message_type='file',
                encrypted_content=ciphertext_b64,
                content_hash=file_hash,
                file_name=file_name,
                file_size=file_size
            )
            
            end_time = time.perf_counter()
            encryption_time = round((end_time - start_time) * 1000, 2)
            
            log_to_file(
                f"Файл зашифрован: {file_name}, key={key_id}, size={len(file_content)} байт, "
                f"time={encryption_time}мс",
                level="INFO"
            )
            
            return {
                'status': 'success',
                'message_id': message_id,
                'key_id': key_id,
                'ciphertext': ciphertext_b64,
                'hash': file_hash,
                'encryption_time_ms': encryption_time,
                'original_size': len(file_content),
                'encrypted_size': len(ciphertext),
                'file_name': file_name
            }
            
        except Exception as e:
            log_to_file(f"Ошибка шифрования файла: {e}", level="ERROR")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def decrypt_message(self, ciphertext_b64: str, key_id: str, 
                       user_id: int, expected_hash: str = None, destroy_key: bool = True) -> Dict[str, Any]:
        """
        Дешифрует текстовое сообщение.
        
        :param ciphertext_b64: Зашифрованное сообщение в base64
        :param key_id: ID квантового ключа
        :param user_id: ID пользователя (получателя)
        :param expected_hash: Ожидаемый хэш для проверки целостности
        :param destroy_key: Уничтожать ли ключ после дешифрования (по умолчанию True)
        :return: Словарь с результатом операции
        """
        start_time = time.perf_counter()
        
        try:
            # 1. Получаем квантовый ключ
            key_bytes = self.key_manager.get_key_for_encryption(key_id)
            if not key_bytes:
                return {
                    'status': 'error',
                    'message': f'Ключ {key_id} не найден или недоступен'
                }
            
            # 2. Декодируем из base64
            ciphertext = base64.b64decode(ciphertext_b64)
            
            # 3. Создаём шифр ГОСТ и дешифруем
            cipher = GOSTCipher(key_bytes)
            plaintext_bytes = cipher.decrypt(ciphertext)
            
            # 4. Проверяем хэш если указан
            hash_verified = True
            if expected_hash:
                actual_hash = GOSTHash.hash_256(plaintext_bytes).hex()
                hash_verified = (actual_hash == expected_hash)
                if not hash_verified:
                    log_to_file(
                        f"Предупреждение: хэш сообщения не совпадает! "
                        f"Ожидался: {expected_hash}, получен: {actual_hash}",
                        level="WARNING"
                    )
            
            # 5. Декодируем текст
            plaintext = plaintext_bytes.decode('utf-8')
            
            # 6. Уничтожаем ключ после использования (политика одноразового использования)
            key_destroyed = False
            if destroy_key:
                self.key_manager.destroy_key(key_id, user_id)
                key_destroyed = True
            
            end_time = time.perf_counter()
            decryption_time = round((end_time - start_time) * 1000, 2)
            
            log_to_file(
                f"Сообщение дешифровано: key={key_id}, size={len(plaintext)} байт, "
                f"time={decryption_time}мс, hash_ok={hash_verified}",
                level="INFO"
            )
            
            return {
                'status': 'success',
                'plaintext': plaintext,
                'key_id': key_id,
                'hash_verified': hash_verified,
                'decryption_time_ms': decryption_time,
                'key_destroyed': key_destroyed
            }
            
        except Exception as e:
            log_to_file(f"Ошибка дешифрования сообщения: {e}", level="ERROR")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def decrypt_file(self, ciphertext_b64: str, key_id: str, 
                    user_id: int, expected_hash: str = None, destroy_key: bool = True) -> Dict[str, Any]:
        """
        Дешифрует файл.
        
        :param ciphertext_b64: Зашифрованный файл в base64
        :param key_id: ID квантового ключа
        :param user_id: ID пользователя (получателя)
        :param expected_hash: Ожидаемый хэш для проверки целостности
        :param destroy_key: Уничтожать ли ключ после дешифрования (по умолчанию True)
        :return: Словарь с результатом операции
        """
        start_time = time.perf_counter()
        
        try:
            # 1. Получаем квантовый ключ
            key_bytes = self.key_manager.get_key_for_encryption(key_id)
            if not key_bytes:
                return {
                    'status': 'error',
                    'message': f'Ключ {key_id} не найден или недоступен'
                }
            
            # 2. Декодируем из base64
            ciphertext = base64.b64decode(ciphertext_b64)
            
            # 3. Создаём шифр ГОСТ
            cipher = GOSTCipher(key_bytes)
            
            # 4. Дешифруем
            plaintext_bytes = cipher.decrypt(ciphertext)
            
            # 5. Проверяем хэш если передан
            hash_verified = False
            if expected_hash:
                actual_hash = GOSTHash.hash_256(plaintext_bytes).hex()
                hash_verified = (actual_hash == expected_hash)
                
                if not hash_verified:
                    log_to_file(
                        f"Предупреждение: хэш файла не совпадает! "
                        f"Ожидалось: {expected_hash}, получено: {actual_hash}",
                        level="WARNING"
                    )
            
            # 6. Уничтожаем ключ после использования (политика одноразового использования)
            key_destroyed = False
            if destroy_key:
                self.key_manager.destroy_key(key_id, user_id)
                key_destroyed = True
            
            end_time = time.perf_counter()
            decryption_time = round((end_time - start_time) * 1000, 2)
            
            log_to_file(
                f"Файл дешифрован: key={key_id}, size={len(plaintext_bytes)} байт, "
                f"time={decryption_time}мс, hash_ok={hash_verified}",
                level="INFO"
            )
            
            return {
                'status': 'success',
                'file_content': plaintext_bytes,
                'key_id': key_id,
                'hash_verified': hash_verified,
                'decryption_time_ms': decryption_time,
                'file_size': len(plaintext_bytes),
                'key_destroyed': key_destroyed
            }
            
        except Exception as e:
            log_to_file(f"Ошибка дешифрования файла: {e}", level="ERROR")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    
    
    def _save_encrypted_message(self, sender_id: int, receiver_id: int, key_id: str,
                               message_type: str, encrypted_content: str, content_hash: str,
                               file_name: str = None, file_size: int = None, plaintext: str = None) -> int:
        """
        Сохраняет зашифрованное сообщение в базе данных.
        
        :param plaintext: Оригинальный незашифрованный текст (сохраняется только на стороне отправителя)
        :return: ID сообщения
        """
        from utils.timezone_utils import moscow_datetime_sql
        
        # Убеждаемся, что поле plaintext существует в таблице
        self.db_manager._ensure_column_exists('messages', 'plaintext', 'TEXT')
        
        # Если plaintext передан, сохраняем его, иначе NULL
        if plaintext:
            query = f"""
                INSERT INTO messages 
                (sender_id, receiver_id, key_id, message_type, content, content_hash,
                 file_name, file_size, plaintext, is_encrypted, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, {moscow_datetime_sql()})
            """
            self.db_manager.execute_query(
                query,
                (sender_id, receiver_id, key_id, message_type, encrypted_content,
                 content_hash, file_name, file_size, plaintext),
                sync=False
            )
        else:
            query = f"""
                INSERT INTO messages 
                (sender_id, receiver_id, key_id, message_type, content, content_hash,
                 file_name, file_size, is_encrypted, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, {moscow_datetime_sql()})
            """
            self.db_manager.execute_query(
                query,
                (sender_id, receiver_id, key_id, message_type, encrypted_content,
                 content_hash, file_name, file_size),
                sync=False
            )
        
        # Получаем ID вставленной записи
        result = self.db_manager.execute_query(
            "SELECT last_insert_rowid() as id",
            fetch=True, sync=False
        )
        return result[0]['id'] if result else 0
    
    def get_encrypted_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """
        Получает зашифрованное сообщение из БД.
        
        :param message_id: ID сообщения
        :return: Данные сообщения или None
        """
        # Используем LEFT JOIN и COALESCE для получения sender_name
        # Если пользователь не найден в локальной БД (отправитель с другого сервера),
        # используем сохраненное sender_name из таблицы messages
        query = """
            SELECT m.*, 
                   COALESCE(u.username, m.sender_name, 'Неизвестно') as sender_name
            FROM messages m
            LEFT JOIN users u ON m.sender_id = u.user_id
            WHERE m.message_id = ?
        """
        result = self.db_manager.execute_query(query, (message_id,), fetch=True, sync=False)
        return result[0] if result else None
    
    def mark_message_as_read(self, message_id: int, user_id: int) -> bool:
        """
        Помечает сообщение как прочитанное.
        
        :param message_id: ID сообщения
        :param user_id: ID пользователя
        :return: Успешность операции
        """
        try:
            from utils.timezone_utils import moscow_datetime_sql
            query = f"""
                UPDATE messages 
                SET is_read = 1, read_at = {moscow_datetime_sql()}
                WHERE message_id = ? AND receiver_id = ?
            """
            self.db_manager.execute_query(query, (message_id, user_id), sync=False)
            return True
        except Exception as e:
            log_to_file(f"Ошибка пометки сообщения: {e}", level="ERROR")
            return False

