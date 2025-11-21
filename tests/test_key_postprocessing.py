import unittest
from modules.key_postprocessing import compare_sequences, sift_keys

class TestKeyPostprocessing(unittest.TestCase):
    def test_compare_sequences(self):
        sequence1 = "01010101"
        sequence2 = "01110101"
        error_rate = compare_sequences(sequence1, sequence2)
        self.assertEqual(error_rate, 25.0)

    def test_sift_keys(self):
        sequence1 = "01010101"
        sequence2 = "01010101"
        with self.assertRaises(ValueError):
            sift_keys(sequence1, sequence2, max_error_rate=0)

if __name__ == "__main__":
    unittest.main()
