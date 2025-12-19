import os
import sys

# Добавляем корневую директорию проекта в путь для импорта модулей
_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from modules.qkd_interaction import QKDModule
from modules.key_postprocessing import KeyPostprocessingModule
from modules.key_recovery import KeyRecoveryModule, encode_reed_solomon, decode_reed_solomon
from modules.key_management import KeyManagementModule
from modules.database_utils import DatabaseManager
from modules.gost_cipher import GOSTCipher, GOSTHash, AESCipher
from modules.message_crypto import MessageCrypto
from utils.logging_utils import log_to_file

# Импорт конфигурации (noinspection PyUnresolvedReferences)
try:
    from config import (
        SERVER_A_HOST, SERVER_A_PORT, REMOTE_SERVER_B,
        FLASK_SECRET_KEY
    )
except ImportError:
    # Fallback значения если config.py не найден
    SERVER_A_HOST = '0.0.0.0'
    SERVER_A_PORT = 5000
    # ВАЖНО: Замените localhost на реальный IP-адрес ПК2 для работы между разными ПК!
    REMOTE_SERVER_B = 'http://172.16.111.53:5001'  # IP ПК2 (server_user_2)
    FLASK_SECRET_KEY = 'dev-secret-key'

import threading
import random
import requests
from datetime import datetime
from utils.timezone_utils import moscow_now, moscow_now_str, moscow_datetime_sql
import time

# Импорт для WebSocket клиента
try:
    import socketio as sio_client
except ImportError:
    sio_client = None
    log_to_file("socketio не установлен, WebSocket клиент недоступен", level="WARNING")


app = Flask(__name__)
CORS(app)  # Разрешаем кросс-доменные запросы для работы между разными ПК
app.secret_key = FLASK_SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'databases', 'user_1_db.db'))
print(f"Путь к базе данных: {db_path}")
# Инициализация базы данных
db_path = os.path.abspath(os.path.join('databases', 'user_1_db.db'))
print(f"Путь к базе данных: {db_path}")
db_manager = DatabaseManager(db_path)

# Создаем таблицу settings, если её нет
try:
    db_manager.execute_query('''
        CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''', sync=False)
    # Добавляем начальное значение, если его нет
    result = db_manager.execute_query(
        "SELECT setting_value FROM settings WHERE setting_key = ?",
        ('remote_server_url',), fetch=True, sync=False
    )
    if not result:
        db_manager.execute_query(
            "INSERT INTO settings (setting_key, setting_value) VALUES (?, ?)",
            ('remote_server_url', REMOTE_SERVER_B), sync=False
        )
except Exception as e:
    log_to_file(f"Ошибка создания таблицы settings: {e}", level="WARNING")

# Инициализация модулей
qkd_module = QKDModule(db_path)

# Глобальная переменная для хранения callback функции отправки через WebSocket
qkd_sequence_callback = None

def set_qkd_sequence_callback(callback):
    """Устанавливает callback функцию для отправки данных последовательности через WebSocket"""
    global qkd_sequence_callback
    qkd_sequence_callback = callback
key_recovery_module = KeyRecoveryModule(db_path)
key_management_module = KeyManagementModule(db_path)
message_crypto = MessageCrypto(db_path)

# Функция для получения IP удаленного сервера из БД
def get_remote_server_url():
    """Получает IP-адрес удаленного сервера из БД или использует значение из config.py."""
    try:
        result = db_manager.execute_query(
            "SELECT setting_value FROM settings WHERE setting_key = ?",
            ('remote_server_url',), fetch=True, sync=False
        )
        if result and result[0]['setting_value']:
            return result[0]['setting_value']
    except Exception as e:
        log_to_file(f"Ошибка получения IP из БД, используем config.py: {e}", level="WARNING")
    
    # Fallback на config.py
    return REMOTE_SERVER_B

# Инициализируем key_postprocessing_module с динамическим URL
key_postprocessing_module = KeyPostprocessingModule(db_path, remote_server_url=get_remote_server_url())

def start_qkd_automatically():
    """
    Автоматически запускает чтение данных с QKD устройства через COM3 при старте сервера.
    Данные автоматически вносятся в таблицу raw_data.
    """
    log_to_file("[AUTO-START] ========== АВТОМАТИЧЕСКИЙ ЗАПУСК QKD ==========", level="INFO")
    print("[AUTO-START] ========== АВТОМАТИЧЕСКИЙ ЗАПУСК QKD ==========")
    log_to_file("[AUTO-START] Попытка автоматического подключения к QKD устройству (COM3, 9600)...", level="INFO")
    print("[AUTO-START] Попытка автоматического подключения к QKD устройству (COM3, 9600)...")
    
    try:
        # ТЕСТ: Проверяем, что можем сохранить тестовую запись в БД (только если её еще нет)
        try:
            # Проверяем, существует ли уже тестовая запись
            check_query = "SELECT sequence_id FROM raw_data WHERE sequence_id = ?"
            existing = db_manager.execute_query(check_query, ("TEST_AUTO_START",), fetch=True, sync=False)
            
            if not existing:
                # Преобразуем HEX в bases формат
                hex_bases_test = "00 " * 32  # 32 байта в HEX
                bases_test = qkd_module.hex_to_bases(hex_bases_test)
                
                test_query = "INSERT INTO raw_data (sequence_id, bits, bases, station, is_test) VALUES (?, ?, ?, ?, ?)"
                test_params = ("TEST_AUTO_START", "0" * 256, bases_test, "A", 0)
                db_manager.execute_query(test_query, test_params, sync=False)
                log_to_file("[AUTO-START] ТЕСТ: Тестовая запись успешно сохранена в БД", level="INFO")
                print("[AUTO-START] ТЕСТ: Тестовая запись успешно сохранена в БД")
            else:
                log_to_file("[AUTO-START] ТЕСТ: Тестовая запись уже существует, пропускаем", level="INFO")
                print("[AUTO-START] ТЕСТ: Тестовая запись уже существует, пропускаем")
        except Exception as test_error:
            log_to_file(f"[AUTO-START] ТЕСТ ОШИБКА: Не удалось сохранить тестовую запись: {test_error}", level="ERROR")
            print(f"[AUTO-START] ТЕСТ ОШИБКА: Не удалось сохранить тестовую запись: {test_error}")
            import traceback
            print(traceback.format_exc())
        
        # Подключаемся к COM3 с повторными попытками
        log_to_file("[AUTO-START] Вызов connect_serial('COM3', 9600) с повторными попытками...", level="INFO")
        print("[AUTO-START] Вызов connect_serial('COM3', 9600) с повторными попытками...")
        print("[AUTO-START] =========================================")
        print("[AUTO-START] ВАЖНО: Если порт COM3 занят другой программой:")
        print("[AUTO-START] 1. Закройте программу, использующую COM3")
        print("[AUTO-START] 2. Подождите 3-5 секунд")
        print("[AUTO-START] 3. Система автоматически повторит попытку подключения")
        print("[AUTO-START] =========================================")
        
        if qkd_module.connect_serial('COM3', baudrate=9600, retry_count=5, retry_delay=3):
            log_to_file("[AUTO-START] COM3 подключен успешно, запуск генерации...", level="INFO")
            print("[AUTO-START] COM3 подключен успешно, запуск генерации...")
            
            # Небольшая задержка перед запуском потока
            import time
            time.sleep(0.5)
            
            # Устанавливаем callback для отправки данных через WebSocket
            def send_sequence_to_client(sequence_id, bits, bases, is_test):
                """Callback функция для отправки данных последовательности через WebSocket"""
                try:
                    last_32_bits = bits[-32:] if len(bits) >= 32 else bits
                    last_32_bases = bases[-32:] if len(bases) >= 32 else bases
                    
                    # Используем start_background_task для отправки из фонового потока
                    def emit_sequence():
                        try:
                            socketio.emit('new_sequence', {
                                'sequence_id': sequence_id,
                                'last_32_bits': last_32_bits,
                                'last_32_bases': last_32_bases,
                                'is_test': is_test,
                                'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
                            }, namespace='/', broadcast=True)
                            log_to_file(f"[WebSocket] Отправлена последовательность {sequence_id} через WebSocket", level="INFO")
                        except Exception as e:
                            log_to_file(f"[WebSocket ERROR] Ошибка отправки через WebSocket: {e}", level="ERROR")
                    
                    # Запускаем в фоновом потоке с правильным контекстом
                    socketio.start_background_task(emit_sequence)
                except Exception as e:
                    log_to_file(f"[WebSocket ERROR] Ошибка создания задачи для WebSocket: {e}", level="ERROR")
            
            # Устанавливаем callback в QKD модуль
            qkd_module.set_sequence_callback(send_sequence_to_client)
            
            # Запускаем генерацию в отдельном потоке
            log_to_file("[AUTO-START] Создание потока для start_generation...", level="INFO")
            print("[AUTO-START] Создание потока для start_generation...")
            
            thread = threading.Thread(target=qkd_module.start_generation, daemon=True, name="QKD-Auto-Generation")
            thread.start()
            
            # Проверяем, что поток запустился
            time.sleep(0.1)
            log_to_file(f"[AUTO-START] Поток генерации запущен: {thread.name}, alive={thread.is_alive()}, ident={thread.ident}", level="INFO")
            print(f"[AUTO-START] Поток генерации запущен: {thread.name}, alive={thread.is_alive()}, ident={thread.ident}")
            
            if not thread.is_alive():
                log_to_file("[AUTO-START] ВНИМАНИЕ: Поток не запустился или уже завершился!", level="ERROR")
                print("[AUTO-START] ВНИМАНИЕ: Поток не запустился или уже завершился!")
        else:
            log_to_file("[AUTO-START] ОШИБКА: Не удалось подключиться к COM3. Проверьте подключение устройства.", level="ERROR")
            print("[AUTO-START] ОШИБКА: Не удалось подключиться к COM3. Проверьте подключение устройства.")
    except Exception as e:
        error_msg = f"[AUTO-START] КРИТИЧЕСКАЯ ОШИБКА при автоматическом запуске: {e}"
        log_to_file(error_msg, level="ERROR")
        print(error_msg)
        import traceback
        error_trace = traceback.format_exc()
        log_to_file(f"[AUTO-START] Traceback: {error_trace}", level="ERROR")
        print(error_trace)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        try:
            user = db_manager.get_user_by_username(username)
            if user and user['password'] == password:
                session['user_id'] = user['user_id']
                session['username'] = user['username']
                session['role'] = user['role']
                flash('Вы успешно вошли в систему!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Неверное имя пользователя или пароль', 'danger')
        except Exception as e:
            print(f"Ошибка при входе: {e}")
            flash(f'Ошибка при входе: {e}', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            first_name = request.form['first_name']
            last_name = request.form['last_name']
            student_id = request.form['student_id']
            username = request.form['username']
            password = request.form['password']
            confirm_password = request.form['confirm_password']
            if password != confirm_password:
                flash('Пароли не совпадают!', 'danger')
                return redirect(url_for('register'))
            if len(password) < 8:
                flash('Пароль должен содержать не менее 8 символов!', 'danger')
                return redirect(url_for('register'))
            if len(student_id) != 9 or not student_id.isdigit():
                flash('Номер зачетной книги должен содержать ровно 9 цифр!', 'danger')
                return redirect(url_for('register'))
            user = db_manager.get_user_by_username(username)
            if user:
                flash('Имя пользователя уже занято!', 'danger')
                return redirect(url_for('register'))
            db_manager.register_user(first_name, last_name, student_id, username, password)
            flash('Регистрация прошла успешно! Теперь вы можете войти.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            print(f"Ошибка при регистрации: {e}")
            flash(f'Ошибка при регистрации: {e}', 'danger')
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        available_keys = key_recovery_module.get_available_keys()
        system_logs = key_management_module.get_system_logs()
        raw_data = qkd_module.get_raw_data()
        test_data = qkd_module.get_test_data()  # Получаем тестовые данные
        qber_value = None

        incoming_items = key_management_module.get_incoming_messages(session['user_id'])

        # Получаем последние 32 бита и их базисы
        last_sequence = qkd_module.get_last_sequence()
        last_32_bits = last_sequence['bits'][-32:] if last_sequence and len(last_sequence.get('bits', '')) >= 32 else (last_sequence['bits'] if last_sequence else None)
        last_32_bases = last_sequence['bases'][-32:] if last_sequence and len(last_sequence.get('bases', '')) >= 32 else (last_sequence['bases'] if last_sequence else None)

        # Получаем тестовые сообщения из БД для текущего пользователя
        test_messages = []
        try:
            current_user = session.get('username')  # Реальное имя пользователя из сессии
            if not current_user:
                current_user = None
            db_manager_test = DatabaseManager(db_path)
            query = '''
                SELECT message_id, sender, receiver, message_text, created_at
                FROM test_messages
                WHERE receiver = ? AND direction = 'received'
                ORDER BY created_at DESC
                LIMIT 50
            '''
            results = db_manager_test.execute_query(query, (current_user,), fetch=True, sync=False) if current_user else []
            if results:
                for row in results:
                    test_messages.append({
                        'id': row['message_id'],
                        'sender': row['sender'],
                        'receiver': row['receiver'],
                        'message': row['message_text'],
                        'received_at': row['created_at']
                    })
        except Exception as e:
            log_to_file(f"Ошибка получения тестовых сообщений для шаблона: {e}", level="ERROR")
            test_messages = []

        return render_template(
            'index.html',
            available_keys=available_keys,
            system_logs=system_logs,
            qber_value=qber_value,
            incoming_items=incoming_items,
            raw_data=raw_data,
            test_data=test_data,  # Передаем тестовые данные в шаблон
            last_32_bits=last_32_bits,
            last_32_bases=last_32_bases,
            remote_server_b=get_remote_server_url(),  # Адрес удалённого сервера Б
            test_messages=test_messages  # Тестовые сообщения из БД
        )
    except Exception as e:
        print(f"Ошибка при загрузке главной страницы: {e}")
        flash(f'Ошибка при загрузке главной страницы: {e}', 'danger')
        return redirect(url_for('login'))



@app.route('/start_qkd')
def start_qkd():
    from utils.logging_utils import log_to_file
    log_to_file("[APP] /start_qkd вызван", level="INFO")
    print("[APP] /start_qkd вызван")
    
    # Для Windows используйте 'COM3', для Linux '/dev/ttyUSB3' или '/dev/ttyUSB0'
    log_to_file("[APP] Попытка подключения к COM3...", level="INFO")
    print("[APP] Попытка подключения к COM3...")
    
    if not qkd_module.connect_serial('COM3', baudrate=9600):  # COM3 для Windows, 9600 бод
        error_msg = 'Не удалось подключиться к QKD-устройству'
        log_to_file(f"[APP] ОШИБКА: {error_msg}", level="ERROR")
        return jsonify({'status': 'error', 'message': error_msg}), 500
    
    log_to_file("[APP] COM3 подключен успешно", level="INFO")
    print("[APP] COM3 подключен успешно")
    
    # TCP подключение можно пропустить для тестирования
    # if not qkd_module.connect_tcp():
    #     return jsonify({'status': 'error', 'message': 'Не удалось установить TCP-канал'}), 500
    # if not qkd_module.synchronize_stations():
    #     return jsonify({'status': 'error', 'message': 'Не удалось синхронизировать станции'}), 500
    
    log_to_file("[APP] Запуск потока start_generation...", level="INFO")
    print("[APP] Запуск потока start_generation...")
    
    # Устанавливаем callback для отправки данных через WebSocket
    def send_sequence_to_client(sequence_id, bits, bases, is_test):
        """Callback функция для отправки данных последовательности через WebSocket"""
        try:
            last_32_bits = bits[-32:] if len(bits) >= 32 else bits
            last_32_bases = bases[-32:] if len(bases) >= 32 else bases
            
            # Используем start_background_task для отправки из фонового потока
            def emit_sequence():
                try:
                    socketio.emit('new_sequence', {
                        'sequence_id': sequence_id,
                        'last_32_bits': last_32_bits,
                        'last_32_bases': last_32_bases,
                        'is_test': is_test,
                        'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
                    }, namespace='/', broadcast=True)
                    log_to_file(f"[WebSocket] Отправлена последовательность {sequence_id} через WebSocket", level="INFO")
                except Exception as e:
                    log_to_file(f"[WebSocket ERROR] Ошибка отправки через WebSocket: {e}", level="ERROR")
            
            # Запускаем в фоновом потоке с правильным контекстом
            socketio.start_background_task(emit_sequence)
        except Exception as e:
            log_to_file(f"[WebSocket ERROR] Ошибка создания задачи для WebSocket: {e}", level="ERROR")
    
    # Устанавливаем callback в QKD модуль
    qkd_module.set_sequence_callback(send_sequence_to_client)
    
    thread = threading.Thread(target=qkd_module.start_generation, daemon=True)
    thread.start()
    
    log_to_file(f"[APP] Поток запущен: {thread.name}, alive={thread.is_alive()}", level="INFO")
    print(f"[APP] Поток запущен: {thread.name}, alive={thread.is_alive()}")
    
    return jsonify({'status': 'success', 'message': 'Генерация ключей начата'})

@app.route('/stop_qkd')
def stop_qkd():
    qkd_module.stop_generation()
    return jsonify({'status': 'success', 'message': 'Генерация ключей остановлена'})

@app.route('/select_sequence', methods=['POST'])
def select_sequence():
    sequence_id = request.form.get('sequence_id')
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID последовательности'}), 400

    username = session.get('username', 'Unknown')
    
    # Логируем попытку выбора
    log_to_file(f"[SELECT] Попытка выбора последовательности {sequence_id} пользователем {username}", level="INFO")
    
    if qkd_module.select_sequence(sequence_id, username):
        # Уведомляем второй сервер о выборе последовательности
        import requests
        try:
            requests.post(f'{get_remote_server_url()}/api/sync_sequence_selection', json={
                'sequence_id': sequence_id,
                'selected_by': f'A_{username}',
                'station': 'A'
            }, timeout=2)
        except Exception as e:
            log_to_file(f"Не удалось синхронизировать выбор последовательности: {e}", level="WARNING")
        
        # Используем новый модуль постобработки с реальным сравнением базисов
        try:
            comparison_result = key_postprocessing_module.compare_bases(sequence_id)
            
            if 'error' in comparison_result:
                error_msg = comparison_result['error']
                log_to_file(f"[SELECT] Ошибка compare_bases для {sequence_id}: {error_msg}", level="ERROR")
                return jsonify({'status': 'error', 'message': error_msg}), 400
        except Exception as e:
            error_msg = f'Ошибка при сравнении базисов: {str(e)}'
            log_to_file(f"[SELECT] Исключение в compare_bases для {sequence_id}: {e}", level="ERROR")
            import traceback
            log_to_file(f"[SELECT] Traceback: {traceback.format_exc()}", level="ERROR")
            return jsonify({'status': 'error', 'message': error_msg}), 500
        
        mismatches = comparison_result.get('mismatches', 0)
        qber_value = comparison_result.get('qber', 0)
        needs_regeneration = comparison_result.get('needs_regeneration', False)
        
        # Сохраняем просеянный ключ в БД для последующего использования при восстановлении
        sifted_key_local = comparison_result.get('sifted_key_local', '')
        if sifted_key_local:
            key_postprocessing_module.save_sifted_key(sequence_id, sifted_key_local)
        
        # Получаем локальные данные для базисов
        local_data = key_postprocessing_module.get_local_sequence(sequence_id)
        last_32_bases_local = local_data['bases'][-32:] if local_data and len(local_data.get('bases', '')) >= 32 else (local_data['bases'] if local_data else '')
        
        # Получаем удаленные базисы для сравнения
        remote_data = key_postprocessing_module.get_remote_sequence(sequence_id)
        if not remote_data:
            # Для тестовых последовательностей получаем из БД
            if sequence_id.startswith('testA_'):
                pair_id = sequence_id.replace('testA_', 'testB_')
            elif sequence_id.startswith('testB_'):
                pair_id = sequence_id.replace('testB_', 'testA_')
            else:
                pair_id = sequence_id
            query = "SELECT bases FROM raw_data WHERE sequence_id = ?"
            result = db_manager.execute_query(query, (pair_id,), fetch=True, sync=False)
            if result:
                remote_bases = result[0]['bases']
            else:
                remote_bases = local_data['bases'] if local_data else ''  # Fallback
        else:
            remote_bases = remote_data['bases']
        
        last_32_bases_remote = remote_bases[-32:] if len(remote_bases) >= 32 else remote_bases
        
        # Получаем is_test из базы данных
        is_test_query = "SELECT is_test FROM raw_data WHERE sequence_id = ?"
        is_test_result = db_manager.execute_query(is_test_query, (sequence_id,), fetch=True, sync=False)
        is_test = False
        if is_test_result:
            is_test = bool(is_test_result[0].get('is_test', 0))
        else:
            # Определяем по имени последовательности
            is_test = sequence_id.startswith('testA_') or sequence_id.startswith('testB_')
        
        # Проверяем порог QBER (11%)
        if needs_regeneration:
            return jsonify({
                'status': 'warning',
                'message': f'QBER ({qber_value}%) превышает порог 11%. Рекомендуется повторная генерация.',
                'mismatches': mismatches,
                'qber': qber_value,
                'needs_regeneration': True,
                'is_test': is_test,
                'last_32_bits_local': comparison_result.get('last_32_bits_local', ''),
                'last_32_bits_remote': comparison_result.get('last_32_bits_remote', ''),
                'last_32_bases_local': last_32_bases_local,
                'last_32_bases_remote': last_32_bases_remote
            })
        
        # Отправляем данные через WebSocket для синхронизации с сервером 2
        try:
            socketio.emit('sifting_complete', {
                'sequence_id': sequence_id,
                'station': 'A',
                'last_32_bits': comparison_result.get('last_32_bits_local', ''),
                'last_32_bits_local': comparison_result.get('last_32_bits_local', ''),
                'last_32_bases': last_32_bases_local,
                'last_32_bases_local': last_32_bases_local,
                'last_32_bits_remote': comparison_result.get('last_32_bits_remote', ''),
                'last_32_bases_remote': last_32_bases_remote,
                'mismatches': mismatches,
                'qber': qber_value,
                'is_test': is_test,
                'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
            }, namespace='/')
            log_to_file(f"Отправлены данные просеивания через WebSocket для {sequence_id}, mismatches={mismatches}, qber={qber_value}%", level="INFO")
        except Exception as e:
            log_to_file(f"Ошибка отправки данных просеивания через WebSocket: {e}", level="ERROR")
            # Логируем в журнал
            try:
                from utils.logging_utils import log_to_db
                log_to_db(db_path, None, 'QKDModule', 'ERROR', f"Ошибка WebSocket синхронизации: {e}")
            except:
                pass
        
        return jsonify({
            'status': 'success', 
            'mismatches': mismatches, 
            'qber': qber_value,
            'is_test': is_test,
            'sifted_length': comparison_result.get('sifted_length', 0),
            'matching_bases': comparison_result.get('matching_bases', 0),
            'last_32_bits_local': comparison_result.get('last_32_bits_local', ''),
            'last_32_bits_remote': comparison_result.get('last_32_bits_remote', ''),
            'last_32_bases_local': last_32_bases_local,
            'last_32_bases_remote': last_32_bases_remote
        })
    else:
        error_msg = f'Не удалось выбрать последовательность {sequence_id}. Проверьте логи сервера.'
        log_to_file(f"[SELECT] Ошибка выбора последовательности {sequence_id}: select_sequence вернул False", level="ERROR")
        return jsonify({'status': 'error', 'message': error_msg}), 500

@app.route('/api/sync_sequence_selection', methods=['POST'])
def sync_sequence_selection():
    """API endpoint для синхронизации выбора последовательности от другого абонента"""
    try:
        data = request.get_json()
        sequence_id = data.get('sequence_id')
        selected_by = data.get('selected_by')
        
        # Обновляем информацию о выборе в своей БД
        query = "UPDATE raw_data SET selected_by = ? WHERE sequence_id = ?"
        db_manager.execute_query(query, (selected_by, sequence_id), sync=False)
        
        return jsonify({'status': 'success', 'message': 'Выбор синхронизирован'})
    except Exception as e:
        log_to_file(f"Ошибка синхронизации выбора последовательности: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/get_sequence_data/<sequence_id>', methods=['GET'])
def get_sequence_data(sequence_id):
    """API endpoint для получения данных последовательности (для сверки базисов между абонентами)"""
    try:
        result = db_manager.execute_query(
            "SELECT bits, bases FROM raw_data WHERE sequence_id = ?",
            (sequence_id,), fetch=True, sync=False
        )
        if result:
            return jsonify({
                'status': 'success',
                'bits': result[0]['bits'],
                'bases': result[0]['bases']
            })
        else:
            return jsonify({'status': 'error', 'message': 'Последовательность не найдена'}), 404
    except Exception as e:
        log_to_file(f"Ошибка получения данных последовательности: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/get_raw_data', methods=['GET'])
def get_raw_data_api():
    """API endpoint для получения актуальных данных raw_data"""
    try:
        raw_data = qkd_module.get_raw_data()
        return jsonify({'status': 'success', 'raw_data': raw_data})
    except Exception as e:
        log_to_file(f"Ошибка получения raw_data: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/get_new_real_sequences', methods=['GET'])
def get_new_real_sequences():
    """API endpoint для получения новых реальных последовательностей из БД (для анимации)"""
    try:
        # Всегда получаем последние 20 последовательностей (для надежности)
        # Фильтрация по новым будет на клиенте по sequence_id
        query = """
            SELECT sequence_id, bits, bases, generated_at 
            FROM raw_data 
            WHERE is_test = 0 
            ORDER BY generated_at DESC 
            LIMIT 20
        """
        result = db_manager.execute_query(query, fetch=True, sync=False)
        
        log_to_file(f"[API] Получено последовательностей из БД: {len(result) if result else 0}", level="INFO")
        
        if not result:
            return jsonify({'status': 'success', 'sequences': []})
        
        # Форматируем данные для отправки
        sequences = []
        for item in result:
            bits = item.get('bits', '')
            bases = item.get('bases', '')
            
            # Проверяем, что данные не пустые
            if not bits or not bases:
                log_to_file(f"[API WARNING] Пропущена последовательность {item.get('sequence_id')} - пустые данные", level="WARNING")
                continue
            
            # Проверяем длину данных
            if len(bits) < 32 or len(bases) < 32:
                log_to_file(f"[API WARNING] Последовательность {item.get('sequence_id')} слишком короткая: bits={len(bits)}, bases={len(bases)}", level="WARNING")
                # Пропускаем, но логируем
            
            last_32_bits = bits[-32:] if len(bits) >= 32 else bits
            last_32_bases = bases[-32:] if len(bases) >= 32 else bases
            
            sequences.append({
                'sequence_id': item.get('sequence_id'),
                'last_32_bits': last_32_bits,
                'last_32_bases': last_32_bases,
                'is_test': False,
                'generated_at': item.get('generated_at'),
                'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
            })
        
        log_to_file(f"[API] Отправлено последовательностей: {len(sequences)}", level="INFO")
        if sequences:
            log_to_file(f"[API] Первая последовательность: {sequences[0].get('sequence_id')}, bits_len={len(sequences[0].get('last_32_bits', ''))}, bases_len={len(sequences[0].get('last_32_bases', ''))}", level="INFO")
        return jsonify({'status': 'success', 'sequences': sequences})
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        log_to_file(f"[API ERROR] Ошибка получения новых последовательностей: {e}", level="ERROR")
        log_to_file(f"[API ERROR] Traceback: {error_trace}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/recover_key', methods=['POST'])
def recover_key():
    """
    Восстановление ключа с использованием алгоритма Рида-Соломона.
    Включает проверку хэша ГОСТ Р 34.11-2018 (Стрибог).
    """
    sequence_id = request.form.get('sequence_id')
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID последовательности'}), 400

    # Получаем значение mismatches (N) из результата просеивания
    # Сначала проверяем, есть ли уже сохраненный sifted_key (значит просеивание уже было выполнено)
    mismatches = 0
    try:
        # Получаем данные последовательности
        query = "SELECT sifted_key FROM raw_data WHERE sequence_id = ?"
        result = db_manager.execute_query(query, (sequence_id,), fetch=True, sync=False)
        
        # Если есть sifted_key, значит просеивание было выполнено, получаем mismatches
        if result and result[0].get('sifted_key'):
            # Выполняем compare_bases для получения mismatches
            comparison_result = key_postprocessing_module.compare_bases(sequence_id)
            if 'error' not in comparison_result:
                mismatches = comparison_result.get('mismatches', 0)
                log_to_file(f"Получено mismatches={mismatches} для {sequence_id} из compare_bases", level="INFO")
        else:
            log_to_file(f"Для {sequence_id} нет sifted_key, mismatches будет 0", level="WARNING")
    except Exception as e:
        log_to_file(f"Не удалось получить mismatches для {sequence_id}: {e}", level="WARNING")
        import traceback
        log_to_file(f"Traceback: {traceback.format_exc()}", level="ERROR")
    
    # Используем новый модуль восстановления с полной диагностикой
    try:
        recovery_result = key_recovery_module.recover_key(sequence_id)
    except Exception as e:
        log_to_file(f"Исключение при восстановлении ключа {sequence_id}: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': f'Ошибка при восстановлении: {str(e)}'}), 500
    
    if recovery_result.get('status') == 'error':
        log_to_file(f"Восстановление ключа {sequence_id} вернуло ошибку: {recovery_result.get('message')}", level="ERROR")
        return jsonify(recovery_result), 500
    
    recovered_key = recovery_result.get('recovered_key', '')
    key_id = f"key_{sequence_id}"
    
    # Сохраняем ключ в защищённое хранилище
    if key_recovery_module.save_recovered_key(sequence_id, recovered_key):
        # Синхронизируем восстановленный ключ с сервером Б
        import requests
        try:
            requests.post(f'{get_remote_server_url()}/api/sync_recovered_key', json={
                'key_id': key_id,
                'sequence_id': sequence_id,
                'recovered_key': recovered_key,
                'length': len(recovered_key),
                'status': 'Активен',
                'hash': recovery_result.get('recovered_hash', '')
            }, timeout=2)
        except Exception as e:
            log_to_file(f"Не удалось синхронизировать восстановленный ключ: {e}", level="WARNING")
        
        log_to_file(f"Восстановление ключа {sequence_id}: mismatches={mismatches}, errors_corrected={mismatches}", level="INFO")
        return jsonify({
            'status': 'success', 
            'key_id': key_id,
            'recovery_percentage': recovery_result.get('recovery_percentage', 0),
            'recovery_time_ms': recovery_result.get('recovery_time_ms', 0),
            'hash_verified': recovery_result.get('hash_verified', False),
            'errors_corrected': mismatches,  # Используем mismatches (N) вместо errors_corrected
            'blocks_info': recovery_result.get('blocks_info', []),
            'blocks_total': recovery_result.get('blocks_total', 0)
        })
    else:
        return jsonify({'status': 'error', 'message': 'Не удалось сохранить восстановленный ключ'}), 500

# WebSocket события
# WebSocket клиент для подключения к серверу 2
qkd_socket_client = None

def init_qkd_socket_client():
    """Инициализирует WebSocket клиент для подключения к серверу 2"""
    global qkd_socket_client
    if sio_client is None:
        log_to_file("socketio не установлен, WebSocket клиент недоступен", level="WARNING")
        return
    
    try:
        remote_url = get_remote_server_url()
        # Извлекаем хост и порт из URL
        if remote_url.startswith('http://'):
            remote_url = remote_url[7:]
        elif remote_url.startswith('https://'):
            remote_url = remote_url[8:]
        
        # Создаем клиент для подключения к серверу 2
        qkd_socket_client = sio_client.Client()
        
        @qkd_socket_client.on('new_sequence')
        def on_new_sequence(data):
            """Обработчик получения новой последовательности от сервера 2"""
            try:
                # Отправляем данные всем подключенным клиентам сервера 1
                socketio.emit('new_sequence', data, namespace='/')
                log_to_file(f"Получена последовательность от сервера 2: {data.get('sequence_id')}", level="INFO")
            except Exception as e:
                log_to_file(f"Ошибка обработки последовательности от сервера 2: {e}", level="ERROR")
        
        @qkd_socket_client.on('sifting_complete')
        def on_sifting_complete(data):
            """Обработчик получения данных просеивания от сервера 2"""
            try:
                # Отправляем данные всем подключенным клиентам сервера 1
                socketio.emit('sifting_complete', data, namespace='/')
                log_to_file(f"Получены данные просеивания от сервера 2: {data.get('sequence_id')}, mismatches={data.get('mismatches')}", level="INFO")
            except Exception as e:
                log_to_file(f"Ошибка обработки данных просеивания от сервера 2: {e}", level="ERROR")
        
        @qkd_socket_client.on('connect')
        def on_connect():
            log_to_file("Подключен к серверу 2 через WebSocket", level="INFO")
        
        @qkd_socket_client.on('disconnect')
        def on_disconnect():
            log_to_file("Отключен от сервера 2 через WebSocket", level="WARNING")
        
        # Подключаемся к серверу 2
        ws_url = f"http://{remote_url}"
        qkd_socket_client.connect(ws_url, wait_timeout=5)
        log_to_file(f"WebSocket клиент подключен к {ws_url}", level="INFO")
    except Exception as e:
        error_msg = f"Ошибка инициализации WebSocket клиента: {e}"
        log_to_file(error_msg, level="ERROR")
        # Логируем в журнал
        try:
            from utils.logging_utils import log_to_db
            log_to_db(db_path, None, 'QKDModule', 'ERROR', error_msg)
        except:
            pass
        qkd_socket_client = None

@socketio.on('connect')
def handle_connect():
    """Обработчик подключения клиента через WebSocket"""
    log_to_file("WebSocket клиент подключен", level="INFO")
    emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    """Обработчик отключения клиента через WebSocket"""
    log_to_file("WebSocket клиент отключен", level="INFO")

@app.route('/api/sync_recovered_key', methods=['POST'])
def sync_recovered_key():
    """API endpoint для синхронизации восстановленного ключа"""
    try:
        data = request.get_json()
        sequence_id = data.get('sequence_id')
        recovered_key = data.get('recovered_key')
        
        if not sequence_id or not recovered_key:
            return jsonify({'status': 'error', 'message': 'Отсутствуют обязательные параметры'}), 400
        
        # Сохраняем ключ используя метод save_recovered_key, который обрабатывает дубликаты
        success = key_recovery_module.save_recovered_key(sequence_id, recovered_key, encrypt=True)
        
        if success:
            return jsonify({'status': 'success', 'message': 'Ключ синхронизирован'})
        else:
            return jsonify({'status': 'error', 'message': 'Не удалось сохранить ключ'}), 500
            
    except Exception as e:
        log_to_file(f"Ошибка синхронизации восстановленного ключа: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/get_test_sequence/<sequence_id>', methods=['GET'])
def get_test_sequence(sequence_id):
    """API endpoint для получения тестовой последовательности"""
    try:
        result = db_manager.execute_query(
            "SELECT bits, bases, station FROM raw_data WHERE sequence_id = ? AND is_test = TRUE",
            (sequence_id,), fetch=True, sync=False
        )
        if result:
            return jsonify({
                'status': 'success',
                'bits': result[0]['bits'],
                'bases': result[0]['bases'],
                'station': result[0]['station']
            })
        else:
            return jsonify({'status': 'error', 'message': 'Последовательность не найдена'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/generate_test_sequence', methods=['POST'])
def generate_test_sequence():
    qber_value_input = request.form.get('qber_value')
    if not qber_value_input or qber_value_input == '':
        return jsonify({'status': 'error', 'message': 'Необходимо указать значение QBER'}), 400

    try:
        qber_value = float(qber_value_input)
    except ValueError as e:
        log_to_file(f"Ошибка парсинга QBER: {e}, input: {qber_value_input}", level="ERROR")
        return jsonify({'status': 'error', 'message': 'Некорректное значение QBER'}), 400
    
    log_to_file(f"Генерация тестовой последовательности. QBER: {qber_value}%", level="INFO")
    
    # Уведомляем второй сервер о начале генерации (блокируем его интерфейс)
    import requests
    try:
        requests.post(f'{get_remote_server_url()}/api/sync_test_mode_state', json={
            'is_generating': True,
            'qber_value': qber_value,
            'connection_time': None,
            'generation_status': None,
            'bases_comparison': None
        }, timeout=2)
    except Exception as e:
        log_to_file(f"Не удалось синхронизировать состояние тестирования: {e}", level="WARNING")
    
    # Эмуляция подключения к QKD-устройству
    conn_time = round(random.uniform(1.1, 3.0), 2)
    time.sleep(conn_time)

    if qber_value > 12:
        return jsonify({
            'status': 'error',
            'message': 'Отказ в обслуживании, ключевая последовательность скомпрометирована',
            'connection_time': conn_time,
            'generation_status': '100%',
            'bases_comparison': 'Отказ в обслуживании, ключевая последовательность скомпрометирована',
            'key_recovery': None,
            'recovery_time': None,
            'encryption_time': None,
            'key_destruction': None
        }), 400

    # 1: Генерация последовательностей для A и B
    bits_a = ''.join(str(random.randint(0, 1)) for _ in range(256))
    bits_b_list = list(bits_a)
    # Сделать ошибки в bits_b (QBER)
    error_count = int(256 * qber_value / 100)
    error_indices = random.sample(range(256), error_count)
    for idx in error_indices:
        bits_b_list[idx] = '1' if bits_b_list[idx] == '0' else '0'
    bits_b = ''.join(bits_b_list)
    bases_a = ''.join(random.choice(['+', 'x']) for _ in range(256))
    bases_b = ''.join(random.choice(['+', 'x']) for _ in range(256)) # эмулируем разные базисы, возможно, сделать частичное совпадение

    # Сохранить тестовые последовательности в обеих БД
    timestamp = moscow_now_str('%Y%m%d%H%M%S')
    seq_id_a = f"testA_{timestamp}"
    seq_id_b = f"testB_{timestamp}"

    # Сохраняем последовательность А в свою БД
    db_manager.execute_query(
        "INSERT INTO raw_data (sequence_id, bits, bases, station, selected_by, is_test) VALUES (?, ?, ?, ?, ?, ?)",
        (seq_id_a, bits_a, bases_a, 'A', 'Тестовая', True), sync=False
    )
    
    # Отправляем данные через WebSocket для анимации
    try:
        last_32_bits_a = bits_a[-32:] if len(bits_a) >= 32 else bits_a
        last_32_bases_a = bases_a[-32:] if len(bases_a) >= 32 else bases_a
        socketio.emit('new_sequence', {
            'sequence_id': seq_id_a,
            'last_32_bits': last_32_bits_a,
            'last_32_bases': last_32_bases_a,
            'is_test': True,
            'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
        }, namespace='/')
    except Exception as e:
        log_to_file(f"Ошибка отправки тестовой последовательности через WebSocket: {e}", level="WARNING")
    
    # Отправляем последовательность Б на сервер Б через REST API
    import requests
    try:
        requests.post(f'{get_remote_server_url()}/api/save_test_sequence', json={
            'sequence_id': seq_id_b,
            'bits': bits_b,
            'bases': bases_b,
            'station': 'B'
        }, timeout=2)
    except Exception as e:
        log_to_file(f"Не удалось отправить тестовую последовательность на сервер Б: {e}", level="WARNING")

    # 2. Cверка базисов (метрика QBER)
    mismatches = sum([1 for x, y in zip(bits_a, bits_b) if x != y])
    factual_qber = round((mismatches / 256) * 100, 2)
    bases_comparison = f'Выполнена, значение QBER {factual_qber}%'

    # 3. Восстановление ключа (RSC) с проверкой хэша ГОСТ Р 34.11-2018
    start_recover = time.perf_counter()
    # Кодируем и декодируем bits_b как эмулируемый "поврежденный"
    data_bytes = int(bits_b, 2).to_bytes((len(bits_b) + 7) // 8, byteorder='big')
    
    # Вычисляем хэш до восстановления (ГОСТ Р 34.11-2018 Стрибог)
    original_hash = GOSTHash.hash_256(data_bytes)
    
    encoded = encode_reed_solomon(data_bytes)
    recovered_data, errors_corrected = decode_reed_solomon(encoded)
    recover_bits = ''.join(format(b, '08b') for b in recovered_data)[:256] if recovered_data else bits_b
    
    # Вычисляем хэш после восстановления
    recovered_bytes = int(recover_bits, 2).to_bytes((len(recover_bits) + 7) // 8, byteorder='big')
    recovered_hash = GOSTHash.hash_256(recovered_bytes)
    hash_match = original_hash == recovered_hash
    
    end_recover = time.perf_counter()
    # Посчитать % совпадения с bits_a (эталонный ключ)
    correct_blocks = sum(1 for a, b in zip(recover_bits, bits_a) if a == b)
    recovery_percentage = round((correct_blocks / 256) * 100, 2)
    recovery_time = round((end_recover - start_recover) * 1000, 1) # мс

    # 4. Метрика времени шифрования/дешифрования ГОСТ Р 34.12-2018 (Кузнечик)
    key = os.urandom(32)  # 256-битный ключ для ГОСТ
    
    try:
        gost_cipher = GOSTCipher(key)
        text_for_enc = os.urandom(4096)  # эмулируем отправку файла/сообщения
        
        start_enc = time.perf_counter()
        enc_data = gost_cipher.encrypt(text_for_enc)
        end_enc = time.perf_counter()
        
        start_dec = time.perf_counter()
        dec_data = gost_cipher.decrypt(enc_data)
        end_dec = time.perf_counter()
        
        enc_time = round(end_enc - start_enc, 4)
        dec_time = round(end_dec - start_dec, 4)
        encryption_time = f"ГОСТ: Шифрование {enc_time} сек, Дешифрование {dec_time} сек"
        
        # Проверяем корректность шифрования/дешифрования
        if dec_data != text_for_enc:
            encryption_time += " (ОШИБКА: данные не совпадают)"
            
    except Exception as e:
        log_to_file(f"Ошибка ГОСТ шифрования в тестовом режиме: {e}", level="ERROR")
        # Fallback на AES если ГОСТ недоступен
        aes_cipher = AESCipher(key)
        text_for_enc = os.urandom(4096)
        start_enc = time.perf_counter()
        enc_data = aes_cipher.encrypt(text_for_enc)
        end_enc = time.perf_counter()
        start_dec = time.perf_counter()
        _ = aes_cipher.decrypt(enc_data)
        end_dec = time.perf_counter()
        enc_time = round(end_enc - start_enc, 4)
        dec_time = round(end_dec - start_dec, 4)
        encryption_time = f"AES (fallback): Шифрование {enc_time} сек, Дешифрование {dec_time} сек"

    # 5. Симуляция уничтожения ключа после использования
    key_destruction = "Использован и уничтожен"

    # Обновляем состояние тестирования на втором сервере с метриками
    try:
        requests.post(f'{get_remote_server_url()}/api/sync_test_mode_state', json={
            'is_generating': False,
            'qber_value': qber_value,
            'connection_time': f'{conn_time} сек',
            'generation_status': '100%',
            'bases_comparison': bases_comparison,
            'key_recovery': f'{recovery_percentage}% (хэш: {"OK" if hash_match else "НЕ совпадает"})',
            'recovery_time': f'{recovery_time} мс',
            'encryption_time': encryption_time,
            'key_destruction': key_destruction
        }, timeout=2)
    except Exception as e:
        log_to_file(f"Не удалось синхронизировать метрики тестирования: {e}", level="WARNING")

    # Получаем последние 32 бита обеих последовательностей для визуализации
    last_32_bits_a = bits_a[-32:] if len(bits_a) >= 32 else bits_a
    last_32_bits_b = bits_b[-32:] if len(bits_b) >= 32 else bits_b
    
    # Дополняем до одинаковой длины для корректного сравнения
    max_len = max(len(last_32_bits_a), len(last_32_bits_b))
    if len(last_32_bits_a) < max_len:
        last_32_bits_a = last_32_bits_a.ljust(max_len, '0')
    if len(last_32_bits_b) < max_len:
        last_32_bits_b = last_32_bits_b.ljust(max_len, '0')
    
    return jsonify({
        'status': 'success',
        'connection_time': f'{conn_time} сек',
        'generation_status': '100%',
        'bases_comparison': bases_comparison,
        'factual_qber': factual_qber,
        'mismatches': mismatches,  # Добавляем количество несовпадений
        'key_recovery': f'{recovery_percentage}% (хэш: {"OK" if hash_match else "НЕ совпадает"})',
        'recovery_time': f'{recovery_time} мс',
        'errors_corrected': errors_corrected if errors_corrected >= 0 else 'Ошибка',
        'encryption_time': encryption_time,
        'key_destruction': key_destruction,
        'seq_id_a': seq_id_a,
        'seq_id_b': seq_id_b,
        'last_32_bits_local': last_32_bits_a,
        'last_32_bits_remote': last_32_bits_b
    })


@app.route('/api/sync_test_mode_state', methods=['POST'])
def sync_test_mode_state():
    """API endpoint для синхронизации состояния режима тестирования от другого сервера"""
    try:
        data = request.get_json()
        # Сохраняем состояние в глобальной переменной для доступа из JavaScript
        if not hasattr(sync_test_mode_state, 'test_state'):
            sync_test_mode_state.test_state = {}
        sync_test_mode_state.test_state.update(data)
        return jsonify({'status': 'success', 'message': 'Состояние синхронизировано'})
    except Exception as e:
        log_to_file(f"Ошибка синхронизации состояния тестирования: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/get_test_mode_state', methods=['GET'])
def get_test_mode_state():
    """API endpoint для получения текущего состояния режима тестирования"""
    try:
        if hasattr(sync_test_mode_state, 'test_state'):
            return jsonify({'status': 'success', 'state': sync_test_mode_state.test_state})
        else:
            return jsonify({'status': 'success', 'state': {'is_generating': False}})
    except Exception as e:
        log_to_file(f"Ошибка получения состояния тестирования: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ============================================
# ENDPOINTS ДЛЯ ШИФРОВАНИЯ/ДЕШИФРОВАНИЯ СООБЩЕНИЙ
# ============================================

@app.route('/encrypt_message', methods=['POST'])
def encrypt_message():
    """
    Шифрует сообщение (текст и/или файл) и отправляет его получателю.
    Использует ГОСТ Р 34.12-2018 (Кузнечик) в режиме CTR.
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    message_text = request.form.get('message', '')
    key_id = request.form.get('key_id')
    receiver_id = request.form.get('receiver_id', 2)  # По умолчанию отправляем абоненту Б
    file = request.files.get('file')
    
    if not message_text and not file:
        return jsonify({'status': 'error', 'message': 'Необходимо ввести текст или выбрать файл'}), 400
    if not key_id:
        return jsonify({'status': 'error', 'message': 'Не выбран ключ для шифрования'}), 400
    
    try:
        receiver_id = int(receiver_id)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Некорректный ID получателя'}), 400
    
    try:
        # Определяем тип сообщения
        if message_text and file:
            message_type = 'text_and_file'
        elif file:
            message_type = 'file'
        else:
            message_type = 'text'
        
        # Шифруем текст если есть
        encrypted_text = None
        text_hash = None
        if message_text:
            result_text = message_crypto.encrypt_message(
                plaintext=message_text,
                key_id=key_id,
                sender_id=session['user_id'],
                receiver_id=receiver_id
            )
            if result_text['status'] != 'success':
                return jsonify(result_text)
            encrypted_text = result_text['ciphertext']
            text_hash = result_text['hash']
        
        # Шифруем файл если есть
        encrypted_file = None
        file_hash = None
        file_name = None
        file_size = None
        if file:
            file_content = file.read()
            file_name = file.filename
            file_size = len(file_content)
            
            result_file = message_crypto.encrypt_file(
                file_content=file_content,
                file_name=file_name,
                key_id=key_id,
                sender_id=session['user_id'],
                receiver_id=receiver_id
            )
            if result_file['status'] != 'success':
                return jsonify(result_file)
            encrypted_file = result_file['ciphertext']
            file_hash = result_file['hash']
        
        # Отправляем зашифрованное сообщение на сервер получателя
        # Получаем username получателя для передачи на сервер получателя
        receiver_query = "SELECT username FROM users WHERE user_id = ?"
        receiver_result = db_manager.execute_query(receiver_query, (receiver_id,), fetch=True, sync=False)
        receiver_name = receiver_result[0]['username'] if receiver_result else None
        
        try:
            payload = {
                'sender_id': session['user_id'],
                'sender_name': session.get('username', 'Unknown'),
                'receiver_id': receiver_id,  # Передаем receiver_id получателю (для обратной совместимости)
                'receiver_name': receiver_name,  # Передаем username получателя
                'key_id': key_id,
                'message_type': message_type
            }
            
            if encrypted_text:
                payload['ciphertext'] = encrypted_text
                payload['hash'] = text_hash
            
            if encrypted_file:
                payload['file_ciphertext'] = encrypted_file
                payload['file_hash'] = file_hash
                payload['file_name'] = file_name
                payload['file_size'] = file_size
            
            response = requests.post(
                f'{get_remote_server_url()}/api/receive_encrypted_message',
                json=payload,
                timeout=5
            )
            
            if response.status_code == 200:
                sent_to_receiver = True
            else:
                sent_to_receiver = False
                log_to_file(f"Ошибка отправки: {response.text}", level="WARNING")
        except Exception as e:
            log_to_file(f"Ошибка отправки сообщения: {e}", level="WARNING")
            sent_to_receiver = False
        
        return jsonify({
            'status': 'success',
            'message_type': message_type,
            'key_id': key_id,
            'encryption_time_ms': result_text.get('encryption_time_ms', 0) if message_text else result_file.get('encryption_time_ms', 0),
            'sent_to_receiver': sent_to_receiver
        })
        
    except Exception as e:
        log_to_file(f"Ошибка шифрования: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/decrypt_message', methods=['POST'])
def decrypt_message():
    """
    Дешифрует полученное сообщение (текст и/или файл).
    После дешифрования ключ уничтожается (политика одноразового использования).
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    message_id = request.form.get('message_id')
    
    if not message_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID сообщения'}), 400
    
    try:
        message_id = int(message_id)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Некорректный ID сообщения'}), 400
    
    # Получаем сообщение из БД
    message = message_crypto.get_encrypted_message(message_id)
    if not message:
        log_to_file(f"Ошибка дешифрования: сообщение {message_id} не найдено для user_id={session['user_id']}", level="ERROR")
        return jsonify({'status': 'error', 'message': 'Сообщение не найдено'}), 404
    
    # Проверяем, что сообщение адресовано текущему пользователю
    if message['receiver_id'] != session['user_id']:
        log_to_file(f"Ошибка доступа: сообщение {message_id} адресовано receiver_id={message['receiver_id']}, но запрос от user_id={session['user_id']}", level="WARNING")
        return jsonify({'status': 'error', 'message': 'Нет доступа к этому сообщению'}), 403
    
    # Проверяем наличие key_id
    key_id = message.get('key_id')
    if not key_id:
        log_to_file(f"Ошибка дешифрования: у сообщения {message_id} отсутствует key_id", level="ERROR")
        return jsonify({'status': 'error', 'message': 'У сообщения отсутствует ключ шифрования'}), 400
    
    message_type = message.get('message_type', 'text')
    sender_name = message.get('sender_name', 'Неизвестно')
    result = {'status': 'success', 'sender_name': sender_name}
    
    log_to_file(f"Начало дешифрования сообщения {message_id} от {sender_name} для user_id={session['user_id']}, key_id={key_id}", level="INFO")
    
    try:
        # Дешифруем текст если есть
        if message_type in ['text', 'text_and_file'] and message.get('content'):
            # Если есть и файл, ключ не уничтожаем при дешифровании текста
            destroy_key_in_text = (message_type == 'text')
            text_result = message_crypto.decrypt_message(
                ciphertext_b64=message['content'],
                key_id=message['key_id'],
                user_id=session['user_id'],
                expected_hash=message.get('content_hash'),
                destroy_key=destroy_key_in_text
            )
            
            if text_result['status'] != 'success':
                log_to_file(f"Ошибка дешифрования текста сообщения {message_id}: {text_result.get('message', 'Unknown error')}", level="ERROR")
                return jsonify(text_result)
            
            result['plaintext'] = text_result['plaintext']
            result['hash_verified'] = text_result['hash_verified']
            result['decryption_time_ms'] = text_result['decryption_time_ms']
            if destroy_key_in_text:
                result['key_destroyed'] = text_result.get('key_destroyed', False)
        
        # Дешифруем файл если есть
        if message_type in ['file', 'text_and_file'] and message.get('file_path'):
            # Ключ уничтожаем только если это последний компонент сообщения
            destroy_key = True
            file_result = message_crypto.decrypt_file(
                ciphertext_b64=message['file_path'],
                key_id=message['key_id'],
                user_id=session['user_id'],
                expected_hash=message.get('content_hash') if message_type == 'file' else None,
                destroy_key=destroy_key
            )
            
            if file_result['status'] != 'success':
                log_to_file(f"Ошибка дешифрования файла сообщения {message_id}: {file_result.get('message', 'Unknown error')}", level="ERROR")
                return jsonify(file_result)
            
            # Сохраняем файл для скачивания
            import tempfile
            import os
            file_name = message.get('file_name', 'decrypted_file')
            # Создаем временный файл с правильным расширением
            # Используем tempfile.gettempdir() для получения директории временных файлов
            temp_dir = tempfile.gettempdir()
            # Сохраняем оригинальное имя файла в отдельной переменной
            original_file_name = file_name
            # Создаем уникальное имя файла
            import time
            unique_name = f"decrypted_{int(time.time())}_{original_file_name}"
            temp_file_path = os.path.join(temp_dir, unique_name)
            
            with open(temp_file_path, 'wb') as f:
                f.write(file_result['file_content'])
            
            # Создаем URL для скачивания
            import urllib.parse
            file_path_encoded = urllib.parse.quote(temp_file_path)
            download_url = f'/download_decrypted_file/{file_path_encoded}'
            
            result['file_path'] = temp_file_path
            result['file_name'] = original_file_name
            result['file_size'] = file_result['file_size']
            result['file_hash_verified'] = file_result['hash_verified']
            result['download_url'] = download_url
            
            if 'decryption_time_ms' not in result:
                result['decryption_time_ms'] = file_result['decryption_time_ms']
            result['key_destroyed'] = file_result.get('key_destroyed', False)
        
        # Помечаем сообщение как прочитанное
        message_crypto.mark_message_as_read(message_id, session['user_id'])
        
        return jsonify(result)
        
    except Exception as e:
        log_to_file(f"Ошибка дешифрования: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/download_decrypted_file/<path:file_path>')
def download_decrypted_file(file_path):
    """Скачивание дешифрованного файла."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        from flask import send_file
        import os
        import urllib.parse
        
        # Декодируем URL-encoded путь
        file_path = urllib.parse.unquote(file_path)
        
        if not os.path.exists(file_path):
            log_to_file(f"Файл не найден: {file_path}", level="ERROR")
            return jsonify({'status': 'error', 'message': 'Файл не найден'}), 404
        
        # Получаем имя файла из пути
        file_name = os.path.basename(file_path)
        # Извлекаем оригинальное имя файла (после префикса decrypted_TIMESTAMP_)
        if file_name.startswith('decrypted_'):
            # Формат: decrypted_TIMESTAMP_original_name.ext
            parts = file_name.split('_', 2)  # Разделяем на ['decrypted', 'TIMESTAMP', 'original_name.ext']
            if len(parts) >= 3:
                file_name = parts[2]  # Берем оригинальное имя
        
        log_to_file(f"Скачивание файла: {file_path} как {file_name}", level="INFO")
        
        return send_file(
            file_path,
            as_attachment=True,
            download_name=file_name
        )
    except Exception as e:
        log_to_file(f"Ошибка скачивания файла: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/receive_encrypted_message', methods=['POST'])
def receive_encrypted_message():
    """
    API endpoint для приёма зашифрованного сообщения от другого абонента.
    """
    try:
        data = request.get_json()
        
        sender_id = data.get('sender_id')
        sender_name = data.get('sender_name', 'Unknown')
        # receiver_id из запроса игнорируем - определяем на стороне получателя
        key_id = data.get('key_id')
        if not key_id:
            log_to_file(f"ОШИБКА: receive_encrypted_message получил сообщение без key_id от sender_name={sender_name}, sender_id={sender_id}", level="ERROR")
            return jsonify({'status': 'error', 'message': 'Отсутствует key_id в сообщении'}), 400
        message_type = data.get('message_type', 'text')
        
        # Текстовое сообщение
        ciphertext = data.get('ciphertext', '')
        content_hash = data.get('hash', '')
        
        # Файл
        file_ciphertext = data.get('file_ciphertext', '')
        file_hash = data.get('file_hash', '')
        file_name = data.get('file_name')
        file_size = data.get('file_size')
        
        # Получаем receiver_id из запроса (это ID на сервере отправителя)
        # Но нам нужно найти правильный user_id получателя на ЭТОМ сервере
        receiver_id_from_request = data.get('receiver_id')
        receiver_name = data.get('receiver_name')  # Имя получателя (если передано)
        
        log_to_file(f"DEBUG: receive_encrypted_message: получен receiver_id={receiver_id_from_request}, receiver_name={receiver_name}", level="INFO")
        
        # ВАЖНО: Сохраняем сообщение для ВСЕХ пользователей с ролью 'user' на этом сервере,
        # исключая отправителя (если он есть на этом сервере).
        # Это гарантирует, что сообщение будет доступно любому авторизованному пользователю,
        # независимо от того, кто именно авторизован в данный момент.
        receiver_ids_to_save = []
        
        # Ищем всех пользователей с ролью 'user' на этом сервере
        # ВАЖНО: sender_id - это ID отправителя на сервере отправителя, 
        # на этом сервере может быть другой пользователь с таким же ID
        # Поэтому исключаем отправителя только если он действительно существует на этом сервере
        # И проверяем по username, а не по user_id, так как user_id могут не совпадать между серверами
        user_query = "SELECT user_id, username FROM users WHERE role = 'user'"
        if sender_id and sender_name:
            # Проверяем, есть ли на этом сервере пользователь с таким же username как отправитель
            sender_check = db_manager.execute_query(
                "SELECT user_id, username FROM users WHERE username = ?",
                (sender_name,),
                fetch=True,
                sync=False
            )
            if sender_check:
                # Если нашли пользователя с таким же username, исключаем его
                sender_user_id = sender_check[0]['user_id']
                user_query += f" AND user_id != {sender_user_id}"
                log_to_file(f"DEBUG: Исключаем отправителя с username='{sender_name}' (user_id={sender_user_id}) из списка получателей", level="INFO")
        
        user_result = db_manager.execute_query(user_query, fetch=True, sync=False)
        if user_result:
            receiver_ids_to_save = [user['user_id'] for user in user_result]
            log_to_file(f"DEBUG: Найдено получателей с ролью 'user' (исключая отправителя): {receiver_ids_to_save}", level="INFO")
            # Логируем детали каждого найденного пользователя
            for user in user_result:
                log_to_file(f"DEBUG: Пользователь найден: user_id={user.get('user_id')}, username={user.get('username')}, role='user'", level="INFO")
        else:
            log_to_file(f"DEBUG: Не найдено пользователей с ролью 'user' на этом сервере", level="ERROR")
        
        # Дополнительная проверка: получаем ВСЕХ пользователей на сервере для отладки
        all_users_query = "SELECT user_id, username, role FROM users"
        all_users_result = db_manager.execute_query(all_users_query, fetch=True, sync=False) or []
        log_to_file(f"DEBUG: Все пользователи на сервере: {[(u.get('user_id'), u.get('username'), u.get('role')) for u in all_users_result]}", level="INFO")
        
        if not receiver_ids_to_save:
            return jsonify({'status': 'error', 'message': 'Не удалось определить получателя'}), 400
        
        log_to_file(f"DEBUG: receive_encrypted_message: sender_id={sender_id}, sender_name={sender_name}, receiver_ids={receiver_ids_to_save}, key_id={key_id}", level="INFO")
        
        # Сохраняем сообщение для каждого получателя
        query = f"""
            INSERT INTO messages 
            (sender_id, sender_name, receiver_id, key_id, message_type, content, content_hash,
             file_path, file_name, file_size, is_encrypted, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, {moscow_datetime_sql()})
        """
        
        for rec_id in receiver_ids_to_save:
            try:
                db_manager.execute_query(
                    query,
                    (sender_id, sender_name, rec_id, key_id, message_type, ciphertext,
                     content_hash, file_ciphertext, file_name, file_size),
                    sync=False
                )
                # Проверяем, что сообщение действительно сохранено
                check_query = "SELECT message_id, receiver_id, is_encrypted, key_id FROM messages WHERE receiver_id = ? AND sender_id = ? AND key_id = ? ORDER BY sent_at DESC LIMIT 1"
                check_result = db_manager.execute_query(check_query, (rec_id, sender_id, key_id), fetch=True, sync=False)
                if check_result:
                    log_to_file(f"✓ Сообщение сохранено: message_id={check_result[0].get('message_id')}, receiver_id={check_result[0].get('receiver_id')}, key_id={check_result[0].get('key_id')}, is_encrypted={check_result[0].get('is_encrypted')}", level="INFO")
                else:
                    log_to_file(f"✗ ОШИБКА: Сообщение НЕ найдено в БД после сохранения для receiver_id={rec_id}, sender_id={sender_id}, key_id={key_id}", level="ERROR")
            except Exception as e:
                log_to_file(f"✗ ОШИБКА при сохранении сообщения для receiver_id={rec_id}: {e}", level="ERROR")
        
        log_to_file(f"Сохранено сообщение от {sender_name} (sender_id={sender_id}) для receiver_ids={receiver_ids_to_save}, is_encrypted=1", level="INFO")
        print(f"DEBUG: Сохранено сообщение от {sender_name} для receiver_ids={receiver_ids_to_save}")
        
        print(f"DEBUG: key_id={key_id}, message_type={message_type}")
        print(f"DEBUG: Сообщение успешно сохранено в БД")
        
        log_to_file(f"Получено зашифрованное сообщение от {sender_name} (key={key_id})", level="INFO")
        
        return jsonify({'status': 'success', 'message': 'Сообщение получено'})
        
    except Exception as e:
        log_to_file(f"Ошибка приёма сообщения: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/get_available_keys', methods=['GET'])
def get_available_keys():
    """Возвращает список доступных ключей для шифрования."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    keys = key_management_module.get_available_keys()
    return jsonify({'status': 'success', 'keys': keys})


@app.route('/destroy_key', methods=['POST'])
def destroy_key():
    """Уничтожает ключ (безвозвратно)."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    key_id = request.form.get('key_id')
    if not key_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID ключа'}), 400
    
    success = key_management_module.destroy_key(key_id, session['user_id'])
    
    if success:
        # Синхронизируем уничтожение с другим сервером
        try:
            requests.post(
                f'{get_remote_server_url()}/api/sync_key_destruction',
                json={'key_id': key_id},
                timeout=2
            )
        except Exception as e:
            log_to_file(f"Не удалось синхронизировать уничтожение ключа: {e}", level="WARNING")
        
        return jsonify({'status': 'success', 'message': f'Ключ {key_id} уничтожен'})
    else:
        return jsonify({'status': 'error', 'message': 'Не удалось уничтожить ключ'}), 500


@app.route('/api/get_incoming_messages', methods=['GET'])
def api_get_incoming_messages():
    """API endpoint для получения входящих сообщений."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        user_id = session['user_id']
        username = session.get('username', 'Unknown')
        role = session.get('role', 'Unknown')
        log_to_file(f"DEBUG: Получение входящих сообщений для user_id={user_id}, username={username}, role={role}", level="INFO")
        incoming_items = key_management_module.get_incoming_messages(user_id)
        log_to_file(f"DEBUG: Найдено входящих сообщений: {len(incoming_items)}", level="INFO")
        if incoming_items:
            log_to_file(f"DEBUG: Первое сообщение: {incoming_items[0]}", level="INFO")
        return jsonify({
            'status': 'success',
            'messages': incoming_items,
            'count': len(incoming_items)
        })
    except Exception as e:
        log_to_file(f"Ошибка получения входящих сообщений: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/get_sent_messages', methods=['GET'])
def api_get_sent_messages():
    """API endpoint для получения отправленных сообщений."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        user_id = session['user_id']
        log_to_file(f"DEBUG: Получение отправленных сообщений для user_id={user_id}", level="INFO")
        sent_items = key_management_module.get_sent_messages(user_id)
        log_to_file(f"DEBUG: Найдено отправленных сообщений: {len(sent_items)}", level="INFO")
        return jsonify({
            'status': 'success',
            'messages': sent_items,
            'count': len(sent_items)
        })
    except Exception as e:
        log_to_file(f"Ошибка получения отправленных сообщений: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/view_sent_message', methods=['POST'])
def api_view_sent_message():
    """API endpoint для просмотра информации об отправленном сообщении."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    message_id = request.form.get('message_id')
    if not message_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID сообщения'}), 400
    
    try:
        message_id = int(message_id)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Некорректный ID сообщения'}), 400
    
    try:
        # Получаем сообщение из БД
        message = message_crypto.get_encrypted_message(message_id)
        if not message:
            return jsonify({'status': 'error', 'message': 'Сообщение не найдено'}), 404
        
        # Проверяем, что сообщение отправлено текущим пользователем
        if message['sender_id'] != session['user_id']:
            return jsonify({'status': 'error', 'message': 'Нет доступа к этому сообщению'}), 403
        
        # Получаем username получателя из таблицы users
        receiver_query = "SELECT username FROM users WHERE user_id = ?"
        receiver_result = db_manager.execute_query(receiver_query, (message['receiver_id'],), fetch=True, sync=False)
        receiver_name = receiver_result[0]['username'] if receiver_result else 'Неизвестно'
        
        # Форматируем дату прочтения
        from utils.timezone_utils import format_datetime_for_display
        read_at = format_datetime_for_display(message.get('read_at', '')) if message.get('read_at') else None
        
        return jsonify({
            'status': 'success',
            'receiver_name': receiver_name,
            'timestamp': format_datetime_for_display(message.get('sent_at', '')),
            'message_type': message.get('message_type', 'text'),
            'key_id': message.get('key_id', ''),
            'plaintext': message.get('plaintext', '')  # Оригинальный текст сообщения
        })
    except Exception as e:
        log_to_file(f"Ошибка получения информации о сообщении: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/get_all_tables', methods=['GET'])
def get_all_tables():
    """Возвращает данные всех таблиц для администратора."""
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Доступ запрещён'}), 403
    
    try:
        # Получаем данные из всех таблиц
        users = db_manager.execute_query("SELECT * FROM users", fetch=True, sync=False) or []
        raw_data = db_manager.execute_query("SELECT * FROM raw_data ORDER BY generated_at DESC", fetch=True, sync=False) or []
        keys = db_manager.execute_query("SELECT * FROM keys ORDER BY created_at DESC", fetch=True, sync=False) or []
        messages = db_manager.execute_query("SELECT * FROM messages ORDER BY sent_at DESC", fetch=True, sync=False) or []
        logs = db_manager.execute_query("SELECT * FROM logs ORDER BY created_at DESC LIMIT 100", fetch=True, sync=False) or []
        sessions_data = db_manager.execute_query("SELECT * FROM sessions ORDER BY created_at DESC", fetch=True, sync=False) or []
        
        # Получаем данные из таблицы settings
        try:
            settings = db_manager.execute_query("SELECT * FROM settings", fetch=True, sync=False) or []
        except:
            settings = []
        
        # Получаем данные из таблицы test_messages
        try:
            test_messages = db_manager.execute_query("SELECT * FROM test_messages ORDER BY created_at DESC LIMIT 100", fetch=True, sync=False) or []
        except:
            test_messages = []
        
        return jsonify({
            'status': 'success',
            'tables': {
                'users': users,
                'raw_data': raw_data,
                'keys': keys,
                'messages': messages,
                'logs': logs,
                'sessions': sessions_data,
                'settings': settings,
                'test_messages': test_messages
            }
        })
    except Exception as e:
        log_to_file(f"Ошибка получения данных таблиц: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/delete_sequence', methods=['POST'])
def delete_sequence():
    """Удаляет последовательность из raw_data."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    sequence_id = request.form.get('sequence_id')
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID последовательности'}), 400
    
    try:
        db_manager.execute_query(
            "DELETE FROM raw_data WHERE sequence_id = ?",
            (sequence_id,),
            sync=False
        )
        log_to_file(f"Последовательность {sequence_id} удалена пользователем {session['username']}", level="INFO")
        return jsonify({'status': 'success', 'message': 'Последовательность удалена'})
    except Exception as e:
        log_to_file(f"Ошибка удаления последовательности: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/test_rest_send', methods=['POST'])
def test_rest_send():
    """
    ТЕСТОВЫЙ ENDPOINT: Отправка тестового сообщения через REST API.
    Можно удалить после тестирования без последствий.
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    test_message = request.form.get('test_message', '')
    
    if not test_message:
        return jsonify({'status': 'error', 'message': 'Сообщение не может быть пустым'}), 400
    
    try:
        import time
        from datetime import datetime as dt
        start_time = time.perf_counter()
        
        sender = session.get('username', 'Unknown')  # Реальное имя отправителя
        
        # Отправляем тестовое сообщение на сервер Б через REST API
        response = requests.post(
            f'{get_remote_server_url()}/api/test_rest_receive',
            json={
                'message': test_message,
                'sender': sender
            },
            timeout=5
        )
        
        end_time = time.perf_counter()
        response_time = round((end_time - start_time) * 1000, 2)
        
        if response.status_code == 200:
            response_data = response.json()
            receiver = response_data.get('receiver', 'server_2')  # Получаем имя получателя из ответа
            
            # Сохраняем отправленное сообщение в локальную БД (сервер 1)
            db_manager_test = DatabaseManager(db_path)
            query_sent = f'''
                INSERT INTO test_messages (sender, receiver, message_text, direction, created_at)
                VALUES (?, ?, ?, 'sent', {moscow_datetime_sql()})
            '''
            db_manager_test.execute_query(query_sent, (sender, receiver, test_message), sync=False)
            log_to_file(f"Сохранено отправленное сообщение от {sender} для {receiver}: {test_message}", level="INFO")
            
            return jsonify({
                'status': 'success',
                'message': 'Сообщение отправлено через REST API',
                'response_time_ms': response_time,
                'remote_server': get_remote_server_url(),
                'receiver': receiver
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'Ошибка сервера: {response.status_code}'
            }), 500
            
    except Exception as e:
        log_to_file(f"Ошибка тестирования REST API: {e}", level="ERROR")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/api/test_rest_receive', methods=['POST'])
def test_rest_receive():
    """
    ТЕСТОВЫЙ ENDPOINT: Приём тестового сообщения через REST API.
    Сохраняет сообщение в базу данных.
    Можно удалить после тестирования без последствий.
    """
    try:
        data = request.get_json()
        message = data.get('message', '')
        sender = data.get('sender', 'Unknown')
        
        # Определяем получателя - это имя сервера (server_1)
        receiver = 'server_1'
        
        # Сохраняем полученное сообщение в базу данных
        db_manager_test = DatabaseManager(db_path)
        query = f'''
            INSERT INTO test_messages (sender, receiver, message_text, direction, created_at)
            VALUES (?, ?, ?, 'received', {moscow_datetime_sql()})
        '''
        db_manager_test.execute_query(query, (sender, receiver, message), sync=False)
        
        log_to_file(f"Получено тестовое сообщение от {sender} для {receiver}: {message}", level="INFO")
        
        # Возвращаем имя получателя (как в примере)
        return jsonify({
            'status': 'success',
            'receiver': receiver,
            'message': 'Сообщение получено и сохранено в БД'
        })
        
    except Exception as e:
        log_to_file(f"Ошибка приёма тестового сообщения: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/test_rest_get_messages', methods=['GET'])
def test_rest_get_messages():
    """
    ТЕСТОВЫЙ ENDPOINT: Получение списка тестовых сообщений из БД.
    Можно удалить после тестирования без последствий.
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        # Фильтруем по имени сервера (server_1) для полученных сообщений
        receiver = 'server_1'
        log_to_file(f"DEBUG: Получение тестовых сообщений для получателя: {receiver}", level="INFO")
        
        # Получаем только полученные сообщения из БД для сервера
        db_manager_test = DatabaseManager(db_path)
        query = '''
            SELECT message_id, sender, receiver, message_text, created_at
            FROM test_messages
            WHERE receiver = ? AND direction = 'received'
            ORDER BY created_at DESC
            LIMIT 50
        '''
        results = db_manager_test.execute_query(query, (receiver,), fetch=True, sync=False)
        
        # Форматируем сообщения для фронтенда
        messages = []
        if results:
            for row in results:
                messages.append({
                    'id': row['message_id'],
                    'sender': row['sender'],
                    'receiver': row['receiver'],
                    'message': row['message_text'],
                    'received_at': row['created_at']
                })
        
        return jsonify({
            'status': 'success',
            'messages': messages,
            'count': len(messages)
        })
        
    except Exception as e:
        log_to_file(f"Ошибка получения тестовых сообщений: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/test_rest_clear', methods=['POST'])
def test_rest_clear():
    """
    ТЕСТОВЫЙ ENDPOINT: Очистка тестовых сообщений из БД.
    Можно удалить после тестирования без последствий.
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        # Удаляем только полученные сообщения из БД для сервера
        receiver = 'server_1'
        db_manager_test = DatabaseManager(db_path)
        query = "DELETE FROM test_messages WHERE receiver = ? AND direction = 'received'"
        db_manager_test.execute_query(query, (receiver,), sync=False)
        
        log_to_file(f"Очищены тестовые сообщения для {receiver}", level="INFO")
        
        return jsonify({'status': 'success', 'message': 'Тестовые сообщения очищены из БД'})
        
    except Exception as e:
        log_to_file(f"Ошибка очистки тестовых сообщений: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/sync_key_destruction', methods=['POST'])
def sync_key_destruction():
    """API endpoint для синхронизации уничтожения ключа."""
    try:
        data = request.get_json()
        key_id = data.get('key_id')
        
        key_management_module.destroy_key(key_id)
        
        return jsonify({'status': 'success'})
    except Exception as e:
        log_to_file(f"Ошибка синхронизации уничтожения ключа: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/save_remote_server_url', methods=['POST'])
def save_remote_server_url():
    """API endpoint для сохранения IP-адреса удаленного сервера."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        data = request.get_json()
        remote_url = data.get('remote_server_url', '').strip()
        
        if not remote_url:
            return jsonify({'status': 'error', 'message': 'IP-адрес не может быть пустым'}), 400
        
        # Валидация URL
        if not remote_url.startswith('http://') and not remote_url.startswith('https://'):
            remote_url = 'http://' + remote_url
        
        # Сохраняем в БД
        from utils.timezone_utils import moscow_datetime_sql
        query = f'''
            INSERT OR REPLACE INTO settings (setting_key, setting_value, updated_at)
            VALUES ('remote_server_url', ?, {moscow_datetime_sql()})
        '''
        db_manager.execute_query(query, (remote_url,), sync=False)
        
        # Обновляем key_postprocessing_module с новым URL
        global key_postprocessing_module
        key_postprocessing_module = KeyPostprocessingModule(db_path, remote_server_url=remote_url)
        
        log_to_file(f"Сохранен IP удаленного сервера: {remote_url}", level="INFO")
        
        return jsonify({
            'status': 'success',
            'message': 'IP-адрес удаленного сервера успешно сохранен',
            'remote_server_url': remote_url
        })
    except Exception as e:
        log_to_file(f"Ошибка сохранения IP удаленного сервера: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/get_remote_server_url', methods=['GET'])
def api_get_remote_server_url():
    """API endpoint для получения текущего IP-адреса удаленного сервера."""
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    try:
        remote_url = get_remote_server_url()
        return jsonify({
            'status': 'success',
            'remote_server_url': remote_url
        })
    except Exception as e:
        log_to_file(f"Ошибка получения IP удаленного сервера: {e}", level="ERROR")
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    print(f"Запуск сервера А на {SERVER_A_HOST}:{SERVER_A_PORT}")
    print(f"Удалённый сервер Б: {REMOTE_SERVER_B}")
    print(f"Сервер запущен на {SERVER_A_HOST}:{SERVER_A_PORT}")
    print(f"Удалённый сервер Б: {REMOTE_SERVER_B}")
    print("Режим: REST API (HTTP)")
    
    # Инициализируем WebSocket клиент для синхронизации с сервером 2
    try:
        init_qkd_socket_client()
    except Exception as e:
        log_to_file(f"Не удалось инициализировать WebSocket клиент: {e}", level="WARNING")
        print(f"Предупреждение: WebSocket клиент не инициализирован: {e}")
    
    # Автоматически запускаем чтение данных с QKD устройства
    # ВАЖНО: В debug режиме Flask перезагружает модуль, поэтому функция может вызваться дважды
    # Используем флаг для предотвращения двойного вызова
    if not hasattr(app, 'qkd_started'):
        start_qkd_automatically()
        app.qkd_started = True
    else:
        log_to_file("[AUTO-START] QKD уже запущен, пропускаем повторный запуск", level="INFO")
        print("[AUTO-START] QKD уже запущен, пропускаем повторный запуск")
    
    socketio.run(app, host=SERVER_A_HOST, port=SERVER_A_PORT, debug=True, allow_unsafe_werkzeug=True)
