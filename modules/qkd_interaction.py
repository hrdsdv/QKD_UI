import serial
import time
import threading
import socket
from datetime import datetime
from modules.database_utils import DatabaseManager
from utils.logging_utils import log_to_file, log_to_db

class QKDModule:
    def __init__(self, db_path):
        self.db_manager = DatabaseManager(db_path)
        self.serial_connection = None
        self.tcp_connection = None
        self.is_running = False
        self.station_name = "A" if "server_user_1" in db_path else "B"
        self.other_station_name = "B" if self.station_name == "A" else "A"
        self.other_station_ip = "127.0.0.1"  # Замените на реальный IP второго сервера
        self.other_station_port = 5001 if self.station_name == "A" else 5000

    def connect_serial(self, port, baudrate=9600):
        try:
            self.serial_connection = serial.Serial(port, baudrate, timeout=1)
            log_to_file(f"Подключено к QKD-устройству через {port}")
            return True
        except serial.SerialException as e:
            msg = f"Ошибка подключения к QKD-устройству: {e}"
            log_to_file(msg, level="ERROR")
            log_to_db(self.db_manager.db_path, None, 'QKDModule', 'ERROR', msg)
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

    def read_qkd_data(self):
        if not self.serial_connection:
            return None

        try:
            line = self.serial_connection.readline().decode('utf-8').strip()
            if line:
                bits, bases = line.split('|')
                return bits, bases
        except Exception as e:
            print(f"Ошибка чтения данных с QKD-устройства: {e}")
            return None

    def save_raw_data(self, sequence_id, bits, bases):
        try:
            query = "INSERT INTO raw_data (sequence_id, bits, bases, station) VALUES (?, ?, ?, ?)"
            self.db_manager.execute_query(query, (sequence_id, bits, bases, self.station_name))
            print(f"Сохранена последовательность {sequence_id} в базе данных")
        except Exception as e:
            print(f"Ошибка сохранения данных в базе: {e}")

    def start_generation(self):
        self.is_running = True
        sequence_counter = 0
        while self.is_running:
            data = self.read_qkd_data()
            if data:
                bits, bases = data
                sequence_id = f"{self.station_name}_{sequence_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                self.save_raw_data(sequence_id, bits, bases)
                sequence_counter += 1
            time.sleep(1)

    def stop_generation(self):
        self.is_running = False

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
        try:
            query = "UPDATE raw_data SET selected_by = ? WHERE sequence_id = ?"
            self.db_manager.execute_query(query, (f"{self.station_name}_{username}", sequence_id), sync=True)
            return True
        except Exception as e:
            print(f"Ошибка выбора последовательности: {e}")
            return False

    def get_test_data(self) -> list:
        try:
            query = "SELECT * FROM raw_data WHERE is_test = TRUE"
            result = self.db_manager.execute_query(query, fetch=True, sync=False)
            return result if result else []
        except Exception as e:
            print(f"Ошибка получения тестовых данных: {e}")
            return []
