import ssl
import socket
from typing import Optional

class SecureNetworkInterface:
    def __init__(self, host: str, port: int, certfile: str, keyfile: str):
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)

    def start_server(self) -> None:
        """Запускает сервер для обработки сетевых запросов."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0) as sock:
            sock.bind((self.host, self.port))
            sock.listen(5)

            with self.context.wrap_socket(sock, server_side=True) as secure_sock:
                print(f"Сервер запущен на {self.host}:{self.port}")
                while True:
                    conn, addr = secure_sock.accept()
                    print(f"Подключено: {addr}")
                    self.handle_connection(conn)

    def handle_connection(self, conn: socket.socket) -> None:
        """Обрабатывает соединение с клиентом."""
        data = conn.recv(1024)
        if not data:
            return
        print(f"Получено: {data.decode()}")
        conn.sendall(b"ACK")
