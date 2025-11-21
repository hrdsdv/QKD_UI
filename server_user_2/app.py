from flask import Flask, render_template, request, jsonify
from modules.key_management import KeyManager
from modules.encryption import GOSTCipher

app = Flask(__name__)
key_manager = KeyManager("databases/user_2_db.db")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/receive", methods=["POST"])
def receive_data():
    encrypted_data = request.data
    # Здесь должна быть логика получения ключа из KeyManager и дешифрования данных
    key_id = 1  # Пример: ID ключа, который используется для дешифрования
    key = key_manager.get_key(key_id)
    cipher = GOSTCipher(key)
    try:
        decrypted_data = cipher.decrypt(encrypted_data)
        return jsonify({"status": "success", "data": decrypted_data.decode()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

if __name__ == "__main__":
    app.run(ssl_context="adhoc", host="0.0.0.0", port=5001)
