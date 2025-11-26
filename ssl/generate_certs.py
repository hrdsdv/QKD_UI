"""
Скрипт для генерации самоподписанных SSL-сертификатов для TLS.

Генерирует:
1. Корневой CA (Certificate Authority)
2. Сертификат для сервера A (server_user_1)
3. Сертификат для сервера B (server_user_2)

Использование:
    python ssl/generate_certs.py
"""

import os
import datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def generate_private_key():
    """Генерирует RSA приватный ключ (2048 бит)."""
    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )


def generate_ca_certificate(ca_key):
    """Генерирует корневой CA сертификат."""
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QKD System CA"),
        x509.NameAttribute(NameOID.COMMON_NAME, "QKD Root CA"),
    ])
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        ca_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=3650)  # 10 лет
    ).add_extension(
        x509.BasicConstraints(ca=True, path_length=0),
        critical=True,
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=False,
            content_commitment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False
        ),
        critical=True,
    ).sign(ca_key, hashes.SHA256(), default_backend())
    
    return cert


def generate_server_certificate(ca_key, ca_cert, server_key, server_name, common_name, san_list):
    """
    Генерирует серверный сертификат, подписанный CA.
    
    :param ca_key: Приватный ключ CA
    :param ca_cert: Сертификат CA
    :param server_key: Приватный ключ сервера
    :param server_name: Название сервера (для организации)
    :param common_name: Common Name для сертификата
    :param san_list: Список Subject Alternative Names (DNS и IP)
    """
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Moscow"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "Moscow"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, f"QKD System - {server_name}"),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    
    # Формируем SAN (Subject Alternative Name)
    san_entries = []
    for san in san_list:
        if san.replace('.', '').isdigit() or ':' in san:
            # IP-адрес
            import ipaddress
            san_entries.append(x509.IPAddress(ipaddress.ip_address(san)))
        else:
            # DNS имя
            san_entries.append(x509.DNSName(san))
    
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.subject
    ).public_key(
        server_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=365)  # 1 год
    ).add_extension(
        x509.SubjectAlternativeName(san_entries),
        critical=False,
    ).add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    ).add_extension(
        x509.KeyUsage(
            digital_signature=True,
            key_encipherment=True,
            content_commitment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False
        ),
        critical=True,
    ).add_extension(
        x509.ExtendedKeyUsage([
            x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
            x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
        ]),
        critical=False,
    ).sign(ca_key, hashes.SHA256(), default_backend())
    
    return cert


def save_private_key(key, filename, password=None):
    """Сохраняет приватный ключ в файл."""
    encryption = serialization.NoEncryption()
    if password:
        encryption = serialization.BestAvailableEncryption(password.encode())
    
    with open(filename, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=encryption
        ))
    print(f"Сохранён приватный ключ: {filename}")


def save_certificate(cert, filename):
    """Сохраняет сертификат в файл."""
    with open(filename, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Сохранён сертификат: {filename}")


def main():
    """Основная функция генерации сертификатов."""
    ssl_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("=" * 60)
    print("Генерация SSL-сертификатов для QKD системы")
    print("=" * 60)
    
    # 1. Генерируем CA
    print("\n1. Генерация корневого CA...")
    ca_key = generate_private_key()
    ca_cert = generate_ca_certificate(ca_key)
    
    save_private_key(ca_key, os.path.join(ssl_dir, "ca.key"))
    save_certificate(ca_cert, os.path.join(ssl_dir, "ca.crt"))
    
    # 2. Генерируем сертификат для сервера A
    print("\n2. Генерация сертификата для сервера A (Абонент А)...")
    server_a_key = generate_private_key()
    server_a_cert = generate_server_certificate(
        ca_key, ca_cert, server_a_key,
        server_name="Server A",
        common_name="server-a.qkd.local",
        san_list=["localhost", "127.0.0.1", "0.0.0.0", "server-a.qkd.local"]
    )
    
    save_private_key(server_a_key, os.path.join(ssl_dir, "server_a.key"))
    save_certificate(server_a_cert, os.path.join(ssl_dir, "server_a.crt"))
    
    # 3. Генерируем сертификат для сервера B
    print("\n3. Генерация сертификата для сервера B (Абонент Б)...")
    server_b_key = generate_private_key()
    server_b_cert = generate_server_certificate(
        ca_key, ca_cert, server_b_key,
        server_name="Server B",
        common_name="server-b.qkd.local",
        san_list=["localhost", "127.0.0.1", "0.0.0.0", "server-b.qkd.local"]
    )
    
    save_private_key(server_b_key, os.path.join(ssl_dir, "server_b.key"))
    save_certificate(server_b_cert, os.path.join(ssl_dir, "server_b.crt"))
    
    print("\n" + "=" * 60)
    print("Генерация завершена!")
    print("=" * 60)
    print(f"\nФайлы сохранены в директории: {ssl_dir}")
    print("\nСписок файлов:")
    print("  - ca.key, ca.crt         : Корневой CA")
    print("  - server_a.key, server_a.crt : Сервер A (порт 5000)")
    print("  - server_b.key, server_b.crt : Сервер B (порт 5001)")
    print("\nДля использования в production:")
    print("  1. Замените самоподписанные сертификаты на сертификаты от доверенного CA")
    print("  2. Добавьте ca.crt в доверенные корневые сертификаты системы")
    print("  3. Обновите SAN в сертификатах для реальных IP-адресов серверов")


if __name__ == "__main__":
    main()

