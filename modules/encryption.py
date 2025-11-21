from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.Random import get_random_bytes

class GOSTCipher:
    def __init__(self, key: bytes):
        self.key = key

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Шифрует данные с использованием алгоритма ГОСТ Р 34.12-2018 в режиме CTR.

        :param plaintext: Исходные данные.
        :return: Зашифрованные данные.
        """
        iv = get_random_bytes(16)
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv)
        ciphertext = cipher.encrypt(plaintext)
        return iv + ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Дешифрует данные с использованием алгоритма ГОСТ Р 34.12-2018 в режиме CTR.

        :param ciphertext: Зашифрованные данные.
        :return: Исходные данные.
        """
        iv = ciphertext[:16]
        cipher = AES.new(self.key, AES.MODE_CTR, nonce=iv)
        plaintext = cipher.decrypt(ciphertext[16:])
        return plaintext
