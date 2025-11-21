def compare_sequences(sequence1: str, sequence2: str) -> float:
    """
    Сравнивает две битовые последовательности и возвращает процент расхождения.

    :param sequence1: Первая битовая строка.
    :param sequence2: Вторая битовая строка.
    :return: Процент расхождения.
    """
    if len(sequence1) != len(sequence2):
        raise ValueError("Длины последовательностей не совпадают.")

    mismatch = sum(c1 != c2 for c1, c2 in zip(sequence1, sequence2))
    return (mismatch / len(sequence1)) * 100

def sift_keys(sequence1: str, sequence2: str, max_error_rate: float = 11.0) -> tuple:
    """
    Формирует просеянные ключи на основе сравнения битовых последовательностей.

    :param sequence1: Первая битовая строка.
    :param sequence2: Вторая битовая строка.
    :param max_error_rate: Максимально допустимый процент расхождения.
    :return: Кортеж из двух просеянных битовых строк.
    """
    error_rate = compare_sequences(sequence1, sequence2)
    if error_rate > max_error_rate:
        raise ValueError(f"Процент расхождения {error_rate}% превышает допустимый.")

    return sequence1, sequence2


class KeyPostprocessingModule:
    def __init__(self):
        self.current_qber = 2.1  # Пример значения QBER

    def get_current_qber(self):
        """Возвращает текущее значение QBER."""
        return self.current_qber

    def calculate_qber(self, sequence1, sequence2):
        """Вычисляет QBER между двумя битовыми последовательностями."""
        if len(sequence1) != len(sequence2):
            raise ValueError("Длины последовательностей не совпадают.")

        mismatch = sum(c1 != c2 for c1, c2 in zip(sequence1, sequence2))
        return (mismatch / len(sequence1)) * 100
