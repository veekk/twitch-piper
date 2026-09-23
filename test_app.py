import unittest
import threading
from unittest.mock import MagicMock, patch
import struct
import tempfile
import wave
from pathlib import Path
from engine import adjust_wav_volume, apply_word_aliases, Chat
from app import parse_irc, channel_name, speech_text, Speaker, parse_aliases, spoken_message, NicknameHistory

class Tests(unittest.TestCase):
    def test_tagged_unicode_chat(self):
        tags, prefix, cmd, args = parse_irc('@display-name=Hello\\sWorld;id=abc :hello!hello@hello PRIVMSG #channel :Привіт : world')
        self.assertEqual(tags['display-name'], 'Hello World')
        self.assertEqual((cmd, args), ('PRIVMSG', ['#channel', 'Привіт : world']))
    def test_ping(self):
        self.assertEqual(parse_irc('PING :tmi.twitch.tv')[2:], ('PING', ['tmi.twitch.tv']))
    def test_channel(self):
        self.assertEqual(channel_name('https://www.twitch.tv/Example/'), 'example')
        with self.assertRaises(ValueError):
            channel_name('abc\r\nJOIN #other')
    def test_filters(self):
        settings = dict(commands=True, links=True, limit=30)
        self.assertEqual(speech_text('!skip', settings), '')
        self.assertEqual(speech_text('hello https://example.com world', settings), 'hello world')
        self.assertEqual(speech_text('\x01ACTION waves\x01', settings), 'waves')
        self.assertEqual(len(speech_text('x' * 100, settings)), 30)
    def test_playback_client_identity_environment(self):
        from audio_identity import playback_environment
        from unittest.mock import patch
        with patch.dict('os.environ', {'PULSE_PROP': 'media.role=music'}):
            env = playback_environment()
        self.assertIn('media.role=music', env['PULSE_PROP'])
        self.assertIn('application.name="Twitch Piper"', env['PULSE_PROP'])
        self.assertIn('application.process.binary="twitch-piper"', env['PULSE_PROP'])

    def test_preload_synthesizes_without_playback(self):
        from unittest.mock import patch
        done = threading.Event()
        speaker = Speaker(lambda kind, value: done.set() if kind == 'engine' and value[0].endswith('Ready') else None)
        try:
            with patch.object(speaker, 'execute', return_value=True) as execute:
                speaker.preload(dict(piper='piper', model='voice.onnx', speed=1, speaker=0))
                self.assertTrue(done.wait(2))
                execute.assert_called_once()
                self.assertIn('-m', execute.call_args.args[0])
        finally:
            speaker.close()
            speaker.join(2)

    def test_obs_playback_identity(self):
        from engine import playback_command
        from unittest.mock import patch
        with patch('engine.shutil.which', side_effect=lambda name: '/usr/bin/' + name):
            args = playback_command('/tmp/test.wav')
        self.assertEqual(args[0], '/usr/bin/paplay')
        self.assertIn('--property=application.process.binary=twitch-piper', args)
        self.assertIn('--property=application.name=Twitch Piper', args)
        self.assertEqual(args[-1], '/tmp/test.wav')
        with patch('engine.shutil.which', side_effect=lambda name: None if name == 'paplay' else '/usr/bin/' + name):
            self.assertEqual(playback_command('/tmp/test.wav'), ['/usr/bin/aplay', '-q', '/tmp/test.wav'])

    def test_required_message_prefix(self):
        settings = dict(commands=False, links=False, limit=100)
        self.assertEqual(speech_text('hello', settings), 'hello')
        settings.update(prefix_only=True, message_prefix='%')
        self.assertEqual(speech_text('hello', settings), '')
        self.assertEqual(speech_text(' %hello', settings), '')
        self.assertEqual(speech_text('% hello', settings), 'hello')
        self.assertEqual(speech_text('%', settings), '')
        settings['strip_percent'] = True
        self.assertEqual(speech_text('%%hello', settings), '%hello')
        settings['message_prefix'] = '>>'
        self.assertEqual(speech_text('>>hello', settings), 'hello')
        self.assertEqual(speech_text('%hello', settings), '')
        settings['prefix_only'] = False
        self.assertEqual(speech_text('hello', settings), 'hello')

    def test_leading_percent_only(self):
        settings = dict(commands=False, links=False, limit=100, strip_percent=True)
        self.assertEqual(speech_text('%hello 50%', settings), 'hello 50%')
        self.assertEqual(speech_text('hello %world', settings), 'hello %world')
        self.assertEqual(speech_text(' %hello', settings), '%hello')
        self.assertEqual(speech_text('%%hello', settings), '%hello')
        self.assertEqual(speech_text('%', settings), '')
        settings['strip_percent'] = False
        self.assertEqual(speech_text('%hello', settings), '%hello')

    def test_aliases_and_custom_phrase(self):
        settings = dict(commands=True, links=True, limit=100, names=True,
                        says='writes', strip_percent=True,
                        nickname_aliases=parse_aliases('Gamer123 = Alex'),
                        word_aliases=parse_aliases('gg = good game\ncat = dog\ndog = wolf\nпривіт = hello'))
        self.assertEqual(spoken_message('GAMER123', '%GG! cat dog cats Привіт', settings),
                         'Alex writes: good game! dog wolf cats hello')
        settings['says'] = ''
        self.assertEqual(spoken_message('gamer123', 'gg', settings), 'Alex: good game')
        self.assertEqual(spoken_message('someone', 'gg', settings), 'someone: good game')
        settings['names'] = False
        self.assertEqual(spoken_message('gamer123', 'gg', settings), 'good game')
        self.assertEqual(spoken_message('gamer123', '%', settings), '')

    def test_alias_validation_and_longest_match(self):
        for raw in ['missing separator', ' = name', 'name = ', 'GG = a\ngg = b']:
            with self.assertRaises(ValueError):
                parse_aliases(raw)
        settings = dict(commands=False, links=False, limit=100,
                        word_aliases=parse_aliases('good = nice\ngood game = well played\nc++ = see plus plus'))
        self.assertEqual(speech_text('Good game, good c++!', settings), 'well played, nice see plus plus!')
        settings['limit'] = 4
        self.assertEqual(speech_text('good game', settings), 'well')

    def test_nickname_repeat_timeout_and_author_change(self):
        history = NicknameHistory()
        settings = dict(names=True, skip_repeat_names=True, nickname_timeout=15, says='says')
        self.assertEqual(history.format('Alex', 'first', settings, 0), 'Alex says: first')
        history.completed('Alex', settings, 10)
        self.assertEqual(history.format('ALEX', 'second', settings, 24), 'second')
        self.assertEqual(history.format('Alex', 'third', settings, 25), 'Alex says: third')
        self.assertEqual(history.format('Sam', 'hello', settings, 11), 'Sam says: hello')
        history.completed('Sam', settings, 12)
        self.assertEqual(history.format('Alex', 'back', settings, 13), 'Alex says: back')

    def test_nickname_repeat_resets_and_disabled_option(self):
        history = NicknameHistory()
        settings = dict(names=True, skip_repeat_names=True, nickname_timeout=15)
        history.completed('Alex', settings, 10)
        history.reset()
        self.assertEqual(history.format('Alex', 'hello', settings, 11), 'Alex says: hello')
        history.completed('Alex', settings, 12)
        settings['skip_repeat_names'] = False
        self.assertEqual(history.format('Alex', 'hello', settings, 13), 'Alex says: hello')
        settings['names'] = False
        self.assertEqual(history.format('Alex', 'hello', settings, 13), 'hello')
        history.completed('Alex', settings, 14)
        self.assertIsNone(history.author)

    def test_nickname_repeat_uses_original_author_not_alias(self):
        history = NicknameHistory()
        settings = dict(names=True, skip_repeat_names=True, nickname_timeout=15,
                        nickname_aliases={'alex': 'Viewer', 'sam': 'Viewer'}, says='writes')
        history.completed('Alex', settings, 10)
        self.assertEqual(history.format('Sam', 'hello', settings, 11), 'Viewer writes: hello')
        self.assertEqual(history.format('Alex', 'again', settings, 11), 'again')

    def test_volume_scales_pcm_and_preserves_audio_format(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'speech.wav'
            for volume, expected in [(100, (-30000, -1000, 0, 1000, 30000)),
                                     (50, (-15000, -500, 0, 500, 15000)),
                                     (0, (0, 0, 0, 0, 0))]:
                with wave.open(str(path), 'wb') as wav:
                    wav.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
                    wav.writeframes(struct.pack('<5h', -30000, -1000, 0, 1000, 30000))
                adjust_wav_volume(path, volume)
                with wave.open(str(path), 'rb') as wav:
                    self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()),
                                     (1, 2, 22050, 5))
                    self.assertEqual(struct.unpack('<5h', wav.readframes(5)), expected)

    def test_unicode_aliases_preserve_unmatched_text_and_do_not_cascade(self):
        aliases = parse_aliases('straße = street\nss = double s\nstreet = road\nΟΣ = Greek')
        self.assertEqual(apply_word_aliases('Straße STRASSE straße ß ΟΣ OtherCase', aliases),
                         'street street street double s Greek OtherCase')
        self.assertEqual(apply_word_aliases('Straßen strassen', aliases), 'Straßen strassen')
        self.assertEqual(apply_word_aliases('İ', parse_aliases('i = eye')), 'İ')
        self.assertEqual(apply_word_aliases('Straße gut', parse_aliases('straße = street\nstraße gut = good street')), 'good street')

    def test_chat_event_preserves_login_and_display_name(self):
        events = []
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'chat':
                chat.stop.set()
        chat = Chat('example', emit)
        tls = MagicMock()
        tls.recv.return_value = '@display-name=視聴者;id=123 :someuser!someuser@host PRIVMSG #example :hello\r\n'.encode()
        context = MagicMock()
        context.wrap_socket.return_value.__enter__.return_value = tls
        with patch('engine.socket.create_connection'), patch('engine.ssl.create_default_context', return_value=context):
            chat.run()
        self.assertIn(('chat', ('視聴者', 'hello', '123', 'someuser')), events)

    def test_long_audio_gets_duration_based_playback_timeout(self):
        finished = threading.Event()
        timeouts = []
        errors = []
        def emit(kind, value):
            if kind == 'error':
                errors.append(value)
            if kind == 'speech' and not value:
                finished.set()
        def popen(args, **kwargs):
            process = MagicMock()
            process.returncode = 0
            def communicate(data=None, timeout=None):
                if '-m' in args:
                    with wave.open(args[args.index('-f') + 1], 'wb') as audio:
                        audio.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                        audio.writeframes(b'\0\0' * 70000)
                    self.assertEqual(timeout, 60)
                else:
                    timeouts.append(timeout)
                return None, b''
            process.communicate.side_effect = communicate
            return process
        speaker = Speaker(emit)
        try:
            with patch('engine.subprocess.Popen', side_effect=popen):
                speaker.enqueue('Long message', dict(piper='piper', model='voice.onnx', speed=.5, speaker=0))
                self.assertTrue(finished.wait(3))
            self.assertEqual(errors, [])
            self.assertEqual(timeouts, [80])
        finally:
            speaker.close()
            speaker.join(2)

    def test_queue_and_cancellation(self):
        speaker = Speaker(lambda *args: None)
        with speaker.cv:
            speaker.paused = True
        for i in range(40):
            speaker.enqueue(str(i), {})
        self.assertEqual(len(speaker.items), 30)
        self.assertEqual(speaker.items[0][1], '10')
        generation = speaker.generation
        speaker.clear()
        self.assertEqual(len(speaker.items), 0)
        self.assertFalse(speaker.execute(['/does-not-exist'], generation))
        speaker.close()
        speaker.join(2)
        self.assertFalse(speaker.is_alive())

if __name__ == '__main__':
    unittest.main()
