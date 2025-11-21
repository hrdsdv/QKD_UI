import reedsolo

def encode_reed_solomon(data: bytes, ecc_length: int = 10) -> bytes:
    """
    Кодирует данные с использованием алгоритма Рида-Соломона.

    :param data: Исходные данные.
    :param ecc_length: Длина избыточной информации.
    :return: Закодированные данные.
    """
    rsc = reedsolo.RSCodec(ecc_length)
    return rsc.encode(data)

def decode_reed_solomon(encoded_data: bytes) -> bytes:
    """
    Декодирует данные с использованием алгоритма Рида-Соломона.

    :param encoded_data: Закодированные данные.
    :return: Декодированные данные.
    """
    rsc = reedsolo.RSCodec(10)
    try:
        return rsc.decode(encoded_data)[0]
    except reedsolo.ReedSolomonError as e:
        print(f"Ошибка декодирования: {e}")
        return b""

from .database_utils import DatabaseManager

class KeyRecoveryModule:
    def __init__(self, db_path: str):
        self.db_manager = DatabaseManager(db_path)

    def get_available_keys(self) -> list:
        """Возвращает список доступных ключей, которые прошли восстановление."""
        return self.db_manager.get_available_keys()

    def add_key(self, key_id: str, status: str, length: int) -> None:
        """Добавляет новый ключ в базу данных."""
        self.db_manager.add_key(key_id, status, length)

    def update_key_status(self, key_id: str, status: str, used_by: int) -> None:
        """Обновляет статус ключа."""
        self.db_manager.update_key_status(key_id, status, used_by)