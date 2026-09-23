"""Persistent subprocess transport with cancellable synthesis."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

BASE = Path(__file__).resolve().parent
CATALOG = json.loads((BASE / 'styletts2_voices.json').read_text())


class StyleWorker:
    def __init__(self):
        self.process = None
        self.key = None
        self.log = None

    def start(self, python, device):
        key = (python, device)
        if self.process is None or self.process.poll() is not None or key != self.key:
            self.close()
            self.log = tempfile.TemporaryFile()
            cache = BASE / '.cache-styletts2'
            env = os.environ.copy()
            for name, folder in [('HF_HOME', 'huggingface'), ('STANZA_RESOURCES_DIR', 'stanza'),
                                 ('TORCH_HOME', 'torch'), ('NUMBA_CACHE_DIR', 'numba')]:
                env.setdefault(name, str(cache / folder))
            try:
                self.process = subprocess.Popen([python, '-u', str(BASE / 'styletts2_worker.py')],
                    stdin=subprocess.PIPE, stdout=self.log, stderr=self.log, env=env)
            except Exception:
                self.close()
                raise
            self.key = key
        return self.process

    def generate(self, text, settings, output, cancelled, timeout=600):
        reply = Path(output).with_suffix('.json')
        request = dict(text=text, voice=settings['style_voice'], speed=settings['speed'],
                       device=settings['style_device'], numbers=settings.get('style_numbers', True), output=output, reply=str(reply))
        try:
            self.process.stdin.write((json.dumps(request) + '\n').encode())
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancelled():
                    self.close()
                    return False
                if reply.exists():
                    result = json.loads(reply.read_text())
                    if 'error' in result:
                        raise RuntimeError(result['error'])
                    return True
                if self.process.poll() is not None:
                    self.log.seek(0, 2)
                    self.log.seek(max(0, self.log.tell() - 1500))
                    detail = self.log.read().decode('utf-8', 'replace')
                    raise RuntimeError('StyleTTS2 worker exited. Check the optional environment. ' + detail)
                time.sleep(.05)
            raise RuntimeError('StyleTTS2 timed out; first use needs model downloads. Check network access.')
        except Exception:
            self.close()
            if cancelled():
                return False
            raise

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait()
            self.process.stdin.close()
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None
