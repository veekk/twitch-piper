#!/usr/bin/env python3
"""Local Twitch chat reader. Python standard library + Piper + aplay/ffplay."""
import collections
import array
import sys
import wave
import json
import os
from pathlib import Path
import queue
import random
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from styletts2_backend import StyleWorker

BASE = Path(__file__).resolve().parent


def voice_language_group(path):
    """Group installed voices by model metadata, independent of chat text."""
    try:
        config = json.loads(Path(str(path) + '.json').read_text())
        language = config.get('language', {})
        code = (language.get('family') or language.get('code', '').split('_')[0]).lower()
        name = language.get('name_english', '')
        if code:
            return f'{name} ({code})' if name else code.upper()
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    match = re.match(r'^([a-z]{2,3})_[A-Z]{2}[-_]', Path(path).name)
    return match[1].upper() if match else 'Other / unknown'


def parse_irc(line):
    tags, prefix = {}, ''
    if line.startswith('@'):
        raw, line = line[1:].split(' ', 1)
        escapes = {'s': ' ', ':': ';', 'r': '\r', 'n': '\n', '\\': '\\'}
        for item in raw.split(';'):
            key, _, value = item.partition('=')
            tags[key] = re.sub(r'\\(.)', lambda m: escapes.get(m[1], m[1]), value)
    if line.startswith(':'):
        prefix, line = line[1:].split(' ', 1)
    head, sep, tail = line.partition(' :')
    parts = head.split()
    return tags, prefix, parts[0], parts[1:] + ([tail] if sep else [])


def channel_name(value):
    value = re.sub(r'^https?://(?:www\.)?twitch\.tv/', '', value.strip()).strip('/#').lower()
    if not re.fullmatch(r'[a-z0-9_]{1,25}', value):
        raise ValueError('Enter a Twitch channel name or channel URL.')
    return value


def parse_aliases(raw):
    aliases = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        original, separator, replacement = line.partition('=')
        if not separator or not original.strip() or not replacement.strip():
            raise ValueError(f'Alias line {number}: use original = spoken replacement.')
        key = original.strip().casefold()
        if key in aliases:
            raise ValueError(f'Alias line {number}: duplicate alias for {original.strip()}.')
        aliases[key] = replacement.strip()
    return aliases


def apply_word_aliases(text, aliases):
    if not aliases:
        return text
    # Match case-folded text, but splice the original so unmatched spelling is preserved.
    # Boundaries map folded offsets back to whole original characters (ß expands to ss).
    folded = []
    boundaries = {0: 0}
    offset = 0
    for index, character in enumerate(text):
        part = character.casefold()
        folded.append(part)
        offset += len(part)
        boundaries[offset] = index + 1
    pattern = r'(?<!\w)(?:' + '|'.join(re.escape(key) for key in sorted(aliases, key=len, reverse=True)) + r')(?!\w)'
    result, previous = [], 0
    for match in re.finditer(pattern, ''.join(folded)):
        if match.start() not in boundaries or match.end() not in boundaries:
            continue
        start, end = boundaries[match.start()], boundaries[match.end()]
        result.extend((text[previous:start], aliases[match[0]]))
        previous = end
    result.append(text[previous:])
    return ''.join(result)


def speech_text(text, settings):
    if text.startswith('\x01ACTION ') and text.endswith('\x01'):
        text = text[8:-1]
    if settings.get('prefix_only', False):
        prefix = settings.get('message_prefix', '%')
        if not prefix or not text.startswith(prefix):
            return ''
        text = text[len(prefix):].lstrip()
    elif settings.get('strip_percent', False) and text.startswith('%'):
        text = text[1:].lstrip()
    if settings['commands'] and text.lstrip().startswith('!'):
        return ''
    if settings['links']:
        text = re.sub(r'(?i)\b(?:https?://|www\.)\S+', '', text)
    text = ' '.join(''.join(c for c in text if c.isprintable() or c.isspace()).split())
    text = apply_word_aliases(text, settings.get('word_aliases', {}))
    return text[:settings['limit']]


def spoken_message(name, text, settings):
    spoken = speech_text(text, settings)
    return nickname_prefix(name, spoken, settings)


def nickname_prefix(name, spoken, settings):
    if not spoken or not settings['names']:
        return spoken
    nickname = settings.get('nickname_aliases', {}).get(name.casefold(), name)
    phrase = settings.get('says', 'says').strip()
    prefix = f'{nickname} {phrase}'.strip()
    return f'{prefix}: {spoken}'


class NicknameHistory:
    """Tracks successfully played messages; the timeout is an idle gap."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.author = None
        self.finished_at = 0.0

    def format(self, author, body, settings, now):
        repeat = (settings.get('skip_repeat_names', False)
                  and self.author == author.casefold()
                  and 0 <= now - self.finished_at < settings.get('nickname_timeout', 15))
        return body if repeat else nickname_prefix(author, body, settings)

    def completed(self, author, settings, now):
        if author is not None and settings['names']:
            self.author = author.casefold()
            self.finished_at = now
        else:
            self.reset()


class Chat(threading.Thread):
    def __init__(self, channel, emit, username='', token=''):
        super().__init__(daemon=True)
        self.channel, self.emit = channel, emit
        self.username = username or f'justinfan{random.randrange(100000, 99999999)}'
        self.token = token
        self.stop = threading.Event()
        self.sock = None

    def close(self):
        self.stop.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        delay = 2
        while not self.stop.is_set():
            try:
                self.emit('status', 'Connecting…')
                with socket.create_connection(('irc.chat.twitch.tv', 6697), timeout=10) as raw:
                    with ssl.create_default_context().wrap_socket(raw, server_hostname='irc.chat.twitch.tv') as sock:
                        self.sock = sock
                        sock.settimeout(1)
                        def send(line):
                            sock.sendall((line + '\r\n').encode())
                        send('CAP REQ :twitch.tv/tags twitch.tv/commands')
                        send('PASS ' + (self.token if self.token.startswith('oauth:') else 'oauth:' + self.token) if self.token else 'PASS SCHMOOPIIE')
                        send('NICK ' + self.username)
                        send('JOIN #' + self.channel)
                        buffer = b''
                        last = time.monotonic()
                        joined = False
                        started = last
                        while not self.stop.is_set():
                            try:
                                data = sock.recv(8192)
                            except socket.timeout:
                                if time.monotonic() - last > 240:
                                    raise OSError('Chat connection timed out')
                                if not joined and time.monotonic() - started > 20:
                                    raise OSError('Channel join timed out; check channel or credentials')
                                continue
                            if not data:
                                raise OSError('Chat connection closed')
                            last = time.monotonic()
                            buffer += data
                            if len(buffer) > 1024 * 1024:
                                raise OSError('Invalid IRC response')
                            while b'\r\n' in buffer:
                                line, buffer = buffer.split(b'\r\n', 1)
                                tags, prefix, command, args = parse_irc(line.decode('utf-8', 'replace'))
                                if command == 'PING':
                                    send('PONG :' + args[-1])
                                elif command in ('366', 'ROOMSTATE'):
                                    joined = True
                                    delay = 2
                                    self.emit('status', 'Connected to #' + self.channel)
                                elif command == 'PRIVMSG' and len(args) >= 2:
                                    login = prefix.split('!')[0]
                                    self.emit('chat', (tags.get('display-name') or login, args[-1], tags.get('id', ''), login))
                                elif command in ('CLEARMSG', 'CLEARCHAT'):
                                    self.emit('moderation', None)
                                elif command == 'RECONNECT':
                                    raise OSError('Twitch requested reconnection')
                                elif command == 'NOTICE':
                                    self.emit('notice', args[-1])
                                    if 'authentication failed' in args[-1].lower() or 'improperly formatted auth' in args[-1].lower():
                                        self.emit('status', 'Authentication failed — disconnect and check credentials')
                                        return
            except (OSError, ValueError) as exc:
                if not self.stop.is_set():
                    self.emit('status', f'{exc}. Retrying in {delay}s…')
            finally:
                self.sock = None
            if self.stop.wait(delay):
                break
            delay = min(delay * 2, 60)


def adjust_wav_volume(path, volume):
    """Apply app-only gain to Piper's signed 16-bit PCM without system mixer changes."""
    if not 0 <= volume <= 100:
        raise ValueError('Volume must be between 0 and 100.')
    if volume == 100:
        return
    with wave.open(str(path), 'rb') as source:
        params = source.getparams()
        if source.getsampwidth() != 2 or source.getcomptype() != 'NONE':
            raise ValueError('Volume control requires Piper 16-bit PCM audio.')
        samples = array.array('h', source.readframes(source.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    gain = volume / 100
    for index in range(len(samples)):
        samples[index] = round(samples[index] * gain)
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as target:
        target.setparams(params)
        target.writeframes(samples.tobytes())


class Speaker(threading.Thread):
    def __init__(self, emit):
        super().__init__(daemon=True)
        self.emit = emit
        self.items = collections.deque(maxlen=30)
        self.cv = threading.Condition()
        self.stopped = False
        self.paused = False
        self.generation = 0
        self.process = None
        self.style_worker = StyleWorker()
        self.volume = 100
        self.nickname_history = NicknameHistory()
        self.start()

    def set_volume(self, volume):
        with self.cv:
            self.volume = max(0, min(100, int(volume)))

    def enqueue(self, text, settings, author=None):
        with self.cv:
            snapshot = settings.copy()
            snapshot['_author'] = author
            self.items.append((time.monotonic(), text, snapshot))
            self.cv.notify()

    def clear(self):
        with self.cv:
            self.items.clear()
            self.skip()

    def skip(self):
        with self.cv:
            self.generation += 1
            self.nickname_history.reset()
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def execute(self, args, generation, data=None, timeout=60):
        with self.cv:
            if self.stopped or generation != self.generation:
                return False
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            process = self.process
        try:
            _, error = process.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError('Speech process timed out')
        finally:
            with self.cv:
                self.process = None
        if generation != self.generation:
            return False
        if process.returncode:
            raise RuntimeError(error.decode('utf-8', 'replace')[-500:] or 'Audio process failed')
        return True

    def run(self):
        while True:
            with self.cv:
                self.cv.wait_for(lambda: self.stopped or (self.items and not self.paused))
                if self.stopped:
                    self.style_worker.close()
                    return
                created, text, settings = self.items.popleft()
                generation = self.generation
            if time.monotonic() - created > 45:
                continue
            failed, played = False, False
            engine_name = settings.get('engine', 'Piper')
            try:
                author = settings.get('_author')
                if author is not None:
                    with self.cv:
                        text = self.nickname_history.format(author, text, settings, time.monotonic())
                self.emit('speech', text)
                self.emit('engine', (engine_name + ' · Starting speech engine', True))
                with tempfile.TemporaryDirectory(prefix='twitch-piper-') as temp:
                    wav = str(Path(temp) / 'speech.wav')
                    if settings.get('engine', 'Piper') == 'StyleTTS2 Ukrainian':
                        with self.cv:
                            if self.stopped or generation != self.generation:
                                continue
                            self.process = self.style_worker.start(settings['style_python'], settings['style_device'])
                        try:
                            ready = self.style_worker.generate(text, settings, wav,
                                lambda: self.stopped or generation != self.generation,
                                progress=lambda message: self.emit('engine', ('StyleTTS2 · ' + message, True)))
                        finally:
                            with self.cv:
                                self.process = None
                    else:
                        self.style_worker.close()
                        args = [settings['piper'], '-m', settings['model'], '-f', wav,
                                '--length_scale', str(1 / settings['speed']), '-s', str(settings['speaker'])]
                        self.emit('engine', ('Piper · Loading voice and generating speech', True))
                        ready = self.execute(args, generation, (text + '\n').encode())
                    if ready:
                        with self.cv:
                            volume = self.volume
                        adjust_wav_volume(wav, volume)
                        player = [shutil.which('aplay'), '-q', wav] if shutil.which('aplay') else [shutil.which('ffplay'), '-nodisp', '-autoexit', '-loglevel', 'error', wav]
                        with wave.open(wav, 'rb') as audio:
                            playback_timeout = max(60, audio.getnframes() / audio.getframerate() + 10)
                        self.emit('engine', (engine_name + ' · Playing audio', False))
                        played = self.execute(player, generation, timeout=playback_timeout)
                        if played:
                            with self.cv:
                                if generation == self.generation:
                                    self.nickname_history.completed(author, settings, time.monotonic())
            except Exception as exc:
                failed = True
                self.emit('engine', ('Engine error · ' + str(exc), False))
                self.emit('error', str(exc))
            finally:
                if not failed:
                    state = 'Ready' if played else 'Stopped · Next message may reload the model'
                    self.emit('engine', (engine_name + ' · ' + state, False))
                self.emit('speech', '')

    def close(self):
        with self.cv:
            self.stopped = True
            self.clear()
            self.cv.notify_all()
