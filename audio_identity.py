"""Persistent PulseAudio/PipeWire client identity for OBS application matching."""
import ctypes as C
import ctypes.util
import os

APP_NAME = 'Twitch Piper'
APP_BINARY = 'twitch-piper'
PROPERTIES = {'application.name': APP_NAME, 'application.id': APP_BINARY,
              'application.process.binary': APP_BINARY, 'application.icon_name': APP_BINARY}


def playback_environment():
    env = os.environ.copy()
    # These apply to the playback CLIENT as well as its stream. paplay's
    # --property arguments alone describe the stream, not its parent client.
    props = ' '.join(f'{key}="{value}"' for key, value in PROPERTIES.items())
    env['PULSE_PROP'] = (env.get('PULSE_PROP', '') + ' ' + props).strip()
    return env


class AudioIdentity:
    def __init__(self):
        self.lib = None
        self.loop = None
        self.context = None
        self.started = False
        self.error = ''
        try:
            name = ctypes.util.find_library('pulse')
            if not name:
                raise OSError('libpulse is not installed')
            lib = self.lib = C.CDLL(name)
            signatures = {
                'pa_threaded_mainloop_new': (C.c_void_p, []),
                'pa_threaded_mainloop_get_api': (C.c_void_p, [C.c_void_p]),
                'pa_threaded_mainloop_start': (C.c_int, [C.c_void_p]),
                'pa_threaded_mainloop_stop': (None, [C.c_void_p]),
                'pa_threaded_mainloop_free': (None, [C.c_void_p]),
                'pa_threaded_mainloop_lock': (None, [C.c_void_p]),
                'pa_threaded_mainloop_unlock': (None, [C.c_void_p]),
                'pa_proplist_new': (C.c_void_p, []),
                'pa_proplist_sets': (C.c_int, [C.c_void_p, C.c_char_p, C.c_char_p]),
                'pa_proplist_free': (None, [C.c_void_p]),
                'pa_context_new_with_proplist': (C.c_void_p, [C.c_void_p, C.c_char_p, C.c_void_p]),
                'pa_context_connect': (C.c_int, [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p]),
                'pa_context_get_state': (C.c_int, [C.c_void_p]),
                'pa_context_disconnect': (None, [C.c_void_p]),
                'pa_context_unref': (None, [C.c_void_p]),
            }
            for symbol, (result, args) in signatures.items():
                function = getattr(lib, symbol)
                function.restype, function.argtypes = result, args
            self.loop = lib.pa_threaded_mainloop_new()
            if not self.loop:
                raise OSError('Cannot create audio mainloop')
            props = lib.pa_proplist_new()
            if not props:
                raise OSError('Cannot create audio properties')
            try:
                for key, value in PROPERTIES.items():
                    lib.pa_proplist_sets(props, key.encode(), value.encode())
                self.context = lib.pa_context_new_with_proplist(
                    lib.pa_threaded_mainloop_get_api(self.loop), APP_NAME.encode(), props)
            finally:
                lib.pa_proplist_free(props)
            if not self.context:
                raise OSError('Cannot create audio client')
            if lib.pa_context_connect(self.context, None, 0, None) < 0:
                raise OSError('Cannot connect to PulseAudio/PipeWire')
            if lib.pa_threaded_mainloop_start(self.loop) < 0:
                raise OSError('Cannot start audio mainloop')
            self.started = True
        except (OSError, AttributeError) as error:
            self.error = str(error)
            self.close()

    def status(self):
        if not self.context:
            return 'OBS audio identity unavailable · ' + self.error
        self.lib.pa_threaded_mainloop_lock(self.loop)
        try:
            state = self.lib.pa_context_get_state(self.context)
        finally:
            self.lib.pa_threaded_mainloop_unlock(self.loop)
        if state == 4:  # PA_CONTEXT_READY
            return 'OBS audio identity · twitch-piper'
        if state in (5, 6):
            return 'OBS audio identity disconnected · Restart after the audio service is available'
        return 'Connecting OBS audio identity…'

    def close(self):
        if self.started:
            self.lib.pa_threaded_mainloop_stop(self.loop)
            self.started = False
        if self.context:
            self.lib.pa_context_disconnect(self.context)
            self.lib.pa_context_unref(self.context)
            self.context = None
        if self.loop:
            self.lib.pa_threaded_mainloop_free(self.loop)
            self.loop = None
