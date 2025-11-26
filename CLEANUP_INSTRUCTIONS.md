# Инструкция по очистке старых файлов БД

## Проблема
В предыдущей версии системы `DatabaseManager` автоматически создавал файлы БД другого абонента в директории каждого сервера:
- В `server_user_1/databases/` создавался `user_2_db.db`
- В `server_user_2/databases/` создавался `user_1_db.db`

Эти файлы больше не нужны, так как синхронизация теперь происходит через REST API.

## Решение

### Удалить ненужные файлы БД:

**Для Windows (PowerShell):**
```powershell
# Удалить user_2_db.db из директории server_user_1
Remove-Item -Path "server_user_1\databases\user_2_db.db" -ErrorAction SilentlyContinue

# Удалить user_1_db.db из директории server_user_2
Remove-Item -Path "server_user_2\databases\user_1_db.db" -ErrorAction SilentlyContinue
```

**Для Linux/Mac:**
```bash
# Удалить user_2_db.db из директории server_user_1
rm -f server_user_1/databases/user_2_db.db

# Удалить user_1_db.db из директории server_user_2
rm -f server_user_2/databases/user_1_db.db
```

### Правильная структура после очистки:

```
qkd_encryption_system/
├── server_user_1/
│   └── databases/
│       └── user_1_db.db  ← только эта БД
├── server_user_2/
│   └── databases/
│       └── user_2_db.db  ← только эта БД
└── databases/  ← общая директория (если используется)
    ├── user_1_db.db
    └── user_2_db.db
```

## Что изменилось

1. **Убрана автоматическая синхронизация БД через файловую систему**
2. **Синхронизация тестовых последовательностей теперь через REST API:**
   - Сервер А отправляет данные на сервер Б: `POST http://localhost:5001/api/save_test_sequence`
   - Сервер Б запрашивает данные у сервера А: `GET http://localhost:5000/api/get_test_sequence/<id>`
3. **Каждый абонент работает только со своей БД**

## Проверка

После очистки убедитесь, что:
- В `server_user_1/databases/` есть только `user_1_db.db`
- В `server_user_2/databases/` есть только `user_2_db.db`
- Оба сервера запускаются без ошибок
- Режим тестирования работает корректно

