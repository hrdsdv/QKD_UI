from flask import Flask, render_template, request, redirect, url_for, session, flash
from modules.qkd_interaction import QKDModule
from modules.key_postprocessing import KeyPostprocessingModule
from modules.key_recovery import KeyRecoveryModule
from modules.key_management import KeyManagementModule
from modules.database_utils import DatabaseManager
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Инициализация базы данных
db_path = os.path.abspath(os.path.join('databases', 'user_2_db.db'))
db_manager = DatabaseManager(db_path)

# Инициализация модулей
qkd_module = QKDModule()
key_postprocessing_module = KeyPostprocessingModule()
key_recovery_module = KeyRecoveryModule(db_path)
key_management_module = KeyManagementModule(db_path)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = db_manager.get_user_by_username(username)
        if user and user['password'] == password:
            session['user_id'] = user['user_id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Вы успешно вошли в систему!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверное имя пользователя или пароль', 'danger')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
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

    # Получаем данные из модулей
    available_keys = key_recovery_module.get_available_keys()
    system_logs = key_management_module.get_system_logs()
    qber_value = None  # На этапе получения сырых данных QBER не определен

    # Получаем входящие сообщения
    incoming_items = key_management_module.get_incoming_messages(session['user_id'])

    return render_template(
        'index.html',
        available_keys=available_keys,
        system_logs=system_logs,
        qber_value=qber_value,
        incoming_items=incoming_items
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
