import datetime
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
        SERVER_B_HOST, SERVER_B_PORT, REMOTE_SERVER_A,
        FLASK_SECRET_KEY
    )
except ImportError:
    # Fallback значения если config.py не найден
    SERVER_B_HOST = '0.0.0.0'
    SERVER_B_PORT = 5001
    # ВАЖНО: Замените localhost на реальный IP-адрес ПК1 для работы между разными ПК!
    # Узнать IP ПК1 можно командой: ipconfig (Windows) или ip addr (Linux)
    REMOTE_SERVER_A = 'http://127.0.0.1:5000'  # ⚠️ ЗАМЕНИТЕ на IP ПК1!
    FLASK_SECRET_KEY = 'dev-secret-key'

import threading
import time
import random
import requests
from datetime import datetime
from utils.timezone_utils import moscow_now, moscow_now_str, moscow_datetime_sql

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

db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'databases', 'user_2_db.db'))
print(f"Путь к базе данных: {db_path}")
# Инициализация базы данных
db_path = os.path.abspath(os.path.join('databases', 'user_2_db.db'))
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
            ('remote_server_url', REMOTE_SERVER_A), sync=False
        )
except Exception as e:
    log_to_file(f"Ошибка создания таблицы settings: {e}", level="WARNING")

# Инициализация модулей
qkd_module = QKDModule(db_path)
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
    return REMOTE_SERVER_A

# Инициализируем key_postprocessing_module с динамическим URL
key_postprocessing_module = KeyPostprocessingModule(db_path, remote_server_url=get_remote_server_url())

def start_qkd_automatically():
    """
    Автоматически запускает чтение данных с QKD устройства для второго абонента.
    Подключается к COM-порту и начинает генерацию последовательностей.
    """
    try:
        log_to_file("[AUTO-START] ========== АВТОМАТИЧЕСКИЙ ЗАПУСК QKD (Абонент Б) ==========", level="INFO")
        print("[AUTO-START] ========== АВТОМАТИЧЕСКИЙ ЗАПУСК QKD (Абонент Б) ==========")
        
        # Определяем COM порт для второго абонента
        # По умолчанию COM3, но можно изменить в config.py или здесь
        qkd_port = 'COM3'  # Измените на нужный COM порт для второго абонента
        
        log_to_file(f"[AUTO-START] Попытка автоматического подключения к QKD устройству ({qkd_port}, 9600)...", level="INFO")
        print(f"[AUTO-START] Попытка автоматического подключения к QKD устройству ({qkd_port}, 9600)...")
        
        # Проверяем, существует ли тестовая запись (для проверки БД)
        try:
            test_result = db_manager.execute_query(
                "SELECT sequence_id FROM raw_data WHERE sequence_id = ?",
                ("TEST_AUTO_START",), fetch=True, sync=False
            )
            if test_result and len(test_result) > 0:
                log_to_file("[AUTO-START] ТЕСТ: Тестовая запись уже существует, пропускаем", level="INFO")
                print("[AUTO-START] ТЕСТ: Тестовая запись уже существует, пропускаем")
            else:
                # Создаём тестовую запись для проверки БД
                test_bits = '0' * 256
                test_bases = '+' * 256
                db_manager.execute_query(
                    "INSERT INTO raw_data (sequence_id, bits, bases, station, is_test) VALUES (?, ?, ?, ?, ?)",
                    ("TEST_AUTO_START", test_bits, test_bases, "B", 1), sync=False
                )
                log_to_file("[AUTO-START] ТЕСТ: Тестовая запись создана", level="INFO")
                print("[AUTO-START] ТЕСТ: Тестовая запись создана")
        except Exception as test_error:
            log_to_file(f"[AUTO-START] ТЕСТ: Ошибка при проверке тестовой записи: {test_error}", level="WARNING")
            print(f"[AUTO-START] ТЕСТ: Ошибка при проверке тестовой записи: {test_error}")
        
        # Подключаемся к COM порту с повторными попытками
        log_to_file(f"[AUTO-START] Вызов connect_serial('{qkd_port}', 9600) с повторными попытками...", level="INFO")
        print(f"[AUTO-START] Вызов connect_serial('{qkd_port}', 9600) с повторными попытками...")
        print("[AUTO-START] =========================================")
        print("[AUTO-START] ВАЖНО: Если порт занят другой программой:")
        print(f"[AUTO-START] 1. Закройте программу, использующую {qkd_port}")
        print("[AUTO-START] 2. Подождите 3-5 секунд")
        print("[AUTO-START] 3. Система автоматически повторит попытку подключения")
        print("[AUTO-START] =========================================")
        
        if qkd_module.connect_serial(qkd_port, baudrate=9600, retry_count=5, retry_delay=3):
            log_to_file(f"[AUTO-START] {qkd_port} подключен успешно, запуск генерации...", level="INFO")
            print(f"[AUTO-START] {qkd_port} подключен успешно, запуск генерации...")
            
            # Небольшая задержка перед запуском потока
            time.sleep(0.5)
            
            # Запускаем генерацию в отдельном потоке
            log_to_file("[AUTO-START] Создание потока для start_generation...", level="INFO")
            print("[AUTO-START] Создание потока для start_generation...")
            
            thread = threading.Thread(target=qkd_module.start_generation, daemon=True, name="QKD-Auto-Generation-B")
            thread.start()
            
            # Проверяем, что поток запустился
            time.sleep(0.1)
            log_to_file(f"[AUTO-START] Поток генерации запущен: {thread.name}, alive={thread.is_alive()}, ident={thread.ident}", level="INFO")
            print(f"[AUTO-START] Поток генерации запущен: {thread.name}, alive={thread.is_alive()}, ident={thread.ident}")
            
            if not thread.is_alive():
                log_to_file("[AUTO-START] ВНИМАНИЕ: Поток не запустился или уже завершился!", level="ERROR")
                print("[AUTO-START] ВНИМАНИЕ: Поток не запустился или уже завершился!")
        else:
            log_to_file(f"[AUTO-START] ОШИБКА: Не удалось подключиться к {qkd_port}. Проверьте подключение устройства.", level="ERROR")
            print(f"[AUTO-START] ОШИБКА: Не удалось подключиться к {qkd_port}. Проверьте подключение устройства.")
    except Exception as e:
        error_msg = f"[AUTO-START] КРИТИЧЕСКАЯ ОШИБКА при автоматическом запуске: {e}"
        log_to_file(error_msg, level="ERROR")
        print(error_msg)
        import traceback
        print(traceback.format_exc())

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
        qber_value = None

        incoming_items = key_management_module.get_incoming_messages(session['user_id'])
        print(f"DEBUG: Получено входящих сообщений: {len(incoming_items)}")
        if incoming_items:
            print(f"DEBUG: Первое сообщение: {incoming_items[0]}")

        # Получаем последние 32 бита и их базисы
        last_sequence = qkd_module.get_last_sequence()
        last_32_bits = last_sequence['bits'][-32:] if last_sequence and len(last_sequence.get('bits', '')) >= 32 else (last_sequence['bits'] if last_sequence else None)
        last_32_bases = last_sequence['bases'][-32:] if last_sequence and len(last_sequence.get('bases', '')) >= 32 else (last_sequence['bases'] if last_sequence else None)

        # Получаем тестовые сообщения из БД для текущего сервера
        # Фильтруем по имени сервера (server_2) для полученных сообщений
        test_messages = []
        try:
            receiver = 'server_2'  # Имя сервера как получателя
            db_manager_test = DatabaseManager(db_path)
            query = '''
                SELECT message_id, sender, receiver, message_text, created_at
                FROM test_messages
                WHERE receiver = ? AND direction = 'received'
                ORDER BY created_at DESC
                LIMIT 50
            '''
            results = db_manager_test.execute_query(query, (receiver,), fetch=True, sync=False)
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
            last_32_bits=last_32_bits,
            last_32_bases=last_32_bases,
            test_messages=test_messages,  # Тестовые сообщения из БД
            remote_server_a=get_remote_server_url()  # Адрес удалённого сервера А
        )
    except Exception as e:
        print(f"Ошибка при загрузке главной страницы: {e}")
        flash(f'Ошибка при загрузке главной страницы: {e}', 'danger')
        return redirect(url_for('login'))

@app.route('/start_qkd')
def start_qkd():
    if not qkd_module.connect_serial('/dev/ttyUSB1'):  # Измените на ваш COM-порт для второго сервера
        return jsonify({'status': 'error', 'message': 'Не удалось подключиться к QKD-устройству'}), 500
    if not qkd_module.connect_tcp():
        return jsonify({'status': 'error', 'message': 'Не удалось установить TCP-канал'}), 500
    if not qkd_module.synchronize_stations():
        return jsonify({'status': 'error', 'message': 'Не удалось синхронизировать станции'}), 500
    threading.Thread(target=qkd_module.start_generation, daemon=True).start()
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
    if qkd_module.select_sequence(sequence_id, username):
        # Уведомляем первый сервер о выборе последовательности
        import requests
        try:
            requests.post(f'{get_remote_server_url()}/api/sync_sequence_selection', json={
                'sequence_id': sequence_id,
                'selected_by': f'B_{username}',
                'station': 'B'
            }, timeout=2)
        except Exception as e:
            log_to_file(f"Не удалось синхронизировать выбор последовательности: {e}", level="WARNING")
        
        # Используем новый модуль постобработки с реальным сравнением базисов
        comparison_result = key_postprocessing_module.compare_bases(sequence_id)
        
        if 'error' in comparison_result:
            return jsonify({'status': 'error', 'message': comparison_result['error']}), 400
        
        mismatches = comparison_result.get('mismatches', 0)
        qber_value = comparison_result.get('qber', 0)
        needs_regeneration = comparison_result.get('needs_regeneration', False)
        
        # Проверяем порог QBER (11%)
        if needs_regeneration:
            return jsonify({
                'status': 'warning',
                'message': f'QBER ({qber_value}%) превышает порог 11%. Рекомендуется повторная генерация.',
                'mismatches': mismatches,
                'qber': qber_value,
                'needs_regeneration': True
            })
        
        return jsonify({
            'status': 'success', 
            'mismatches': mismatches, 
            'qber': qber_value,
            'sifted_length': comparison_result.get('sifted_length', 0),
            'matching_bases': comparison_result.get('matching_bases', 0),
            'last_32_bits_local': comparison_result.get('last_32_bits_local', ''),
            'last_32_bits_remote': comparison_result.get('last_32_bits_remote', '')
        })
    else:
        return jsonify({'status': 'error', 'message': 'Не удалось выбрать последовательность'}), 500

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
        from utils.logging_utils import log_to_file
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

@app.route('/recover_key', methods=['POST'])
def recover_key():
    """
    Восстановление ключа с использованием алгоритма Рида-Соломона.
    Включает проверку хэша ГОСТ Р 34.11-2018 (Стрибог).
    """
    sequence_id = request.form.get('sequence_id')
    if not sequence_id:
        return jsonify({'status': 'error', 'message': 'Не указан ID последовательности'}), 400

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
        # Синхронизируем восстановленный ключ с сервером А
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
        
        return jsonify({
            'status': 'success', 
            'key_id': key_id,
            'recovery_percentage': recovery_result.get('recovery_percentage', 0),
            'recovery_time_ms': recovery_result.get('recovery_time_ms', 0),
            'hash_verified': recovery_result.get('hash_verified', False),
            'errors_corrected': recovery_result.get('errors_corrected', 0),
            'blocks_info': recovery_result.get('blocks_info', []),
            'blocks_total': recovery_result.get('blocks_total', 0)
        })
    else:
        return jsonify({'status': 'error', 'message': 'Не удалось сохранить восстановленный ключ'}), 500

# WebSocket события для приема данных от сервера 1
# Создаем клиент для подключения к серверу 1
qkd_socket_client = None

def init_qkd_socket_client():
    """Инициализирует WebSocket клиент для подключения к серверу 1"""
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
        
        # Создаем клиент для подключения к серверу 1
        qkd_socket_client = sio_client.Client()
        
        @qkd_socket_client.on('new_sequence')
        def on_new_sequence(data):
            """Обработчик получения новой последовательности от сервера 1"""
            try:
                # Отправляем данные всем подключенным клиентам сервера 2
                socketio.emit('new_sequence', data, namespace='/')
                log_to_file(f"Получена последовательность от сервера 1: {data.get('sequence_id')}", level="INFO")
            except Exception as e:
                log_to_file(f"Ошибка обработки последовательности от сервера 1: {e}", level="ERROR")
        
        @qkd_socket_client.on('connect')
        def on_connect():
            log_to_file("Подключен к серверу 1 через WebSocket", level="INFO")
        
        @qkd_socket_client.on('disconnect')
        def on_disconnect():
            log_to_file("Отключен от сервера 1 через WebSocket", level="WARNING")
        
        # Подключаемся к серверу 1
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
    """Обработчик подключения клиента через WebSocket на сервере 2"""
    log_to_file("WebSocket клиент подключен к серверу 2", level="INFO")
    emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    """Обработчик отключения клиента через WebSocket на сервере 2"""
    log_to_file("WebSocket клиент отключен от сервера 2", level="INFO")

@app.route('/api/sync_recovered_key', methods=['POST'])
def sync_recovered_key():
    """API endpoint для синхронизации восстановленного ключа"""
    from utils.logging_utils import log_to_file
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

@app.route('/api/save_test_sequence', methods=['POST'])
def save_test_sequence():
    """API endpoint для приёма тестовой последовательности от сервера А"""
    try:
        data = request.get_json()
        sequence_id = data.get('sequence_id')
        bits = data.get('bits')
        bases = data.get('bases')
        station = data.get('station', 'B')
        
        db_manager.execute_query(
            "INSERT INTO raw_data (sequence_id, bits, bases, station, selected_by, is_test) VALUES (?, ?, ?, ?, ?, ?)",
            (sequence_id, bits, bases, station, 'Тестовая', True), sync=False
        )
        
        # Отправляем данные через WebSocket для анимации на сервере 2
        try:
            last_32_bits = bits[-32:] if len(bits) >= 32 else bits
            last_32_bases = bases[-32:] if len(bases) >= 32 else bases
            socketio.emit('new_sequence', {
                'sequence_id': sequence_id,
                'last_32_bits': last_32_bits,
                'last_32_bases': last_32_bases,
                'is_test': True,
                'timestamp': moscow_now_str('%Y-%m-%d %H:%M:%S')
            }, namespace='/')
        except Exception as e:
            log_to_file(f"Ошибка отправки тестовой последовательности через WebSocket: {e}", level="WARNING")
        
        return jsonify({'status': 'success', 'message': 'Тестовая последовательность сохранена'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/generate_test_sequence', methods=['POST'])
def generate_test_sequence():
    seq_id_b = request.form.get('seq_id_b')
    if not seq_id_b:
        # fallback: найти последнюю тестовую последовательность у абонента B
        result = db_manager.execute_query("SELECT * FROM raw_data WHERE is_test = TRUE AND station = 'B' ORDER BY generated_at DESC LIMIT 1", fetch=True, sync=False)
        if not result:
            return jsonify({'status': 'error', 'message': 'Нет свежих тестовых последовательностей'}), 400
        rec = result[0]
        seq_id_b = rec['sequence_id']

    # Получить строку bits_b из своей БД
    row_b = db_manager.execute_query("SELECT * FROM raw_data WHERE sequence_id = ?", (seq_id_b,), fetch=True, sync=False)
    if not row_b:
        return jsonify({'status': 'error', 'message': 'Нет тестовой последовательности B'}), 400
    bits_b = row_b[0]['bits']
    timestamp_part = seq_id_b.split('_')[-1] if '_' in seq_id_b else None
    seq_id_a = f'testA_{timestamp_part}' if timestamp_part else None
    
    # Запросить последовательность А через REST API с сервера А
    import requests
    try:
        response = requests.get(f'{get_remote_server_url()}/api/get_test_sequence/{seq_id_a}', timeout=2)
        if response.status_code == 200:
            data_a = response.json()
            bits_a = data_a.get('bits')
        else:
            bits_a = None
    except Exception as e:
        bits_a = None

    # Эмулируем подключение
    conn_time = round(random.uniform(1.1, 3.0), 2)
    time.sleep(conn_time)

    if not bits_a:
        return jsonify({'status': 'error', 'message': 'Нет тестовой последовательности А для сравнения'}), 400

    mismatches = sum([1 for x, y in zip(bits_a, bits_b) if x != y])
    factual_qber = round((mismatches / 256) * 100, 2)
    bases_comparison = f'Выполнена, значение QBER {factual_qber}%'

    # Восстановление с проверкой хэша ГОСТ Р 34.11-2018
    start_recover = time.perf_counter()
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
    correct_blocks = sum(1 for a, b in zip(recover_bits, bits_a) if a == b)
    recovery_percentage = round((correct_blocks / 256) * 100, 2)
    recovery_time = round((end_recover - start_recover) * 1000, 1)

    # Метрика времени шифрования/дешифрования ГОСТ Р 34.12-2018 (Кузнечик)
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

    key_destruction = "Использован и уничтожен"

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
        'last_32_bits_local': last_32_bits_b,  # Для сервера Б локальная последовательность - это bits_b
        'last_32_bits_remote': last_32_bits_a  # Удаленная - это bits_a
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
    receiver_id = request.form.get('receiver_id', 1)  # По умолчанию отправляем абоненту А
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


# Старая версия для совместимости (будет удалена)
@app.route('/encrypt_message_old', methods=['POST'])
def encrypt_message_old():
    """
    Шифрует текстовое сообщение и отправляет его получателю.
    Использует ГОСТ Р 34.12-2018 (Кузнечик) в режиме CTR.
    """
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Не авторизован'}), 401
    
    message_text = request.form.get('message')
    key_id = request.form.get('key_id')
    receiver_id = request.form.get('receiver_id', 1)  # По умолчанию отправляем абоненту А
    
    if not message_text:
        return jsonify({'status': 'error', 'message': 'Сообщение не может быть пустым'}), 400
    if not key_id:
        return jsonify({'status': 'error', 'message': 'Не выбран ключ для шифрования'}), 400
    
    try:
        receiver_id = int(receiver_id)
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Некорректный ID получателя'}), 400
    
    # Шифруем сообщение
    result = message_crypto.encrypt_message(
        plaintext=message_text,
        key_id=key_id,
        sender_id=session['user_id'],
        receiver_id=receiver_id
    )
    
    if result['status'] == 'success':
        # Отправляем зашифрованное сообщение на сервер получателя
        try:
            response = requests.post(
                f'{get_remote_server_url()}/api/receive_encrypted_message',
                json={
                    'message_id': result['message_id'],
                    'sender_id': session['user_id'],
                    'sender_name': session.get('username', 'Unknown'),
                    'receiver_id': receiver_id,
                    'key_id': key_id,
                    'ciphertext': result['ciphertext'],
                    'hash': result['hash'],
                    'message_type': 'text'
                },
                timeout=5
            )
            if response.status_code == 200:
                result['sent_to_receiver'] = True
            else:
                result['sent_to_receiver'] = False
                result['send_error'] = response.text
        except Exception as e:
            log_to_file(f"Ошибка отправки сообщения: {e}", level="WARNING")
            result['sent_to_receiver'] = False
            result['send_error'] = str(e)
    
    return jsonify(result)


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
        return jsonify({'status': 'error', 'message': 'Сообщение не найдено'}), 404
    
    # Проверяем, что сообщение адресовано текущему пользователю
    if message['receiver_id'] != session['user_id']:
        return jsonify({'status': 'error', 'message': 'Нет доступа к этому сообщению'}), 403
    
    message_type = message.get('message_type', 'text')
    result = {'status': 'success', 'sender_name': message.get('sender_name', 'Unknown')}
    
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
            db_manager.execute_query(
                query,
                (sender_id, sender_name, rec_id, key_id, message_type, ciphertext,
                 content_hash, file_ciphertext, file_name, file_size),
                sync=False
            )
            # Проверяем, что сообщение действительно сохранено
            check_query = "SELECT message_id, receiver_id, is_encrypted FROM messages WHERE receiver_id = ? AND sender_id = ? AND key_id = ? ORDER BY sent_at DESC LIMIT 1"
            check_result = db_manager.execute_query(check_query, (rec_id, sender_id, key_id), fetch=True, sync=False)
            if check_result:
                log_to_file(f"DEBUG: Проверка сохранения: message_id={check_result[0].get('message_id')}, receiver_id={check_result[0].get('receiver_id')}, is_encrypted={check_result[0].get('is_encrypted')}", level="INFO")
            else:
                log_to_file(f"DEBUG: ОШИБКА: Сообщение НЕ найдено в БД после сохранения для receiver_id={rec_id}", level="ERROR")
        
        log_to_file(f"Сохранено сообщение от {sender_name} (sender_id={sender_id}) для receiver_ids={receiver_ids_to_save}, is_encrypted=1", level="INFO")
        print(f"DEBUG: Сохранено сообщение от {sender_name} для receiver_ids={receiver_ids_to_save}")
        
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
        log_to_file(f"DEBUG: api_get_incoming_messages: user_id={user_id}, username={username}", level="INFO")
        incoming_items = key_management_module.get_incoming_messages(user_id)
        log_to_file(f"DEBUG: api_get_incoming_messages: найдено сообщений: {len(incoming_items)}", level="INFO")
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
        sent_items = key_management_module.get_sent_messages(session['user_id'])
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
        
        return jsonify({
            'status': 'success',
            'tables': {
                'users': users,
                'raw_data': raw_data,
                'keys': keys,
                'messages': messages,
                'logs': logs,
                'sessions': sessions_data
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
        start_time = time.perf_counter()
        
        sender = session.get('username', 'Unknown')  # Реальное имя отправителя
        
        # Отправляем тестовое сообщение на сервер А через REST API
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
            receiver = response_data.get('receiver', 'server_1')  # Получаем имя получателя из ответа
            
            # Сохраняем отправленное сообщение в локальную БД (сервер 2)
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
        
        # Определяем получателя - это имя сервера (server_2)
        receiver = 'server_2'
        
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
        # Фильтруем по имени сервера (server_2) для полученных сообщений
        receiver = 'server_2'
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
        
        log_to_file(f"DEBUG: Найдено сообщений в БД: {len(results) if results else 0}", level="INFO")
        
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
        
        log_to_file(f"DEBUG: Возвращено сообщений: {len(messages)}", level="INFO")
        
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
        receiver = 'server_2'
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
    print(f"Запуск сервера Б на {SERVER_B_HOST}:{SERVER_B_PORT}")
    print(f"Удалённый сервер А: {get_remote_server_url()}")
    print(f"Сервер запущен на {SERVER_B_HOST}:{SERVER_B_PORT}")
    print(f"Удалённый сервер А: {get_remote_server_url()}")
    print("Режим: REST API (HTTP) + WebSocket")
    
    # Инициализируем WebSocket клиент для подключения к серверу 1
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
    
    socketio.run(app, host=SERVER_B_HOST, port=SERVER_B_PORT, debug=True, allow_unsafe_werkzeug=True)
