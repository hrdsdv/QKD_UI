import unittest
from modules.key_recovery import encode_reed_solomon, decode_reed_solomon

class TestKeyRecovery(unittest.TestCase):
    def test_encode_decode(self):
        data = b"test data"
        encoded_data = encode_reed_solomon(data)
        decoded_data = decode_reed_solomon(encoded_data)
        self.assertEqual(decoded_data, data)

if __name__ == "__main__":
    unittest.main()
