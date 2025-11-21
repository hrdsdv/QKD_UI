import requests

class NetworkExchange:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def send_encrypted_data(self, data: bytes) -> bool:
        """
        Отправляет зашифрованные данные на сервер получателя.

        :param data: Зашифрованные данные.
        :return: Статус успешности отправки.
        """
        try:
            response = requests.post(f"{self.base_url}/receive", data=data)
            return response.status_code == 200
        except Exception as e:
            print(f"Ошибка при отправке данных: {e}")
            return False
