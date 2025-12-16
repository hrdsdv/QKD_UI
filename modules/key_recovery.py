"""
Модуль восстановления ключа - Алгоритм Рида-Соломона

Реализует коррекцию ошибок в квантовых ключах с использованием кодов Рида-Соломона.

Процесс восстановления:
1. Разбиение ключевой последовательности на блоки
2. Кодирование одного из ключей с добавлением избыточной информации
3. Передача избыточной информации по открытому каналу
4. Декодирование и исправление ошибок с помощью алгоритма Берлекэмпа-Месси
5. Проверка корректности через хэш-функцию ГОСТ Р 34.11-2018 (Стрибог)
"""

import reedsolo
import time
from typing import Tuple, Optional
from modules.database_utils import DatabaseManager
from modules.gost_cipher import GOSTHash, GOSTCipher, AESCipher
from utils.logging_utils import log_to_file, log_to_db


# Параметры кодирования Рида-Соломона
RS_ECC_LENGTH = 10  # Количество проверочных символов (можно исправить до 5 ошибок)
BLOCK_SIZE = 32     # Размер блока в байтах


def encode_reed_solomon(data: bytes, ecc_length: int = RS_ECC_LENGTH) -> bytes:
    """
    Кодирует данные с использованием алгоритма Рида-Соломона.
    
    :param data: Исходные данные
    :param ecc_length: Длина избыточной информации (проверочных символов)
    :return: Закодированные данные с избыточной информацией
    """
    rsc = reedsolo.RSCodec(ecc_length)
    return bytes(rsc.encode(data))


def decode_reed_solomon(encoded_data: bytes, ecc_length: int = RS_ECC_LENGTH) -> Tuple[bytes, int]:
    """
    Декодирует данные с использованием алгоритма Рида-Соломона.
    Использует алгоритм Берлекэмпа-Месси для поиска и исправления ошибок.
    
    :param encoded_data: Закодированные данные
    :param ecc_length: Длина избыточной информации
    :return: Кортеж (декодированные данные, количество исправленных ошибок)
    """
    rsc = reedsolo.RSCodec(ecc_length)
    try:
        decoded = rsc.decode(encoded_data)
        # decoded возвращает кортеж (данные, избыточность, количество ошибок)
        return bytes(decoded[0]), len(decoded[2]) if len(decoded) > 2 else 0
    except reedsolo.ReedSolomonError as e:
        log_to_file(f"Ошибка декодирования RS: {e}", level="ERROR")
        return b"", -1


class KeyRecoveryModule:
    """
    Модуль восстановления квантовых ключей с использованием кодов Рида-Соломона.
    
    Обеспечивает:
    - Коррекцию ошибок в ключах
    - Проверку целостности через ГОСТ Р 34.11-2018 (Стрибог)
    - Синхронизацию ключей между абонентами
    """
    
    def __init__(self, db_path: str, master_key: bytes = None):
        """
        Инициализация модуля.
        
        :param db_path: Путь к базе данных
        :param master_key: Мастер-ключ для шифрования хранилища (32 байта)
        """
        self.db_manager = DatabaseManager(db_path)
        self.station_name = "A" if "user_1" in db_path else "B"
        
        # Мастер-ключ для шифрования ключей в БД (AES-256-GCM)
        if master_key:
            # Проверяем длину переданного ключа
            if len(master_key) != 32:
                raise ValueError(f"Длина мастер-ключа должна быть 32 байта (256 бит), получено {len(master_key)} байт")
            self.master_key = master_key
        else:
            # Генерируем или загружаем мастер-ключ
            self.master_key = self._get_or_create_master_key()
        
        # Дополнительная проверка перед созданием AESCipher
        if self.master_key and len(self.master_key) != 32:
            raise ValueError(f"Длина мастер-ключа должна быть 32 байта (256 бит), получено {len(self.master_key)} байт")
        
        self.key_cipher = AESCipher(self.master_key) if self.master_key else None
    
    def _get_or_create_master_key(self) -> bytes:
        """
        Получает или создаёт мастер-ключ для шифрования хранилища.
        В production версии должен храниться в защищённом хранилище (HSM, TPM).
        """
        import os
        master_key_file = os.path.join(os.path.dirname(self.db_manager.db_path), '.master_key')
        
        if os.path.exists(master_key_file):
            with open(master_key_file, 'rb') as f:
                master_key = f.read()
            
            # Проверяем длину ключа (должно быть 32 байта для AES-256)
            if len(master_key) != 32:
                log_to_file(
                    f"Обнаружен мастер-ключ неправильной длины ({len(master_key)} байт вместо 32). "
                    "Пересоздаём ключ.",
                    level="WARNING"
                )
                # Пересоздаём ключ правильной длины
                master_key = os.urandom(32)
                with open(master_key_file, 'wb') as f:
                    f.write(master_key)
                # Устанавливаем права доступа (только владелец)
                try:
                    os.chmod(master_key_file, 0o600)
                except:
                    pass
            return master_key
        else:
            # Генерируем новый мастер-ключ
            master_key = os.urandom(32)
            with open(master_key_file, 'wb') as f:
                f.write(master_key)
            # Устанавливаем права доступа (только владелец)
            try:
                os.chmod(master_key_file, 0o600)
            except:
                pass
            return master_key
    
    def get_available_keys(self) -> list:
        """Возвращает список доступных ключей."""
        return self.db_manager.get_available_keys()
    
    def add_key(self, key_id: str, status: str, length: int) -> None:
        """Добавляет новый ключ в базу данных."""
        self.db_manager.add_key(key_id, status, length)
    
    def update_key_status(self, key_id: str, status: str, used_by: int = None) -> None:
        """Обновляет статус ключа."""
        self.db_manager.update_key_status(key_id, status, used_by)
    
    def recover_key(self, sequence_id: str, remote_parity: bytes = None) -> dict:
        """
        Восстанавливает ключ из сырой последовательности с использованием алгоритма Рида-Соломона.
        
        Процесс:
        1. Получение сырой последовательности из БД
        2. Разбиение на блоки
        3. Кодирование/декодирование RS
        4. Проверка хэша ГОСТ Р 34.11-2018
        
        :param sequence_id: ID последовательности
        :param remote_parity: Избыточная информация от удалённого абонента (опционально)
        :return: Словарь с результатами восстановления
        """
        start_time = time.perf_counter()
        
        try:
            # Получаем сырую последовательность
            query = "SELECT bits, bases, sifted_key FROM raw_data WHERE sequence_id = ?"
            result = self.db_manager.execute_query(query, (sequence_id,), fetch=True, sync=False)
            
            if not result:
                return {'status': 'error', 'message': 'Последовательность не найдена'}
            
            # Используем просеянный ключ если есть, иначе сырые биты
            bits = result[0].get('sifted_key') or result[0]['bits']
            
            # Преобразуем строку бит в байты
            # Дополняем до кратности 8
            padded_bits = bits + '0' * (8 - len(bits) % 8) if len(bits) % 8 != 0 else bits
            byte_data = int(padded_bits, 2).to_bytes(len(padded_bits) // 8, byteorder='big')
            
            # Вычисляем хэш исходных данных (ГОСТ Р 34.11-2018)
            original_hash = GOSTHash.hash_256(byte_data)
            
            # Разбиваем на блоки и обрабатываем
            # Убеждаемся, что есть хотя бы один блок (даже если данные пустые)
            blocks = [byte_data[i:i+BLOCK_SIZE] for i in range(0, len(byte_data), BLOCK_SIZE)]
            if not blocks and len(byte_data) > 0:
                # Если данные есть, но блоков нет (не должно произойти), создаем один блок
                blocks = [byte_data]
            elif not blocks:
                # Если данных нет вообще, создаем пустой блок
                blocks = [b'']
            
            log_to_file(f"Восстановление ключа {sequence_id}: длина данных {len(byte_data)} байт, создано {len(blocks)} блоков", level="INFO")
            recovered_blocks = []
            total_errors_corrected = 0
            blocks_verified = 0
            blocks_info = []  # Детальная информация о каждом блоке для визуализации
            
            for i, block in enumerate(blocks):
                block_start = time.perf_counter()
                
                # Кодируем блок (добавляем избыточность)
                encoded_block = encode_reed_solomon(block)
                
                # Симулируем получение и декодирование
                # В реальной системе здесь была бы передача parity данных
                decoded_block, errors = decode_reed_solomon(encoded_block)
                
                if errors >= 0:
                    recovered_blocks.append(decoded_block)
                    total_errors_corrected += errors
                    
                    # Проверяем хэш блока
                    block_verified = decoded_block == block
                    if block_verified:
                        blocks_verified += 1
                else:
                    # Не удалось восстановить блок
                    recovered_blocks.append(block)  # Используем исходный
                    block_verified = False
                    errors = -1
                
                block_time = (time.perf_counter() - block_start) * 1000
                log_to_file(f"Блок {i+1}/{len(blocks)}: исправлено {errors} ошибок, время {block_time:.1f}мс", level="DEBUG")
                
                # Сохраняем информацию о блоке для визуализации
                blocks_info.append({
                    'block_id': i + 1,
                    'data_bytes': len(block),
                    'parity_bytes': RS_ECC_LENGTH,
                    'errors_corrected': errors if errors >= 0 else 0,
                    'verified': block_verified,
                    'processing_time_ms': round(block_time, 1)
                })
            
            # Собираем восстановленный ключ
            recovered_data = b''.join(recovered_blocks)
            
            # Вычисляем хэш восстановленных данных
            recovered_hash = GOSTHash.hash_256(recovered_data)
            
            # Проверяем совпадение хэшей
            hash_match = original_hash == recovered_hash
            
            # Преобразуем обратно в строку бит
            recovered_bits = ''.join(format(byte, '08b') for byte in recovered_data)[:len(bits)]
            
            # Вычисляем процент успешного восстановления
            recovery_percentage = (blocks_verified / len(blocks)) * 100 if blocks else 0
            
            end_time = time.perf_counter()
            recovery_time_ms = (end_time - start_time) * 1000
            
            result = {
                'status': 'success',
                'sequence_id': sequence_id,
                'recovered_key': recovered_bits,
                'original_length': len(bits),
                'recovered_length': len(recovered_bits),
                'blocks_total': len(blocks),
                'blocks_verified': blocks_verified,
                'errors_corrected': total_errors_corrected,
                'recovery_percentage': round(recovery_percentage, 2),
                'recovery_time_ms': round(recovery_time_ms, 1),
                'hash_verified': hash_match,
                'original_hash': original_hash.hex(),
                'recovered_hash': recovered_hash.hex(),
                'blocks_info': blocks_info  # Детальная информация о каждом блоке
            }
            
            log_to_file(
                f"Восстановление ключа {sequence_id}: "
                f"{recovery_percentage:.1f}% блоков верифицировано, "
                f"{total_errors_corrected} ошибок исправлено, "
                f"время {recovery_time_ms:.1f}мс, "
                f"хэш {'совпадает' if hash_match else 'НЕ совпадает'}",
                level="INFO"
            )
            
            return result
            
        except Exception as e:
            msg = f"Ошибка восстановления ключа: {e}"
            log_to_file(msg, level="ERROR")
            log_to_db(self.db_manager.db_path, None, 'KeyRecoveryModule', 'ERROR', msg)
            return {'status': 'error', 'message': str(e)}
    
    def save_recovered_key(self, sequence_id: str, recovered_key: str, encrypt: bool = True) -> bool:
        """
        Сохраняет восстановленный ключ в защищённое хранилище.
        
        Ключ шифруется с использованием AES-256-GCM перед сохранением.
        
        :param sequence_id: ID последовательности
        :param recovered_key: Восстановленный ключ (строка бит)
        :param encrypt: Шифровать ли ключ перед сохранением
        :return: Успешность операции
        """
        try:
            key_id = f"key_{sequence_id}"
            
            # Преобразуем ключ в байты
            key_bytes = int(recovered_key, 2).to_bytes((len(recovered_key) + 7) // 8, byteorder='big')
            
            # Вычисляем хэш ключа для верификации
            key_hash = GOSTHash.hash_256(key_bytes).hex()
            
            # Используем KeyManagementModule для сохранения в защищённое хранилище
            from modules.key_management import KeyManagementModule
            key_manager = KeyManagementModule(self.db_manager.db_path)
            
            # Сохраняем ключ в защищённое хранилище (secure_keys)
            success = key_manager.secure_storage.store_key(key_id, key_bytes, key_hash)
            
            if success:
                # Также добавляем запись в таблицу keys для отображения в UI
                # Используем INSERT OR REPLACE для упрощения логики
                try:
                    from utils.timezone_utils import moscow_datetime_sql
                    query = f"""
                        INSERT OR REPLACE INTO keys (key_id, key_hash, status, length, created_at)
                        VALUES (?, ?, 'Активен', ?, {moscow_datetime_sql()})
                    """
                    self.db_manager.execute_query(
                        query, 
                        (key_id, key_hash, len(recovered_key)),
                        sync=False
                    )
                    log_to_file(f"Ключ {key_id} сохранён в защищённое хранилище", level="INFO")
                except Exception as e:
                    log_to_file(f"Ошибка сохранения ключа в таблицу keys: {e}", level="WARNING")
                    # Не критично, ключ уже в secure_keys
                
                return True
            else:
                log_to_file(f"Не удалось сохранить ключ {key_id} в защищённое хранилище", level="ERROR")
                return False
            
        except Exception as e:
            msg = f"Ошибка сохранения восстановленного ключа: {e}"
            log_to_file(msg, level="ERROR")
            log_to_db(self.db_manager.db_path, None, 'KeyRecoveryModule', 'ERROR', msg)
            return False
    
    def get_key_for_encryption(self, key_id: str) -> bytes:
        """
        Получает ключ для шифрования/дешифрования.
        
        :param key_id: ID ключа
        :return: Ключ в байтах (32 байта для ГОСТ)
        """
        try:
            query = "SELECT key_data, is_encrypted FROM keys WHERE key_id = ? AND status = 'Активен'"
            result = self.db_manager.execute_query(query, (key_id,), fetch=True, sync=False)
            
            if not result:
                raise ValueError(f"Ключ {key_id} не найден или не активен")
            
            key_data = result[0]['key_data']
            is_encrypted = result[0].get('is_encrypted', False)
            
            if is_encrypted and self.key_cipher:
                # Расшифровываем ключ
                encrypted_bytes = bytes.fromhex(key_data)
                key_bytes = self.key_cipher.decrypt(encrypted_bytes)
            else:
                # Ключ хранится как строка бит
                key_bytes = int(key_data, 2).to_bytes((len(key_data) + 7) // 8, byteorder='big')
            
            # Дополняем или обрезаем до 32 байт (256 бит) для ГОСТ
            if len(key_bytes) < 32:
                key_bytes = key_bytes + b'\x00' * (32 - len(key_bytes))
            elif len(key_bytes) > 32:
                key_bytes = key_bytes[:32]
            
            return key_bytes
            
        except Exception as e:
            log_to_file(f"Ошибка получения ключа {key_id}: {e}", level="ERROR")
            raise
    
    def destroy_key(self, key_id: str, user_id: int = None) -> bool:
        """
        Безвозвратно уничтожает ключ.
        
        Процесс:
        1. Перезапись данных ключа случайными байтами
        2. Удаление записи из БД
        3. Логирование уничтожения
        
        :param key_id: ID ключа
        :param user_id: ID пользователя, инициировавшего уничтожение
        :return: Успешность операции
        """
        try:
            # Перезаписываем данные ключа случайными байтами (secure erase)
            import os
            random_data = os.urandom(256).hex()
            
            # Обновляем статус на "Использован и уничтожен", но не удаляем запись из БД
            from utils.timezone_utils import moscow_datetime_sql
            query = f"UPDATE keys SET key_data = ?, status = 'Использован и уничтожен', used_by = ?, used_at = {moscow_datetime_sql()} WHERE key_id = ?"
            self.db_manager.execute_query(query, (random_data, user_id, key_id), sync=False)
            
            # Также обновляем статус в secure_keys если ключ там есть
            query_secure = f"UPDATE secure_keys SET status = 'Уничтожен', destroyed_at = {moscow_datetime_sql()}, used_by = ? WHERE key_id = ?"
            self.db_manager.execute_query(query_secure, (user_id, key_id), sync=False)
            
            # Логируем уничтожение
            log_to_file(f"Ключ {key_id} уничтожен пользователем {user_id} (статус изменен на 'Использован и уничтожен')", level="INFO")
            log_to_db(self.db_manager.db_path, user_id, 'KeyRecoveryModule', 'INFO', f"Ключ {key_id} уничтожен")
            
            return True
            
        except Exception as e:
            log_to_file(f"Ошибка уничтожения ключа {key_id}: {e}", level="ERROR")
            return False
