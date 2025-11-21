from flask import Flask, render_template
from modules.qkd_interaction import QKDModule
from modules.key_postprocessing import KeyPostprocessingModule
from modules.key_recovery import KeyRecoveryModule
from modules.key_management import KeyManagementModule

app = Flask(__name__)

# Инициализация модулей
qkd_module = QKDModule()
key_postprocessing_module = KeyPostprocessingModule()
key_recovery_module = KeyRecoveryModule()
key_management_module = KeyManagementModule()

@app.route('/')
def index():
    # Получаем доступные ключи из модуля восстановления ключа
    available_keys = key_recovery_module.get_available_keys()
    system_logs = key_management_module.get_system_logs()
    qber_value = None  # На этапе получения сырых данных QBER не определен

    # Пример данных для вкладки "Входящие"
    incoming_items = [
        {
            "id": 1,
            "sender": "Пользователь B",
            "timestamp": "21.11.2025 12:30:45",
            "type": "text",
            "size": 120,
            "decrypted": None
        },
        {
            "id": 2,
            "sender": "Пользователь C",
            "timestamp": "21.11.2025 12:25:30",
            "type": "file",
            "filename": "document.pdf",
            "filetype": "PDF",
            "size": "245 KB",
            "decrypted": None
        }
    ]

    return render_template(
        'index.html',
        available_keys=available_keys,
        system_logs=system_logs,
        qber_value=qber_value,
        incoming_items=incoming_items
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
