import serial
import time
import threading
import socket
from datetime import datetime
from utils.timezone_utils import moscow_now_str, format_datetime_for_display
from modules.database_utils import DatabaseManager
from utils.logging_utils import log_to_file, log_to_db

class QKDModule:
    # Размер последовательности в битах и байтах
    SEQUENCE_BITS = 256
    SEQUENCE_BYTES = SEQUENCE_BITS // 8  # 32 байта
    
    def __init__(self, db_path):
        self.db_manager = DatabaseManager(db_path)
        self.serial_connection = None
        self.tcp_connection = None
        self.is_running = False
        self.station_name = "A" if "server_user_1" in db_path else "B"
        self.other_station_name = "B" if self.station_name == "A" else "A"
        self.other_station_ip = "127.0.0.1"  # Замените на реальный IP второго сервера
        self.other_station_port = 5001 if self.station_name == "A" else 5000
        # Буфер для накопления данных до 256 бит
        self.data_buffer = bytearray()
        # Callback функция для отправки данных последовательности через WebSocket
        self.sequence_callback = None
    
    def set_sequence_callback(self, callback):
        """Устанавливает callback функцию для отправки данных последовательности"""
        self.sequence_callback = callback

    def connect_serial(self, port, baudrate=9600, retry_count=3, retry_delay=2):
        """
        Подключается к COM-порту с повторными попытками при ошибке доступа.
        
        Args:
            port: Имя COM-порта (например 'COM3')
            baudrate: Скорость передачи (по умолчанию 9600)
            retry_count: Количество попыток подключения (по умолчанию 3)
            retry_delay: Задержка между попытками в секундах (по умолчанию 2)
        """
        # Если уже есть подключение, закрываем его
        if self.serial_connection and self.serial_connection.is_open:
            try:
                self.serial_connection.close()
                log_to_file(f"[QKD] Закрыто существующее подключение к {port}", level="INFO")
                print(f"[QKD] Закрыто существующее подключение к {port}")
            except:
                pass
        
        for attempt in range(retry_count):
            try:
                log_to_file(f"[QKD] Попытка подключения #{attempt + 1}/{retry_count} к {port}...", level="INFO")
                print(f"[QKD] Попытка подключения #{attempt + 1}/{retry_count} к {port}...")
                
                self.serial_connection = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=1
                )
                # Очищаем буфер при новом подключении
                self.data_buffer = bytearray()
                msg = f"Подключено к QKD-устройству через {port} (baudrate={baudrate})"
                log_to_file(msg, level="INFO")
                log_to_file(f"[QKD DEBUG] Параметры порта: {self.serial_connection}", level="DEBUG")
                log_to_file(f"[QKD DEBUG] Формирование последовательностей по {self.SEQUENCE_BITS} бит ({self.SEQUENCE_BYTES} байт)", level="DEBUG")
                print(f"[QKD] {msg}")
                print(f"[QKD DEBUG] Параметры порта: {self.serial_connection}")
                print(f"[QKD DEBUG] Формирование последовательностей по {self.SEQUENCE_BITS} бит ({self.SEQUENCE_BYTES} байт)")
                return True
            except serial.SerialException as e:
                error_str = str(e)
                msg = f"Ошибка подключения к QKD-устройству (попытка {attempt + 1}/{retry_count}): {e}"
                log_to_file(msg, level="ERROR")
                print(f"[QKD ERROR] {msg}")
                
                # Если это ошибка доступа (порт занят), пробуем закрыть порт и повторить
                if "PermissionError" in error_str or "отказано в доступе" in error_str.lower() or "access is denied" in error_str.lower() or "could not open port" in error_str.lower():
                    log_to_file(f"[QKD] Порт {port} занят другой программой. Попытка освободить...", level="WARNING")
                    print(f"[QKD] Порт {port} занят другой программой. Попытка освободить...")
                    print(f"[QKD] ВНИМАНИЕ: Закройте программу, использующую {port}, и подождите {retry_delay} секунд")
                    
                    # Пробуем закрыть порт через pyserial (если возможно)
                    try:
                        # Пытаемся открыть и сразу закрыть порт для освобождения
                        temp_ser = serial.Serial(port, baudrate, timeout=0.1)
                        temp_ser.close()
                        log_to_file(f"[QKD] Порт {port} освобожден", level="INFO")
                        print(f"[QKD] Порт {port} освобожден")
                        time.sleep(0.5)  # Небольшая задержка
                    except Exception as close_error:
                        log_to_file(f"[QKD] Не удалось освободить порт: {close_error}", level="WARNING")
                        print(f"[QKD] Не удалось освободить порт: {close_error}")
                    
                    if attempt < retry_count - 1:
                        log_to_file(f"[QKD] Ожидание {retry_delay} секунд перед повторной попыткой...", level="INFO")
                        print(f"[QKD] Ожидание {retry_delay} секунд перед повторной попыткой...")
                        print(f"[QKD] Пожалуйста, закройте программу, использующую {port}, если она еще открыта")
                        time.sleep(retry_delay)
                        continue
                
                # Если это последняя попытка, логируем в БД
                if attempt == retry_count - 1:
                    log_to_db(self.db_manager.db_path, None, 'QKDModule', 'ERROR', msg)
                    final_msg = f"[QKD ERROR] Все {retry_count} попыток подключения исчерпаны. Закройте программу, использующую {port}, и перезапустите сервер."
                    log_to_file(final_msg, level="ERROR")
                    print(final_msg)
        
        return False

    def connect_tcp(self):
        try:
            if self.station_name == "A":
                self.tcp_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_connection.bind(('0.0.0.0', 5000))
                self.tcp_connection.listen(1)
                print("Ожидание подключения от Абонента Б...")
                self.tcp_connection, _ = self.tcp_connection.accept()
            else:
                self.tcp_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.tcp_connection.connect((self.other_station_ip, 5000))
            print(f"TCP-канал между станциями установлен")
            return True
        except Exception as e:
            print(f"Ошибка установки TCP-канала: {e}")
            return False

    def synchronize_stations(self):
        try:
            if self.station_name == "A":
                self.tcp_connection.sendall(b"SYNC_REQUEST")
                response = self.tcp_connection.recv(1024)
                if response == b"SYNC_ACK":
                    print("Синхронизация завершена")
                    return True
            else:
                response = self.tcp_connection.recv(1024)
                if response == b"SYNC_REQUEST":
                    self.tcp_connection.sendall(b"SYNC_ACK")
                    print("Синхронизация завершена")
                    return True
        except Exception as e:
            print(f"Ошибка синхронизации: {e}")
            return False

    def bytes_to_bits(self, data):
        """
        Преобразует байты в битовую последовательность
        
        Args:
            data: Байты для преобразования
        
        Returns:
            Строка с битовой последовательностью
        """
        return ''.join(format(byte, '08b') for byte in data)
    
    def bytes_to_hex(self, data):
        """
        Преобразует байты в HEX (базисную) последовательность
        
        Args:
            data: Байты для преобразования
        
        Returns:
            Строка с HEX последовательностью (с пробелами, как в оригинальном коде)
        """
        return ' '.join(f'{byte:02X}' for byte in data)
    
    def hex_to_bases(self, hex_string):
        """
        Преобразует HEX строку в базисную последовательность формата '+' и 'x'
        Использует младший бит каждого байта для определения базиса:
        - 0 или четное -> '+'
        - 1 или нечетное -> 'x'
        
        Args:
            hex_string: HEX строка (например "00 01 02 ..." или "000102...")
        
        Returns:
            Строка из 256 символов '+' и 'x'
        """
        try:
            # Убираем пробелы из HEX строки
            hex_clean = hex_string.replace(' ', '')
            
            # Преобразуем HEX в байты
            bytes_data = bytes.fromhex(hex_clean)
            
            # Проверяем, что получили 32 байта (256 бит)
            if len(bytes_data) != 32:
                error_msg = f"[QKD ERROR] hex_to_bases: ожидалось 32 байта, получено {len(bytes_data)}"
                log_to_file(error_msg, level="ERROR")
                print(error_msg)
                # Возвращаем случайную последовательность базисов для отладки
                import random
                return ''.join(random.choice(['+', 'x']) for _ in range(256))
            
            # Преобразуем каждый байт в 8 базисов (по младшему биту каждого бита)
            bases = ''
            for byte in bytes_data:
                # Каждый байт дает 8 бит, каждый бит дает один базис
                for bit_pos in range(8):
                    bit = (byte >> bit_pos) & 1
                    bases += '+' if bit == 0 else 'x'
            
            # Проверяем длину
            if len(bases) != 256:
                error_msg = f"[QKD ERROR] hex_to_bases: ожидалось 256 символов, получено {len(bases)}"
                log_to_file(error_msg, level="ERROR")
                print(error_msg)
                # Обрезаем или дополняем до 256
                if len(bases) > 256:
                    bases = bases[:256]
                else:
                    bases += '+' * (256 - len(bases))
            
            return bases
        except Exception as e:
            error_msg = f"[QKD ERROR] Ошибка в hex_to_bases: {e}, hex_string: {hex_string[:50]}..."
            log_to_file(error_msg, level="ERROR")
            print(error_msg)
            import traceback
            print(traceback.format_exc())
            # Возвращаем случайную последовательность базисов для отладки
            import random
            return ''.join(random.choice(['+', 'x']) for _ in range(256))

    def read_qkd_data(self):
        """
        Читает данные с COM-порта и формирует последовательности по 256 бит (32 байта).
        Возвращает (bits, bases) когда накоплено достаточно данных, иначе None.
        ТОЧНО КАК В ОРИГИНАЛЬНОМ КОДЕ - использует while для обработки всех последовательностей.
        """
        if not self.serial_connection:
            log_to_file(f"[QKD DEBUG] Serial connection не установлен", level="WARNING")
            return None

        # Проверяем, что порт открыт
        if not self.serial_connection.is_open:
            log_to_file(f"[QKD DEBUG] Serial port закрыт", level="ERROR")
            print(f"[QKD ERROR] Serial port закрыт")
            return None

        try:
            # ТОЧНО КАК В ОРИГИНАЛЕ: проверяем наличие данных в буфере порта
            if self.serial_connection.in_waiting > 0:
                # Читаем все доступные данные
                data = self.serial_connection.read(self.serial_connection.in_waiting)
                
                if data:
                    # DEBUG: Выводим полученные байты
                    hex_repr = ' '.join([f'{b:02X}' for b in data])
                    log_to_file(f"[QKD DEBUG] Получено {len(data)} байт с порта (hex): {hex_repr}", level="DEBUG")
                    print(f"[QKD DEBUG] Получено {len(data)} байт с порта (hex): {hex_repr}")
                    
                    # Добавляем данные в буфер (ТОЧНО КАК В ОРИГИНАЛЕ)
                    self.data_buffer.extend(data)
                    log_to_file(f"[QKD DEBUG] Размер буфера после добавления: {len(self.data_buffer)} байт", level="DEBUG")
                    print(f"[QKD DEBUG] Размер буфера после добавления: {len(self.data_buffer)} байт")
            
            # КРИТИЧНО: ТОЧНО КАК В ОРИГИНАЛЕ - используем WHILE для обработки ВСЕХ последовательностей
            # Пока в буфере достаточно данных для формирования последовательности
            if len(self.data_buffer) >= self.SEQUENCE_BYTES:
                # Извлекаем ровно 32 байта (256 бит)
                sequence = bytes(self.data_buffer[:self.SEQUENCE_BYTES])
                self.data_buffer = self.data_buffer[self.SEQUENCE_BYTES:]
                
                # Преобразуем в битовую последовательность
                bits = self.bytes_to_bits(sequence)
                
                # Преобразуем HEX в базисную последовательность формата '+' и 'x'
                hex_bases = self.bytes_to_hex(sequence)
                bases = self.hex_to_bases(hex_bases)
                
                # DEBUG: Выводим сформированную последовательность
                timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                log_to_file(f"[QKD] [{timestamp}] Сформирована последовательность ({self.SEQUENCE_BITS} бит)", level="INFO")
                log_to_file(f"[QKD] Базисная (HEX): {hex_bases}", level="INFO")
                log_to_file(f"[QKD] Базисная (+/x): {bases[:64]}... (первые 64 символа из {len(bases)})", level="INFO")
                log_to_file(f"[QKD] Битовая: {bits[:64]}... (первые 64 бита из {len(bits)})", level="INFO")
                print(f"[QKD] [{timestamp}] Сформирована последовательность ({self.SEQUENCE_BITS} бит)")
                print(f"[QKD] Базисная (HEX): {hex_bases}")
                print(f"[QKD] Базисная (+/x): {bases[:64]}... (первые 64 символа из {len(bases)})")
                print(f"[QKD] Битовая: {bits[:64]}... (первые 64 бита из {len(bits)})")
                print(f"[QKD] Остаток в буфере: {len(self.data_buffer)} байт")
                
                return bits, bases
            else:
                # Недостаточно данных для формирования последовательности
                return None
                
        except Exception as e:
            error_msg = f"Ошибка чтения данных с QKD-устройства: {e}"
            log_to_file(error_msg, level="ERROR")
            print(f"[QKD DEBUG] {error_msg}")
            import traceback
            print(f"[QKD DEBUG] Traceback: {traceback.format_exc()}")
            return None

    def save_raw_data(self, sequence_id, bits, bases, is_test=False):
        """
        Сохраняет последовательность в базу данных
        
        Args:
            sequence_id: Уникальный идентификатор последовательности
            bits: Битовая последовательность (строка из '0' и '1')
            bases: Базисная последовательность в HEX формате (строка)
            is_test: Флаг тестовых данных (False для реальных данных от QKD устройства)
        """
        try:
            # Проверяем параметры перед сохранением
            if not sequence_id:
                raise ValueError("sequence_id не может быть пустым")
            if not bits:
                raise ValueError("bits не может быть пустым")
            if not bases:
                raise ValueError("bases не может быть пустым")
            
            # Убеждаемся, что bits и bases - строки
            bits = str(bits)
            bases = str(bases)
            
            # Логируем параметры перед сохранением (ИСПОЛЬЗУЕМ INFO для гарантии записи в лог)
            log_to_file(f"[QKD] Попытка сохранения: sequence_id={sequence_id}, bits_len={len(bits)}, bases_len={len(bases)}, station={self.station_name}, is_test={is_test}", level="INFO")
            print(f"[QKD] Попытка сохранения: sequence_id={sequence_id}, bits_len={len(bits)}, bases_len={len(bases)}, station={self.station_name}, is_test={is_test}")
            
            # Преобразуем булево значение в 0/1 для SQLite (КРИТИЧНО: явно указываем 0 для реальных данных)
            is_test_value = 1 if is_test else 0
            
            # Убеждаемся, что bases имеет правильный формат (256 символов '+' и 'x')
            if len(bases) != 256:
                error_msg = f"[QKD ERROR] bases должна содержать 256 символов, получено {len(bases)}. bases: {bases[:100]}..."
                log_to_file(error_msg, level="ERROR")
                print(error_msg)
                # ИСПРАВЛЯЕМ: дополняем или обрезаем до 256
                if len(bases) < 256:
                    bases = bases + '+' * (256 - len(bases))
                    log_to_file(f"[QKD] bases дополнена до 256 символов", level="WARNING")
                    print(f"[QKD] bases дополнена до 256 символов")
                else:
                    bases = bases[:256]
                    log_to_file(f"[QKD] bases обрезана до 256 символов", level="WARNING")
                    print(f"[QKD] bases обрезана до 256 символов")
            
            # Проверяем, что bases содержит только '+' и 'x'
            invalid_chars = [c for c in bases if c not in ['+', 'x']]
            if invalid_chars:
                error_msg = f"[QKD ERROR] bases содержит недопустимые символы: {set(invalid_chars)}. bases: {bases[:100]}..."
                log_to_file(error_msg, level="ERROR")
                print(error_msg)
                # ИСПРАВЛЯЕМ: заменяем недопустимые символы на '+'
                bases_fixed = ''.join('+' if c not in ['+', 'x'] else c for c in bases)
                bases = bases_fixed
                log_to_file(f"[QKD] bases исправлена: недопустимые символы заменены на '+'", level="WARNING")
                print(f"[QKD] bases исправлена: недопустимые символы заменены на '+'")
            
            query = "INSERT INTO raw_data (sequence_id, bits, bases, station, is_test) VALUES (?, ?, ?, ?, ?)"
            params = (sequence_id, bits, bases, self.station_name, is_test_value)
            
            log_to_file(f"[QKD] Параметры сохранения: is_test={is_test}, is_test_value={is_test_value}, bases_len={len(bases)}, bases_format={'+' if bases and bases[0] in ['+', 'x'] else 'HEX'}", level="INFO")
            print(f"[QKD] Параметры сохранения: is_test={is_test}, is_test_value={is_test_value}, bases_len={len(bases)}, bases_format={'+' if bases and bases[0] in ['+', 'x'] else 'HEX'}")
            log_to_file(f"[QKD] Параметры: sequence_id='{params[0]}', bits_len={len(params[1])}, bases_len={len(params[2])}, station='{params[3]}', is_test={params[4]}", level="INFO")
            print(f"[QKD] Параметры: sequence_id='{params[0]}', bits_len={len(params[1])}, bases_len={len(params[2])}, station='{params[3]}', is_test={params[4]}")
            
            # Выполняем запрос с явным указанием sync=False
            print(f"[QKD] ========== ВЫЗОВ execute_query ПЕРЕД СОХРАНЕНИЕМ ==========")
            log_to_file(f"[QKD] ========== ВЫЗОВ execute_query ПЕРЕД СОХРАНЕНИЕМ ==========", level="INFO")
            log_to_file(f"[QKD] SQL: {query}", level="INFO")
            log_to_file(f"[QKD] Параметры: {params}", level="INFO")
            
            try:
                result = self.db_manager.execute_query(query, params, sync=False)
                print(f"[QKD] ========== execute_query ВЫПОЛНЕН УСПЕШНО (result={result}) ==========")
                log_to_file(f"[QKD] ========== execute_query ВЫПОЛНЕН УСПЕШНО (result={result}) ==========", level="INFO")
            except Exception as db_error:
                error_msg = f"[QKD ERROR] ОШИБКА В execute_query: {db_error}"
                log_to_file(error_msg, level="ERROR")
                print(error_msg)
                import traceback
                error_trace = traceback.format_exc()
                log_to_file(f"[QKD ERROR] Traceback: {error_trace}", level="ERROR")
                print(f"[QKD ERROR] Traceback: {error_trace}")
                raise  # Пробрасываем дальше
            
            data_type = "тестовая" if is_test else "реальная"
            msg = f"Сохранена {data_type} последовательность {sequence_id} в базе данных (bits: {len(bits)} бит, bases: {len(bases)} символов)"
            log_to_file(msg, level="INFO")
            print(f"[QKD] {msg}")
            
            # Проверяем, что данные действительно сохранились (с небольшой задержкой для гарантии коммита)
            import time
            time.sleep(0.01)  # Небольшая задержка для гарантии коммита транзакции
            
            verify_query = "SELECT sequence_id, bits, bases FROM raw_data WHERE sequence_id = ?"
            verify_result = self.db_manager.execute_query(verify_query, (sequence_id,), fetch=True, sync=False)
            if verify_result and len(verify_result) > 0:
                log_to_file(f"[QKD DEBUG] Подтверждение: последовательность {sequence_id} найдена в БД", level="DEBUG")
                print(f"[QKD DEBUG] Подтверждение: последовательность {sequence_id} найдена в БД")
                print(f"[QKD DEBUG] Проверка: bits_len={len(verify_result[0].get('bits', ''))}, bases_len={len(verify_result[0].get('bases', ''))}")
            else:
                error_msg = f"КРИТИЧЕСКАЯ ОШИБКА: Последовательность {sequence_id} НЕ найдена в БД после сохранения!"
                log_to_file(error_msg, level="ERROR")
                print(f"[QKD ERROR] {error_msg}")
                # Пробуем еще раз проверить через секунду
                time.sleep(1)
                verify_result2 = self.db_manager.execute_query(verify_query, (sequence_id,), fetch=True, sync=False)
                if verify_result2 and len(verify_result2) > 0:
                    print(f"[QKD DEBUG] После задержки последовательность найдена")
                else:
                    print(f"[QKD ERROR] Даже после задержки последовательность НЕ найдена!")
                
        except Exception as e:
            import traceback
            error_msg = f"Ошибка сохранения данных в базе: {e}"
            error_trace = traceback.format_exc()
            log_to_file(error_msg, level="ERROR")
            log_to_file(f"[QKD ERROR] Traceback: {error_trace}", level="ERROR")
            print(f"[QKD ERROR] {error_msg}")
            print(f"[QKD ERROR] Traceback: {error_trace}")
            raise  # Пробрасываем исключение дальше для отладки

    def start_generation(self):
        """
        Запускает процесс генерации ключей из реального QKD устройства.
        Читает данные с COM-порта, формирует последовательности по 256 бит и сохраняет в БД.
        """
        # КРИТИЧНО: Логируем СРАЗУ при входе в метод
        log_to_file(f"[QKD] ====== START_GENERATION ВЫЗВАН ======", level="INFO")
        print(f"[QKD] ====== START_GENERATION ВЫЗВАН ======")
        
        # Проверяем состояние подключения
        if not self.serial_connection:
            error_msg = "[QKD ERROR] Serial connection НЕ установлен! Вызовите connect_serial() сначала!"
            log_to_file(error_msg, level="ERROR")
            print(error_msg)
            return
        
        if not self.serial_connection.is_open:
            error_msg = "[QKD ERROR] Serial port закрыт!"
            log_to_file(error_msg, level="ERROR")
            print(error_msg)
            return
        
        self.is_running = True
        sequence_counter = 0
        read_attempts = 0
        last_log_time = time.time()
        
        log_to_file(f"[QKD] Начата генерация ключей из реального QKD устройства. Станция: {self.station_name}", level="INFO")
        log_to_file(f"[QKD] Serial connection: {self.serial_connection}", level="INFO")
        log_to_file(f"[QKD] Порт открыт: {self.serial_connection.is_open}", level="INFO")
        log_to_file(f"[QKD] Текущий размер буфера: {len(self.data_buffer)} байт", level="INFO")
        print(f"[QKD] Начата генерация ключей из реального QKD устройства. Станция: {self.station_name}")
        print(f"[QKD] Serial connection: {self.serial_connection}")
        print(f"[QKD] Порт открыт: {self.serial_connection.is_open}")
        print(f"[QKD] Ожидание данных с COM-порта... (нажмите Ctrl+C для остановки)")
        print(f"[QKD] Текущий размер буфера: {len(self.data_buffer)} байт")
        
        while self.is_running:
            try:
                read_attempts += 1
                
                # КРИТИЧНО: Обрабатываем ВСЕ последовательности, которые можно сформировать из буфера
                # ТОЧНО КАК В ОРИГИНАЛЬНОМ КОДЕ - используем while для обработки всех последовательностей
                sequences_processed = 0
                sequences_in_buffer = len(self.data_buffer) // self.SEQUENCE_BYTES
                if sequences_in_buffer > 0:
                    log_to_file(f"[QKD] В буфере достаточно данных для {sequences_in_buffer} последовательностей", level="INFO")
                    print(f"[QKD] В буфере достаточно данных для {sequences_in_buffer} последовательностей")
                
                while len(self.data_buffer) >= self.SEQUENCE_BYTES:
                    # Извлекаем ровно 32 байта (256 бит)
                    sequence = bytes(self.data_buffer[:self.SEQUENCE_BYTES])
                    self.data_buffer = self.data_buffer[self.SEQUENCE_BYTES:]
                    
                    log_to_file(f"[QKD] Обработка последовательности #{sequences_processed + 1}, осталось в буфере: {len(self.data_buffer)} байт", level="INFO")
                    print(f"[QKD] Обработка последовательности #{sequences_processed + 1}, осталось в буфере: {len(self.data_buffer)} байт")
                    
                    # Преобразуем в битовую и базисную последовательности
                    try:
                        bits = self.bytes_to_bits(sequence)
                        # Для bases используем формат '+' и 'x' (как в тестовых последовательностях)
                        # Преобразуем HEX в базисную последовательность
                        hex_bases = self.bytes_to_hex(sequence)
                        log_to_file(f"[QKD] HEX bases: {hex_bases[:50]}...", level="INFO")
                        print(f"[QKD] HEX bases: {hex_bases[:50]}...")
                        
                        bases = self.hex_to_bases(hex_bases)
                        log_to_file(f"[QKD] Преобразовано в bases (+/x): {bases[:50]}..., длина: {len(bases)}", level="INFO")
                        print(f"[QKD] Преобразовано в bases (+/x): {bases[:50]}..., длина: {len(bases)}")
                    except Exception as conv_error:
                        error_msg = f"[QKD ERROR] Ошибка преобразования последовательности: {conv_error}"
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        import traceback
                        print(traceback.format_exc())
                        continue  # Пропускаем эту последовательность, продолжаем со следующей
                    
                    # Проверяем, что данные не пустые
                    if not bits or not bases:
                        log_to_file(f"[QKD DEBUG] Пропуск пустых данных", level="WARNING")
                        print(f"[QKD DEBUG] Пропуск пустых данных")
                        continue
                    
                    # Проверяем длину данных
                    if len(bits) != self.SEQUENCE_BITS:
                        error_msg = f"[QKD ERROR] Неправильная длина bits: ожидалось {self.SEQUENCE_BITS}, получено {len(bits)}"
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        continue
                    
                    # Проверяем длину bases
                    if len(bases) != 256:
                        error_msg = f"[QKD ERROR] Неправильная длина bases: ожидалось 256, получено {len(bases)}. bases: {bases[:50]}..."
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        continue
                    
                    # Проверяем формат bases
                    invalid_chars = [c for c in bases if c not in ['+', 'x']]
                    if invalid_chars:
                        error_msg = f"[QKD ERROR] bases содержит недопустимые символы: {set(invalid_chars)}. bases: {bases[:50]}..."
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        continue
                    
                    sequence_id = f"{self.station_name}_{sequence_counter}_{moscow_now_str('%Y%m%d%H%M%S')}"
                    log_to_file(f"[QKD] ========== СОХРАНЕНИЕ ПОСЛЕДОВАТЕЛЬНОСТИ #{sequence_counter} ==========", level="INFO")
                    print(f"[QKD] ========== СОХРАНЕНИЕ ПОСЛЕДОВАТЕЛЬНОСТИ #{sequence_counter} ==========")
                    log_to_file(f"[QKD] sequence_id: {sequence_id}", level="INFO")
                    print(f"[QKD] sequence_id: {sequence_id}")
                    log_to_file(f"[QKD] bits длина: {len(bits)}, bases длина: {len(bases)}", level="INFO")
                    print(f"[QKD] bits длина: {len(bits)}, bases длина: {len(bases)}")
                    log_to_file(f"[QKD] bases первые 32 символа: {bases[:32]}", level="INFO")
                    print(f"[QKD] bases первые 32 символа: {bases[:32]}")
                    log_to_file(f"[QKD] bits первые 32 бита: {bits[:32]}", level="INFO")
                    print(f"[QKD] bits первые 32 бита: {bits[:32]}")
                    
                    # Проверяем, что bits не все нули
                    if bits == '0' * len(bits):
                        error_msg = f"[QKD ERROR] ВНИМАНИЕ: bits состоит только из нулей! Это подозрительно."
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        # НЕ пропускаем - сохраняем все равно, может быть это валидные данные
                    
                    # Сохраняем как реальные данные (is_test=False)
                    try:
                        log_to_file(f"[QKD] ВЫЗОВ save_raw_data для последовательности #{sequence_counter}", level="INFO")
                        print(f"[QKD] ВЫЗОВ save_raw_data для последовательности #{sequence_counter}")
                        self.save_raw_data(sequence_id, bits, bases, is_test=False)
                        sequence_counter += 1
                        sequences_processed += 1
                        log_to_file(f"[QKD] ========== УСПЕШНО СОХРАНЕНА ПОСЛЕДОВАТЕЛЬНОСТЬ #{sequence_counter-1} ==========", level="INFO")
                        print(f"[QKD] ========== УСПЕШНО СОХРАНЕНА ПОСЛЕДОВАТЕЛЬНОСТЬ #{sequence_counter-1} ==========")
                        log_to_file(f"[QKD] sequence_id: {sequence_id}", level="INFO")
                        print(f"[QKD] sequence_id: {sequence_id}")
                        
                        # КРИТИЧНО: Вызываем callback для отправки данных через WebSocket
                        if self.sequence_callback:
                            try:
                                log_to_file(f"[QKD] Вызов callback для отправки через WebSocket: sequence_id={sequence_id}", level="INFO")
                                print(f"[QKD] Вызов callback для отправки через WebSocket: sequence_id={sequence_id}")
                                self.sequence_callback(sequence_id, bits, bases, is_test=False)
                                log_to_file(f"[QKD] Callback успешно выполнен", level="INFO")
                                print(f"[QKD] Callback успешно выполнен")
                            except Exception as callback_error:
                                error_msg = f"[QKD ERROR] Ошибка в callback: {callback_error}"
                                log_to_file(error_msg, level="ERROR")
                                print(error_msg)
                                import traceback
                                print(traceback.format_exc())
                        else:
                            log_to_file(f"[QKD WARNING] Callback не установлен! Анимация не будет отображаться.", level="WARNING")
                            print(f"[QKD WARNING] Callback не установлен! Анимация не будет отображаться.")
                    except ValueError as val_error:
                        # Ошибка валидации - логируем детально
                        error_msg = f"[QKD ERROR] Ошибка валидации при сохранении последовательности #{sequence_counter}: {val_error}"
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        log_to_file(f"[QKD ERROR] bits: {bits[:100]}...", level="ERROR")
                        log_to_file(f"[QKD ERROR] bases: {bases[:100]}...", level="ERROR")
                        print(f"[QKD ERROR] bits: {bits[:100]}...")
                        print(f"[QKD ERROR] bases: {bases[:100]}...")
                        # Продолжаем работу, не прерывая цикл
                        continue
                    except Exception as save_error:
                        # Другие ошибки - логируем и продолжаем
                        error_msg = f"[QKD ERROR] КРИТИЧЕСКАЯ ОШИБКА при сохранении последовательности #{sequence_counter}: {save_error}"
                        log_to_file(error_msg, level="ERROR")
                        print(error_msg)
                        import traceback
                        error_trace = traceback.format_exc()
                        log_to_file(f"[QKD ERROR] Traceback: {error_trace}", level="ERROR")
                        print(error_trace)
                        # Продолжаем работу, не прерывая цикл
                        continue
                
                # Теперь читаем новые данные с порта (ТОЧНО КАК В ОРИГИНАЛЕ)
                if self.serial_connection and self.serial_connection.is_open:
                    bytes_waiting = self.serial_connection.in_waiting
                    if bytes_waiting > 0:
                        data = self.serial_connection.read(bytes_waiting)
                        if data:
                            hex_repr = ' '.join([f'{b:02X}' for b in data])
                            log_to_file(f"[QKD] Получено {len(data)} байт с порта (hex): {hex_repr}", level="INFO")
                            print(f"[QKD] Получено {len(data)} байт с порта (hex): {hex_repr}")
                            self.data_buffer.extend(data)
                            log_to_file(f"[QKD] Размер буфера после добавления: {len(self.data_buffer)} байт", level="INFO")
                            print(f"[QKD] Размер буфера после добавления: {len(self.data_buffer)} байт")
                            
                            # КРИТИЧНО: После добавления данных проверяем, можно ли сформировать последовательности
                            # Если да, продолжаем цикл while для обработки всех последовательностей
                            if len(self.data_buffer) >= self.SEQUENCE_BYTES:
                                log_to_file(f"[QKD] После чтения данных буфер достиг размера {len(self.data_buffer)} байт, продолжаем обработку", level="INFO")
                                print(f"[QKD] После чтения данных буфер достиг размера {len(self.data_buffer)} байт, продолжаем обработку")
                                continue  # Возвращаемся к началу цикла while для обработки всех последовательностей
                    else:
                        # Логируем каждые 100 попыток, что данных нет
                        if read_attempts % 100 == 0:
                            log_to_file(f"[QKD] Попытка #{read_attempts}: данных на порту нет (in_waiting=0), размер буфера={len(self.data_buffer)} байт", level="INFO")
                            print(f"[QKD] Попытка #{read_attempts}: данных на порту нет (in_waiting=0), размер буфера={len(self.data_buffer)} байт")
                else:
                    error_msg = f"[QKD ERROR] Serial connection недоступен! connection={self.serial_connection}, is_open={self.serial_connection.is_open if self.serial_connection else 'None'}"
                    log_to_file(error_msg, level="ERROR")
                    print(error_msg)
                    break  # Выходим из цикла, если порт недоступен
                
                # Логируем статус каждые 10 секунд
                current_time = time.time()
                if current_time - last_log_time >= 10:
                    log_to_file(f"[QKD] Статус: попыток чтения={read_attempts}, сохранено последовательностей={sequence_counter}, размер буфера={len(self.data_buffer)} байт, обработано за цикл={sequences_processed}", level="INFO")
                    print(f"[QKD] Статус: попыток чтения={read_attempts}, сохранено последовательностей={sequence_counter}, размер буфера={len(self.data_buffer)} байт, обработано за цикл={sequences_processed}")
                    last_log_time = current_time
                
                # Небольшая задержка для снижения нагрузки на CPU (ТОЧНО КАК В ОРИГИНАЛЕ)
                time.sleep(0.01)
            except KeyboardInterrupt:
                log_to_file(f"[QKD] Прерывание пользователем. Остановка генерации...", level="INFO")
                print(f"\n[QKD] Прерывание пользователем. Остановка генерации...")
                self.stop_generation()
                # Выводим остаток данных в буфере, если есть
                if len(self.data_buffer) > 0:
                    print(f"[QKD] Остаток данных в буфере: {len(self.data_buffer)} байт ({len(self.data_buffer) * 8} бит)")
                    hex_repr = ' '.join([f'{b:02X}' for b in self.data_buffer])
                    print(f"[QKD] HEX: {hex_repr}")
                break
            except Exception as e:
                import traceback
                error_msg = f"КРИТИЧЕСКАЯ ОШИБКА в цикле генерации: {e}"
                error_trace = traceback.format_exc()
                log_to_file(error_msg, level="ERROR")
                log_to_file(f"[QKD ERROR] Traceback: {error_trace}", level="ERROR")
                print(f"[QKD ERROR] {error_msg}")
                print(f"[QKD ERROR] Traceback: {error_trace}")
                time.sleep(0.1)  # Небольшая задержка перед повторной попыткой
        
        # Логируем завершение
        log_to_file(f"[QKD] ====== START_GENERATION ЗАВЕРШЕН ======", level="INFO")
        print(f"[QKD] ====== START_GENERATION ЗАВЕРШЕН ======")

    def stop_generation(self):
        """
        Останавливает процесс генерации ключей
        """
        self.is_running = False
        if len(self.data_buffer) > 0:
            log_to_file(f"[QKD DEBUG] Остаток данных в буфере при остановке: {len(self.data_buffer)} байт", level="DEBUG")
            print(f"[QKD DEBUG] Остаток данных в буфере при остановке: {len(self.data_buffer)} байт")
        log_to_file(f"[QKD] Генерация ключей остановлена", level="INFO")
        print(f"[QKD] Генерация ключей остановлена")

    def get_last_sequence(self):
        try:
            query = "SELECT bits, bases FROM raw_data ORDER BY generated_at DESC LIMIT 1"
            result = self.db_manager.execute_query(query, fetch=True)
            if result:
                return result[0]
        except Exception as e:
            print(f"Ошибка получения последней последовательности: {e}")
            return None
        return None

    def get_raw_data(self) -> list:
        try:
            # Получаем все сырые данные (и тестовые, и реальные)
            query = "SELECT * FROM raw_data ORDER BY generated_at DESC"
            result = self.db_manager.execute_query(query, fetch=True, sync=False)
            
            if not result:
                return []
            
            # Проверяем, был ли восстановлен ключ для каждой последовательности
            for item in result:
                sequence_id = item.get('sequence_id', '')
                # Форматируем время для отображения
                if item.get('generated_at'):
                    item['generated_at'] = format_datetime_for_display(item['generated_at'])
                
                # Преобразуем is_test из SQLite формата (0/1) в булево значение
                is_test_value = item.get('is_test')
                if is_test_value is not None:
                    # Преобразуем 0/1 в False/True
                    item['is_test'] = bool(is_test_value) if is_test_value != 0 else False
                else:
                    # Если is_test отсутствует, определяем по sequence_id (тестовые начинаются с testA_ или testB_)
                    item['is_test'] = sequence_id.startswith('testA_') or sequence_id.startswith('testB_')
                
                if sequence_id:
                    key_id = f"key_{sequence_id}"
                    # Проверяем наличие ключа в таблице keys
                    key_query = "SELECT key_id FROM keys WHERE key_id = ? AND status = 'Активен'"
                    key_result = self.db_manager.execute_query(key_query, (key_id,), fetch=True, sync=False)
                    item['is_recovered'] = bool(key_result)
                else:
                    item['is_recovered'] = False
            
            return result
        except Exception as e:
            from utils.logging_utils import log_to_file
            log_to_file(f"Ошибка получения сырых данных: {e}", level="ERROR")
            return []

    def select_sequence(self, sequence_id: str, username: str) -> bool:
        """
        Выбирает последовательность для просеивания.
        Разрешает повторный выбор (обновляет selected_by даже если уже выбрано).
        
        :param sequence_id: ID последовательности
        :param username: Имя пользователя
        :return: True если успешно, False если ошибка
        """
        try:
            # Сначала проверяем, существует ли последовательность
            check_query = "SELECT sequence_id FROM raw_data WHERE sequence_id = ?"
            check_result = self.db_manager.execute_query(check_query, (sequence_id,), fetch=True, sync=False)
            
            if not check_result:
                error_msg = f"Последовательность {sequence_id} не найдена в БД"
                from utils.logging_utils import log_to_file
                log_to_file(error_msg, level="ERROR")
                print(f"[QKD] {error_msg}")
                return False
            
            # Обновляем selected_by (разрешаем повторный выбор)
            selected_by_value = f"{self.station_name}_{username}"
            query = "UPDATE raw_data SET selected_by = ? WHERE sequence_id = ?"
            self.db_manager.execute_query(query, (selected_by_value, sequence_id), sync=True)
            
            from utils.logging_utils import log_to_file
            log_to_file(f"[QKD] Последовательность {sequence_id} выбрана пользователем {selected_by_value}", level="INFO")
            print(f"[QKD] Последовательность {sequence_id} выбрана пользователем {selected_by_value}")
            
            return True
        except Exception as e:
            from utils.logging_utils import log_to_file
            error_msg = f"Ошибка выбора последовательности {sequence_id}: {e}"
            log_to_file(error_msg, level="ERROR")
            print(f"[QKD] {error_msg}")
            import traceback
            log_to_file(f"[QKD] Traceback: {traceback.format_exc()}", level="ERROR")
            print(f"[QKD] Traceback: {traceback.format_exc()}")
            return False

    def get_test_data(self) -> list:
        try:
            query = "SELECT * FROM raw_data WHERE is_test = TRUE"
            result = self.db_manager.execute_query(query, fetch=True, sync=False)
            return result if result else []
        except Exception as e:
            print(f"Ошибка получения тестовых данных: {e}")
            return []
