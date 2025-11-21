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


class KeyRecoveryModule:
    def __init__(self):
        # Пример доступных ключей, которые прошли восстановление
        self.available_keys = [
            {"key_id": "QKD-2024-A1B2", "generated": "21.11.2025 10:30:15", "status": "Активен"},
            {"key_id": "QKD-2024-C3D4", "generated": "21.11.2025 09:15:42", "status": "Активен"},
        ]

    def get_available_keys(self):
        """Возвращает список доступных ключей, которые прошли восстановление."""
        return self.available_keys