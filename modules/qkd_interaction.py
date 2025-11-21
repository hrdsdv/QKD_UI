import serial
import random

def get_qkd_sequences(port: str, baudrate: int = 9600) -> tuple:
    """
    Получает две битовые последовательности от устройства QKD через UART.

    :param port: COM-порт, к которому подключено устройство QKD.
    :param baudrate: Скорость передачи данных.
    :return: Кортеж из двух битовых строк.
    """
    try:
        with serial.Serial(port, baudrate, timeout=1) as ser:
            sequence1 = ser.readline().strip().decode()
            sequence2 = ser.readline().strip().decode()
            return sequence1, sequence2
    except Exception as e:
        print(f"Ошибка при чтении данных с QKD: {e}")
        return "", ""

def introduce_errors(sequence1: str, sequence2: str, error_rate: float = 0.07) -> tuple:
    """
    Вносит расхождения в битовые последовательности, если они идентичны.

    :param sequence1: Первая битовая строка.
    :param sequence2: Вторая битовая строка.
    :param error_rate: Процент расхождения (по умолчанию 7%).
    :return: Кортеж из двух битовых строк с расхождениями.
    """
    if sequence1 == sequence2:
        sequence_list = list(sequence2)
        num_errors = int(len(sequence_list) * error_rate)
        for i in random.sample(range(len(sequence_list)), num_errors):
            sequence_list[i] = '1' if sequence_list[i] == '0' else '0'
        return sequence1, ''.join(sequence_list)
    return sequence1, sequence2


class QKDModule:
    def __init__(self):
        self.current_status = "Получение сырых ключевых данных"
        self.raw_key_sequence1 = "0101101010110101"
        self.raw_key_sequence2 = "0101101001110101"

    def get_current_status(self):
        """Возвращает текущий статус модуля QKD."""
        return self.current_status

    def get_raw_sequences(self):
        """Возвращает сырые битовые последовательности."""
        return self.raw_key_sequence1, self.raw_key_sequence2