import importlib.util
import unittest
from ukrainian_numbers import expand_numbers

class NumberTests(unittest.TestCase):
    def test_disabled_is_unchanged(self):
        self.assertEqual(expand_numbers('Маю 123 і 12,05', False), 'Маю 123 і 12,05')

    @unittest.skipUnless(importlib.util.find_spec('num2words'), 'Optional StyleTTS2 dependency')
    def test_ukrainian_numbers(self):
        self.assertEqual(expand_numbers('Маю 42.'), 'Маю сорок два.')
        self.assertEqual(expand_numbers('1 234'), 'одна тисяча двісті тридцять чотири')
        self.assertEqual(expand_numbers('-12,05'), "мінус дванадцять кома нуль п'ять")
        self.assertEqual(expand_numbers('007'), 'нуль нуль сім')
        self.assertEqual(expand_numbers('user42'), 'user сорок два')
        self.assertFalse(any(c.isdigit() for c in expand_numbers('9'*100)))
