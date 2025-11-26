"""
Модуль шифрования/дешифрования по ГОСТ Р 34.12-2018 (Кузнечик) в режиме CTR
и хэширования по ГОСТ Р 34.11-2018 (Стрибог)

Реализация на чистом Python для совместимости.
"""

import os
import struct
import hashlib
from typing import Tuple
from utils.logging_utils import log_to_file


# ============================================================================
# ГОСТ Р 34.12-2018 "Кузнечик" (Kuznechik) - Реализация
# ============================================================================

# S-box (таблица замен) для Кузнечика
KUZNECHIK_SBOX = bytes([
    0xFC, 0xEE, 0xDD, 0x11, 0xCF, 0x6E, 0x31, 0x16, 0xFB, 0xC4, 0xFA, 0xDA, 0x23, 0xC5, 0x04, 0x4D,
    0xE9, 0x77, 0xF0, 0xDB, 0x93, 0x2E, 0x99, 0xBA, 0x17, 0x36, 0xF1, 0xBB, 0x14, 0xCD, 0x5F, 0xC1,
    0xF9, 0x18, 0x65, 0x5A, 0xE2, 0x5C, 0xEF, 0x21, 0x81, 0x1C, 0x3C, 0x42, 0x8B, 0x01, 0x8E, 0x4F,
    0x05, 0x84, 0x02, 0xAE, 0xE3, 0x6A, 0x8F, 0xA0, 0x06, 0x0B, 0xED, 0x98, 0x7F, 0xD4, 0xD3, 0x1F,
    0xEB, 0x34, 0x2C, 0x51, 0xEA, 0xC8, 0x48, 0xAB, 0xF2, 0x2A, 0x68, 0xA2, 0xFD, 0x3A, 0xCE, 0xCC,
    0xB5, 0x70, 0x0E, 0x56, 0x08, 0x0C, 0x76, 0x12, 0xBF, 0x72, 0x13, 0x47, 0x9C, 0xB7, 0x5D, 0x87,
    0x15, 0xA1, 0x96, 0x29, 0x10, 0x7B, 0x9A, 0xC7, 0xF3, 0x91, 0x78, 0x6F, 0x9D, 0x9E, 0xB2, 0xB1,
    0x32, 0x75, 0x19, 0x3D, 0xFF, 0x35, 0x8A, 0x7E, 0x6D, 0x54, 0xC6, 0x80, 0xC3, 0xBD, 0x0D, 0x57,
    0xDF, 0xF5, 0x24, 0xA9, 0x3E, 0xA8, 0x43, 0xC9, 0xD7, 0x79, 0xD6, 0xF6, 0x7C, 0x22, 0xB9, 0x03,
    0xE0, 0x0F, 0xEC, 0xDE, 0x7A, 0x94, 0xB0, 0xBC, 0xDC, 0xE8, 0x28, 0x50, 0x4E, 0x33, 0x0A, 0x4A,
    0xA7, 0x97, 0x60, 0x73, 0x1E, 0x00, 0x62, 0x44, 0x1A, 0xB8, 0x38, 0x82, 0x64, 0x9F, 0x26, 0x41,
    0xAD, 0x45, 0x46, 0x92, 0x27, 0x5E, 0x55, 0x2F, 0x8C, 0xA3, 0xA5, 0x7D, 0x69, 0xD5, 0x95, 0x3B,
    0x07, 0x58, 0xB3, 0x40, 0x86, 0xAC, 0x1D, 0xF7, 0x30, 0x37, 0x6B, 0xE4, 0x88, 0xD9, 0xE7, 0x89,
    0xE1, 0x1B, 0x83, 0x49, 0x4C, 0x3F, 0xF8, 0xFE, 0x8D, 0x53, 0xAA, 0x90, 0xCA, 0xD8, 0x85, 0x61,
    0x20, 0x71, 0x67, 0xA4, 0x2D, 0x2B, 0x09, 0x5B, 0xCB, 0x9B, 0x25, 0xD0, 0xBE, 0xE5, 0x6C, 0x52,
    0x59, 0xA6, 0x74, 0xD2, 0xE6, 0xF4, 0xB4, 0xC0, 0xD1, 0x66, 0xAF, 0xC2, 0x39, 0x4B, 0x63, 0xB6
])

# Обратный S-box
KUZNECHIK_SBOX_INV = bytes([KUZNECHIK_SBOX.index(i) for i in range(256)])

# Коэффициенты для линейного преобразования
KUZNECHIK_L_VEC = bytes([
    0x94, 0x20, 0x85, 0x10, 0xC2, 0xC0, 0x01, 0xFB,
    0x01, 0xC0, 0xC2, 0x10, 0x85, 0x20, 0x94, 0x01
])


def _gf_mul(a: int, b: int) -> int:
    """Умножение в поле Галуа GF(2^8) с полиномом x^8 + x^7 + x^6 + x + 1"""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a <<= 1
        if a & 0x100:
            a ^= 0x1C3  # Полином x^8 + x^7 + x^6 + x + 1
        b >>= 1
    return result


def _kuznechik_l_step(block: bytes) -> bytes:
    """Один шаг линейного преобразования L"""
    result = 0
    for i in range(16):
        result ^= _gf_mul(block[i], KUZNECHIK_L_VEC[i])
    return bytes([result]) + block[:15]


def _kuznechik_l(block: bytes) -> bytes:
    """Полное линейное преобразование L (16 шагов)"""
    for _ in range(16):
        block = _kuznechik_l_step(block)
    return block


def _kuznechik_l_inv(block: bytes) -> bytes:
    """Обратное линейное преобразование L^-1"""
    for _ in range(16):
        block = block[1:] + bytes([block[0]])
        result = 0
        for i in range(16):
            result ^= _gf_mul(block[i], KUZNECHIK_L_VEC[i])
        block = block[:15] + bytes([result])
    return block


def _kuznechik_s(block: bytes) -> bytes:
    """Нелинейное преобразование S (применение S-box)"""
    return bytes([KUZNECHIK_SBOX[b] for b in block])


def _kuznechik_s_inv(block: bytes) -> bytes:
    """Обратное нелинейное преобразование S^-1"""
    return bytes([KUZNECHIK_SBOX_INV[b] for b in block])


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    """XOR двух байтовых строк"""
    return bytes([x ^ y for x, y in zip(a, b)])


class KuznechikCipher:
    """
    Реализация блочного шифра Кузнечик (ГОСТ Р 34.12-2018).
    Размер блока: 128 бит (16 байт)
    Размер ключа: 256 бит (32 байта)
    """
    
    BLOCK_SIZE = 16
    KEY_SIZE = 32
    ROUNDS = 10
    
    def __init__(self, key: bytes):
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"Длина ключа должна быть {self.KEY_SIZE} байт")
        self._round_keys = self._expand_key(key)
    
    def _expand_key(self, key: bytes) -> list:
        """Развёртывание ключа (Key Schedule)"""
        # Разбиваем ключ на две части
        k1 = key[:16]
        k2 = key[16:]
        
        round_keys = [k1, k2]
        
        # Генерируем итерационные константы
        c = []
        for i in range(1, 33):
            c.append(_kuznechik_l(bytes([i]) + bytes(15)))
        
        # Генерируем раундовые ключи
        for i in range(4):
            for j in range(8):
                idx = 8 * i + j
                temp = _xor_bytes(k1, c[idx])
                temp = _kuznechik_s(temp)
                temp = _kuznechik_l(temp)
                temp = _xor_bytes(temp, k2)
                k2, k1 = k1, temp
            round_keys.extend([k1, k2])
        
        return round_keys
    
    def encrypt_block(self, block: bytes) -> bytes:
        """Шифрование одного блока"""
        if len(block) != self.BLOCK_SIZE:
            raise ValueError(f"Размер блока должен быть {self.BLOCK_SIZE} байт")
        
        # 9 раундов преобразований
        for i in range(9):
            block = _xor_bytes(block, self._round_keys[i])
            block = _kuznechik_s(block)
            block = _kuznechik_l(block)
        
        # Последний раунд (только XOR с ключом)
        block = _xor_bytes(block, self._round_keys[9])
        
        return block
    
    def decrypt_block(self, block: bytes) -> bytes:
        """Дешифрование одного блока"""
        if len(block) != self.BLOCK_SIZE:
            raise ValueError(f"Размер блока должен быть {self.BLOCK_SIZE} байт")
        
        # Первый раунд (только XOR с ключом)
        block = _xor_bytes(block, self._round_keys[9])
        
        # 9 раундов обратных преобразований
        for i in range(8, -1, -1):
            block = _kuznechik_l_inv(block)
            block = _kuznechik_s_inv(block)
            block = _xor_bytes(block, self._round_keys[i])
        
        return block


class GOSTCipher:
    """
    Класс для шифрования/дешифрования данных по ГОСТ Р 34.12-2018 (Кузнечик) в режиме CTR.
    """
    
    BLOCK_SIZE = 16
    KEY_SIZE = 32
    NONCE_SIZE = 8
    
    def __init__(self, key: bytes):
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"Длина ключа должна быть {self.KEY_SIZE} байт (256 бит)")
        self.cipher = KuznechikCipher(key)
    
    def _increment_counter(self, counter: bytes) -> bytes:
        """Инкремент счётчика для режима CTR"""
        counter_int = int.from_bytes(counter, 'big') + 1
        return counter_int.to_bytes(self.BLOCK_SIZE, 'big')
    
    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Шифрует данные в режиме CTR.
        
        :param plaintext: Исходные данные
        :return: nonce (8 байт) + зашифрованные данные
        """
        try:
            nonce = os.urandom(self.NONCE_SIZE)
            counter = nonce + b'\x00' * (self.BLOCK_SIZE - self.NONCE_SIZE)
            
            ciphertext = b''
            for i in range(0, len(plaintext), self.BLOCK_SIZE):
                # Шифруем счётчик
                keystream = self.cipher.encrypt_block(counter)
                
                # XOR с открытым текстом
                block = plaintext[i:i + self.BLOCK_SIZE]
                encrypted_block = _xor_bytes(block, keystream[:len(block)])
                ciphertext += encrypted_block
                
                # Инкрементируем счётчик
                counter = self._increment_counter(counter)
            
            return nonce + ciphertext
            
        except Exception as e:
            log_to_file(f"Ошибка шифрования ГОСТ: {e}", level="ERROR")
            raise
    
    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Дешифрует данные в режиме CTR.
        В CTR режиме шифрование и дешифрование идентичны.
        
        :param ciphertext: nonce (8 байт) + зашифрованные данные
        :return: Расшифрованные данные
        """
        try:
            if len(ciphertext) < self.NONCE_SIZE:
                raise ValueError("Шифротекст слишком короткий")
            
            nonce = ciphertext[:self.NONCE_SIZE]
            encrypted_data = ciphertext[self.NONCE_SIZE:]
            counter = nonce + b'\x00' * (self.BLOCK_SIZE - self.NONCE_SIZE)
            
            plaintext = b''
            for i in range(0, len(encrypted_data), self.BLOCK_SIZE):
                keystream = self.cipher.encrypt_block(counter)
                block = encrypted_data[i:i + self.BLOCK_SIZE]
                decrypted_block = _xor_bytes(block, keystream[:len(block)])
                plaintext += decrypted_block
                counter = self._increment_counter(counter)
            
            return plaintext
            
        except Exception as e:
            log_to_file(f"Ошибка дешифрования ГОСТ: {e}", level="ERROR")
            raise


# ============================================================================
# ГОСТ Р 34.11-2018 "Стрибог" (Streebog) - Реализация
# ============================================================================

# Таблица замен для Стрибога (Pi)
STREEBOG_PI = bytes([
    0xFC, 0xEE, 0xDD, 0x11, 0xCF, 0x6E, 0x31, 0x16, 0xFB, 0xC4, 0xFA, 0xDA, 0x23, 0xC5, 0x04, 0x4D,
    0xE9, 0x77, 0xF0, 0xDB, 0x93, 0x2E, 0x99, 0xBA, 0x17, 0x36, 0xF1, 0xBB, 0x14, 0xCD, 0x5F, 0xC1,
    0xF9, 0x18, 0x65, 0x5A, 0xE2, 0x5C, 0xEF, 0x21, 0x81, 0x1C, 0x3C, 0x42, 0x8B, 0x01, 0x8E, 0x4F,
    0x05, 0x84, 0x02, 0xAE, 0xE3, 0x6A, 0x8F, 0xA0, 0x06, 0x0B, 0xED, 0x98, 0x7F, 0xD4, 0xD3, 0x1F,
    0xEB, 0x34, 0x2C, 0x51, 0xEA, 0xC8, 0x48, 0xAB, 0xF2, 0x2A, 0x68, 0xA2, 0xFD, 0x3A, 0xCE, 0xCC,
    0xB5, 0x70, 0x0E, 0x56, 0x08, 0x0C, 0x76, 0x12, 0xBF, 0x72, 0x13, 0x47, 0x9C, 0xB7, 0x5D, 0x87,
    0x15, 0xA1, 0x96, 0x29, 0x10, 0x7B, 0x9A, 0xC7, 0xF3, 0x91, 0x78, 0x6F, 0x9D, 0x9E, 0xB2, 0xB1,
    0x32, 0x75, 0x19, 0x3D, 0xFF, 0x35, 0x8A, 0x7E, 0x6D, 0x54, 0xC6, 0x80, 0xC3, 0xBD, 0x0D, 0x57,
    0xDF, 0xF5, 0x24, 0xA9, 0x3E, 0xA8, 0x43, 0xC9, 0xD7, 0x79, 0xD6, 0xF6, 0x7C, 0x22, 0xB9, 0x03,
    0xE0, 0x0F, 0xEC, 0xDE, 0x7A, 0x94, 0xB0, 0xBC, 0xDC, 0xE8, 0x28, 0x50, 0x4E, 0x33, 0x0A, 0x4A,
    0xA7, 0x97, 0x60, 0x73, 0x1E, 0x00, 0x62, 0x44, 0x1A, 0xB8, 0x38, 0x82, 0x64, 0x9F, 0x26, 0x41,
    0xAD, 0x45, 0x46, 0x92, 0x27, 0x5E, 0x55, 0x2F, 0x8C, 0xA3, 0xA5, 0x7D, 0x69, 0xD5, 0x95, 0x3B,
    0x07, 0x58, 0xB3, 0x40, 0x86, 0xAC, 0x1D, 0xF7, 0x30, 0x37, 0x6B, 0xE4, 0x88, 0xD9, 0xE7, 0x89,
    0xE1, 0x1B, 0x83, 0x49, 0x4C, 0x3F, 0xF8, 0xFE, 0x8D, 0x53, 0xAA, 0x90, 0xCA, 0xD8, 0x85, 0x61,
    0x20, 0x71, 0x67, 0xA4, 0x2D, 0x2B, 0x09, 0x5B, 0xCB, 0x9B, 0x25, 0xD0, 0xBE, 0xE5, 0x6C, 0x52,
    0x59, 0xA6, 0x74, 0xD2, 0xE6, 0xF4, 0xB4, 0xC0, 0xD1, 0x66, 0xAF, 0xC2, 0x39, 0x4B, 0x63, 0xB6
])


class GOSTHash:
    """
    Класс для хэширования по ГОСТ Р 34.11-2018 (Стрибог).
    
    Для упрощения используем SHA-256/SHA-512 как заглушку с пометкой.
    В production версии следует использовать полную реализацию Стрибога.
    """
    
    @staticmethod
    def hash_256(data: bytes) -> bytes:
        """
        Вычисляет хэш по ГОСТ Р 34.11-2018 (Стрибог-256).
        
        Примечание: Использует SHA-256 как временную замену.
        Для полного соответствия ГОСТ необходима полная реализация Стрибога.
        
        :param data: Данные для хэширования
        :return: Хэш (32 байта = 256 бит)
        """
        try:
            # Используем SHA-256 как совместимую замену
            # В production следует использовать полную реализацию Стрибога
            return hashlib.sha256(data).digest()
        except Exception as e:
            log_to_file(f"Ошибка хэширования: {e}", level="ERROR")
            raise
    
    @staticmethod
    def hash_512(data: bytes) -> bytes:
        """
        Вычисляет хэш по ГОСТ Р 34.11-2018 (Стрибог-512).
        
        :param data: Данные для хэширования
        :return: Хэш (64 байта = 512 бит)
        """
        try:
            return hashlib.sha512(data).digest()
        except Exception as e:
            log_to_file(f"Ошибка хэширования: {e}", level="ERROR")
            raise
    
    @staticmethod
    def verify(data: bytes, expected_hash: bytes, digest_size: int = 32) -> bool:
        """
        Проверяет соответствие данных хэшу.
        
        :param data: Данные для проверки
        :param expected_hash: Ожидаемый хэш
        :param digest_size: Размер хэша (32 или 64 байта)
        :return: True если хэши совпадают
        """
        try:
            if digest_size == 32:
                actual_hash = GOSTHash.hash_256(data)
            else:
                actual_hash = GOSTHash.hash_512(data)
            return actual_hash == expected_hash
        except Exception as e:
            log_to_file(f"Ошибка проверки хэша: {e}", level="ERROR")
            return False
    
    @staticmethod
    def hash_hex(data: bytes, digest_size: int = 32) -> str:
        """
        Вычисляет хэш и возвращает его в hex-формате.
        
        :param data: Данные для хэширования
        :param digest_size: Размер хэша (32 или 64 байта)
        :return: Хэш в hex-формате
        """
        if digest_size == 32:
            return GOSTHash.hash_256(data).hex()
        else:
            return GOSTHash.hash_512(data).hex()


# ============================================================================
# AES для обратной совместимости и шифрования хранилища
# ============================================================================

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class AESCipher:
    """
    Класс для AES шифрования (используется для шифрования хранилища ключей).
    AES-256-GCM для защищённого хранения ключей в БД.
    """
    
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("Длина ключа AES должна быть 32 байта (256 бит)")
        self.key = key
    
    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Шифрует данные с использованием AES-256-GCM.
        
        :param plaintext: Исходные данные
        :return: nonce (16 байт) + tag (16 байт) + зашифрованные данные
        """
        cipher = AES.new(self.key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        return cipher.nonce + tag + ciphertext
    
    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Дешифрует данные с использованием AES-256-GCM.
        
        :param ciphertext: nonce (16 байт) + tag (16 байт) + зашифрованные данные
        :return: Расшифрованные данные
        """
        nonce = ciphertext[:16]
        tag = ciphertext[16:32]
        encrypted_data = ciphertext[32:]
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(encrypted_data, tag)
