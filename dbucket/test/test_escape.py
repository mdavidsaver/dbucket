
import unittest
from ..escape import escape_match

class TestEscape(unittest.TestCase):
    data = [
        (r"hello",         r"'hello'"),
        (r"hello world",   r"'hello world'"),
        (r"hell'o w'orld", r"'hell'\''o w'\''orld'"),
    ]
    def test_escape(self):
        for raw, esc in self.data:
            self.assertEqual(escape_match(raw), esc)
