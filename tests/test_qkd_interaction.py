import unittest
from modules.qkd_interaction import introduce_errors

class TestQKDInteraction(unittest.TestCase):
    def test_introduce_errors(self):
        sequence1 = "01010101"
        sequence2 = "01010101"
        new_sequence1, new_sequence2 = introduce_errors(sequence1, sequence2, error_rate=0.25)
        self.assertNotEqual(new_sequence1, new_sequence2)

if __name__ == "__main__":
    unittest.main()
