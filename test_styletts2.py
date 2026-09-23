import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from styletts2_backend import StyleWorker, CATALOG
from styletts2_worker import text_chunks


class StyleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'styletts2_worker.py').write_text('''import sys, json, wave, time
from pathlib import Path
for line in sys.stdin:
 r = json.loads(line)
 if r['text'] == 'wait': time.sleep(20)
 if r['text'] == 'error':
  result = {'error': 'test synthesis error'}
 else:
  with wave.open(r['output'], 'wb') as w:
   w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\\0\\0' * 2400)
  result = {'ok': True}
 Path(r['reply']).write_text(json.dumps(result))
''')
        self.worker = StyleWorker()
        self.settings = dict(style_voice=CATALOG['voices'][0], style_device='CPU', speed=1)
        with patch('styletts2_backend.BASE', self.root):
            self.process = self.worker.start(sys.executable, 'CPU')

    def tearDown(self):
        self.worker.close()
        self.temp.cleanup()

    def generate(self, text, name='a', cancelled=lambda: False, timeout=3):
        return self.worker.generate(text, self.settings, str(self.root / (name + '.wav')), cancelled, timeout)

    def test_reuses_worker_and_produces_pcm(self):
        self.assertTrue(self.generate('Привіт'))
        self.assertTrue(self.generate('Другий текст', 'b'))
        self.assertIs(self.process, self.worker.start(sys.executable, 'CPU'))
        with wave.open(str(self.root / 'b.wav')) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 24000))

    def test_cancellation_reaps_worker(self):
        started = time.monotonic()
        self.assertFalse(self.generate('wait', cancelled=lambda: time.monotonic() - started > .1))
        self.assertIsNotNone(self.process.poll())

    def test_timeout_reaps_worker(self):
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.generate('wait', timeout=.1)
        self.assertIsNotNone(self.process.poll())

    def test_worker_error_is_reported(self):
        with self.assertRaisesRegex(RuntimeError, 'test synthesis error'):
            self.generate('error')
        self.assertIsNotNone(self.process.poll())

    def test_chunks_bound_long_text_and_preserve_stress(self):
        chunks = list(text_chunks('Приві+т! ' + 'слово ' * 150))
        self.assertIn('\u0301', chunks[0])
        self.assertTrue(all(len(c) <= 181 for c in chunks))
        self.assertEqual(' '.join(chunks).count('слово'), 150)


if __name__ == '__main__':
    unittest.main()
