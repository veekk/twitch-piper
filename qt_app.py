"""Native Qt / KDE Breeze interface for the local chat reader."""
import collections
import configparser
import html
import json
import os
from pathlib import Path
import queue
import re
import shutil
import sys
import time

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QStyleFactory,
    QSystemTrayIcon, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
from resource_monitor import ResourceMonitor
from styletts2_backend import CATALOG
from engine import BASE, Chat, Speaker, channel_name, parse_aliases, speech_text, voice_language_group


class Window(QMainWindow):
    def __init__(self, settings_path=None):
        super().__init__()
        self._closing = False
        self._force_quit = False
        self._close_prompt_open = False
        self._close_dialog = None
        self._restore_maximized = False
        self._startup_attempted = False
        self.settings_path = Path(settings_path) if settings_path else BASE / 'settings.json'
        self.setWindowTitle('Twitch × Piper')
        self.setWindowIcon(QIcon(str(BASE / 'assets' / 'twitch-piper.png')))
        self.resize(980, 790)
        self.setMinimumSize(760, 650)
        self.events = queue.Queue(maxsize=2000)
        self.chat = None
        self.session = 0
        self.seen = collections.deque(maxlen=500)
        self.speaker = Speaker(self.emit)
        self.speaking = ''
        self.inputs = {}
        try:
            saved = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            saved = {}
        paths = sorted(p for p in (Path.home() / 'Downloads/piper-voices').rglob('*.onnx') if Path(str(p) + '.json').is_file())
        default = next((str(p) for p in paths if p.name == 'en_US-lessac-medium.onnx'), str(paths[0]) if paths else '')
        self.values = dict(engine='Piper', style_voice=CATALOG['voices'][0],
            style_python=str(BASE / '.venv-styletts2/bin/python'), style_device='Auto', style_numbers=True, channel='', model=default, piper=shutil.which('piper-tts') or shutil.which('piper') or '',
            speed='1.0', speaker='0', limit='280', ignored='nightbot, streamelements, moobot', names=True,
            commands=True, links=True, strip_percent=False, prefix_only=False, message_prefix='%', says='says', nickname_aliases='', word_aliases='',
            skip_repeat_names=True, nickname_timeout='15', appearance='Plasma (system)', volume=100,
            auto_connect=False, minimize_to_tray=False, close_action='Ask every time')
        self.values.update({key: value for key, value in saved.items() if key in self.values})
        self.aliases = {key: self.values[key] for key in ('nickname_aliases', 'word_aliases')}
        self.voice_paths = {}
        self.voice_groups = {}
        self.last_voice = {}
        for path in paths:
            self.add_voice(str(path))
        if self.values['model']:
            self.add_voice(self.values['model'])
        outer = QWidget()
        self.setCentralWidget(outer)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel('Twitch × Piper')
        font = QFont(title.font())
        font.setPointSize(font.pointSize() + 8)
        font.setBold(True)
        title.setFont(font)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(44, 44))
        header.addWidget(logo)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel('Local chat reader'))
        self.tray_button = self.button('To tray', self.hide_to_tray, 'window-minimize')
        self.tray_button.setToolTip('Keep reading chat in the background; restore from the tray icon.')
        header.addWidget(self.tray_button)
        layout.addLayout(header)
        connection = QHBoxLayout()
        connection.addWidget(QLabel('Channel:'))
        self.channel = self.entry('channel')
        self.channel.setPlaceholderText('Channel name or twitch.tv/channel')
        self.channel.returnPressed.connect(self.connect_chat)
        connection.addWidget(self.channel, 1)
        self.connect_button = self.button('Connect', self.connect_chat, 'network-connect')
        connection.addWidget(self.connect_button)
        layout.addLayout(connection)
        self.status = QLabel('Disconnected · Choose a channel to start')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.build_chat()
        self.build_voice()
        self.build_aliases()
        self.build_setup()
        save_row = QHBoxLayout()
        self.saved_status = QLabel('Save settings to apply alias edits.')
        save_row.addWidget(self.saved_status, 1)
        save_row.addWidget(self.button('Save settings', self.apply_settings, 'document-save'))
        layout.addLayout(save_row)
        controls = QHBoxLayout()
        self.pause_button = self.button('Pause', self.pause, 'media-playback-pause')
        controls.addWidget(self.pause_button)
        controls.addWidget(self.button('Skip', self.speaker.skip, 'media-skip-forward'))
        controls.addWidget(self.button('Clear queue', self.speaker.clear, 'edit-clear'))
        controls.addStretch()
        self.volume_label = QLabel('Volume:')
        controls.addWidget(self.volume_label)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(140)
        self.volume_slider.setAccessibleName('Speech volume')
        self.volume_slider.setToolTip('Speech volume, 0–100%. Applies when the next message starts playing.')
        self.volume_slider.setValue(int(self.values['volume']))
        self.volume_label.setBuddy(self.volume_slider)
        self.volume_value = QLabel()
        self.volume_value.setMinimumWidth(45)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.inputs['volume'] = self.volume_slider
        self.change_volume(self.volume_slider.value())
        controls.addWidget(self.volume_slider)
        controls.addWidget(self.volume_value)
        controls.addSpacing(12)
        self.queue_status = QLabel('0 / 30 queued')
        controls.addWidget(self.queue_status)
        layout.addLayout(controls)
        self.now = QLabel('Speech idle')
        self.now.setWordWrap(True)
        layout.addWidget(self.now)
        self.engine_message = 'Engine idle · Loads when the first message is read'
        self.engine_started = None
        self.engine_status = QLabel(self.engine_message)
        self.engine_status.setWordWrap(True)
        self.engine_progress = QProgressBar()
        self.engine_progress.setRange(0, 0)
        self.engine_progress.setTextVisible(False)
        self.engine_progress.setFixedWidth(110)
        self.engine_progress.setFixedHeight(12)
        self.engine_progress.hide()
        engine_row = QHBoxLayout()
        engine_row.addWidget(self.engine_status, 1)
        engine_row.addWidget(self.engine_progress)
        layout.addLayout(engine_row)
        self.resources = ResourceMonitor()
        self.load_label = QLabel(self.resources.latest)
        self.load_label.setWordWrap(True)
        self.load_label.setToolTip('App and child processes, including the speech worker. CPU: 100% = one logical core. '
            'RAM: summed resident memory (shared pages may be counted twice). GPU: NVIDIA per-process SM utilization; '
            'VRAM: GPU memory. N/A means unavailable or unsupported. Samples update about every 1–4 seconds.')
        layout.addWidget(self.load_label)
        self.apply_appearance(self.values['appearance'])
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.build_tray()
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.timeout.connect(self.startup_connect)
        self.startup_timer.start(0)

    def change_volume(self, volume):
        self.speaker.set_volume(volume)
        self.volume_value.setText('Muted' if volume == 0 else f'{volume}%')

    def button(self, text, callback, icon=None):
        button = QPushButton(text)
        if icon:
            button.setIcon(QIcon.fromTheme(icon))
        button.clicked.connect(callback)
        return button

    def entry(self, key):
        widget = QLineEdit(str(self.values[key]))
        self.inputs[key] = widget
        return widget

    def check(self, key, text):
        widget = QCheckBox(text)
        widget.setChecked(bool(self.values[key]))
        self.inputs[key] = widget
        return widget

    def number(self, key, low, high, step=1, decimal=False):
        widget = QDoubleSpinBox() if decimal else QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        if decimal:
            widget.setDecimals(1)
        widget.setValue(float(self.values[key]) if decimal else int(self.values[key]))
        self.inputs[key] = widget
        return widget

    def page(self, title, scroll=False):
        page = QWidget()
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QScrollArea.Shape.NoFrame)
            area.setWidget(page)
            self.tabs.addTab(area, title)
        else:
            self.tabs.addTab(page, title)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        return layout

    def build_chat(self):
        layout = self.page('Chat')
        self.log = QTextBrowser()
        self.log.setOpenLinks(False)
        self.log.document().setMaximumBlockCount(400)
        self.append('Messages appear here after you connect. Test your voice below.')
        layout.addWidget(self.log, 1)
        test = QGroupBox('Try a message')
        row = QHBoxLayout(test)
        self.sample_name = QLineEdit('Piper')
        self.sample_name.setMaximumWidth(130)
        self.sample_name.setAccessibleName('Test nickname')
        self.sample = QLineEdit('Hello! Twitch chat is ready to read aloud with Piper.')
        self.sample.setAccessibleName('Test message')
        self.sample.returnPressed.connect(self.test_voice)
        row.addWidget(QLabel('Nickname:'))
        row.addWidget(self.sample_name)
        row.addWidget(self.sample, 1)
        row.addWidget(self.button('Read test', self.test_voice, 'media-playback-start'))
        layout.addWidget(test)

    def build_voice(self):
        layout = self.page('Voice && filters', scroll=True)
        engine_row = QFormLayout()
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(['Piper', 'StyleTTS2 Ukrainian'])
        self.engine_combo.setCurrentText(self.values['engine'])
        self.inputs['engine'] = self.engine_combo
        engine_row.addRow('Speech engine:', self.engine_combo)
        layout.addLayout(engine_row)
        voice = QGroupBox('Piper voice')
        self.piper_group = voice
        form = QFormLayout(voice)
        self.language_combo = QComboBox()
        self.voice_combo = QComboBox()
        self.voice_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.voice_combo.setMinimumContentsLength(18)
        self.language_combo.currentTextChanged.connect(self.select_language)
        self.voice_combo.currentTextChanged.connect(self.select_voice)
        form.addRow('Language:', self.language_combo)
        row = QHBoxLayout()
        row.addWidget(self.voice_combo, 1)
        row.addWidget(self.button('Browse…', self.browse, 'document-open'))
        form.addRow('Voice:', row)
        self.model_hint = QLabel()
        self.model_hint.setWordWrap(True)
        self.model_hint.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.model_hint)
        form.addRow('Speaker ID:', self.number('speaker', 0, 999))
        form.addRow('Maximum characters:', self.number('limit', 20, 1000, 20))
        layout.addWidget(voice)
        self.style_group = QGroupBox('StyleTTS2 · Ukrainian')
        style_form = QFormLayout(self.style_group)
        for key, label, items in [('style_voice', 'Voice:', CATALOG['voices']),
                                  ('style_device', 'Device:', ['Auto', 'CPU', 'CUDA'])]:
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentText(self.values[key])
            self.inputs[key] = combo
            style_form.addRow(label, combo)
        style_form.addRow(self.check('style_numbers', 'Read numbers as Ukrainian words'))
        style_form.addRow('Python executable:', self.entry('style_python'))
        note = QLabel('Install the optional environment from README first. The first test downloads models.\nAuto uses CUDA when available, otherwise CPU. Voices are from patriotyk’s demo.')
        note.setWordWrap(True)
        style_form.addRow(note)
        layout.addWidget(self.style_group)
        speed_form = QFormLayout()
        speed_form.addRow('Speed:', self.number('speed', .5, 2, .1, True))
        layout.addLayout(speed_form)
        self.engine_combo.currentTextChanged.connect(self.select_engine)
        self.select_engine(self.engine_combo.currentText())
        nicknames = QGroupBox('Nicknames')
        form = QFormLayout(nicknames)
        form.addRow(self.check('names', 'Read nicknames'))
        phrase = self.entry('says')
        phrase.setPlaceholderText('Leave empty to omit “says”')
        form.addRow('After nickname:', phrase)
        form.addRow(self.check('skip_repeat_names', 'Skip nickname for consecutive messages by the same author'))
        timeout = self.number('nickname_timeout', 1, 3600, 1, True)
        timeout.setSuffix(' seconds')
        form.addRow('Repeat after idle gap:', timeout)
        layout.addWidget(nicknames)
        filters = QGroupBox('Message filters')
        form = QFormLayout(filters)
        prefix_toggle = self.check('prefix_only', 'Read only messages starting with this prefix')
        form.addRow(prefix_toggle)
        prefix_entry = self.entry('message_prefix')
        prefix_entry.setPlaceholderText('%')
        prefix_entry.setToolTip('Must be at the very beginning. The matched prefix is removed before speaking.')
        prefix_entry.setEnabled(prefix_toggle.isChecked())
        prefix_toggle.toggled.connect(prefix_entry.setEnabled)
        form.addRow('Required prefix:', prefix_entry)
        for key, label in [('strip_percent', 'Remove only a leading % sign'), ('commands', 'Skip messages starting with !'), ('links', 'Remove web links from speech')]:
            form.addRow(self.check(key, label))
        ignored = self.entry('ignored')
        ignored.setPlaceholderText('Comma-separated usernames')
        form.addRow('Ignore users:', ignored)
        layout.addWidget(filters)
        layout.addStretch()
        self.sync_voice()

    def select_engine(self, engine):
        style = engine == 'StyleTTS2 Ukrainian'
        self.piper_group.setVisible(not style)
        self.style_group.setVisible(style)
        self.inputs['speed'].setRange(.7 if style else .5, 1.3 if style else 2)
        english = 'Hello! Twitch chat is ready to read aloud with Piper.'
        ukrainian = 'Привіт! Дякую за повідомлення. Український голос готовий до роботи.'
        if self.sample.text() in (english, ukrainian):
            self.sample.setText(ukrainian if style else english)
        if self.sample_name.text() in ('Piper', 'Пайпер'):
            self.sample_name.setText('Пайпер' if style else 'Piper')

    def build_aliases(self):
        layout = self.page('Aliases')
        row = QHBoxLayout()
        self.alias_editors = {}
        for key, title, example in [('nickname_aliases', 'Nickname aliases', 'gamer123 = Alex'), ('word_aliases', 'Word & phrase aliases', 'gg = good game')]:
            group = QGroupBox(title)
            column = QVBoxLayout(group)
            column.addWidget(QLabel(example))
            editor = QPlainTextEdit(self.aliases[key])
            editor.setPlaceholderText('One original = replacement per line')
            column.addWidget(editor)
            self.alias_editors[key] = editor
            row.addWidget(group)
        layout.addLayout(row, 1)
        note = QLabel('Matches ignore case. Word aliases match whole words or phrases.\nClick Save settings to apply edits. Chat keeps the original text.')
        note.setWordWrap(True)
        layout.addWidget(note)

    def build_setup(self):
        layout = self.page('Connection && setup', scroll=True)
        startup = QGroupBox('Startup and window behavior')
        form = QFormLayout(startup)
        form.addRow(self.check('auto_connect', 'Auto-connect to the saved channel when the app starts'))
        form.addRow(self.check('minimize_to_tray', 'Minimize to the system tray'))
        self.close_action = QComboBox()
        self.close_action.addItems(['Ask every time', 'Close app', 'Minimize to tray'])
        if self.values['close_action'] in ['Ask every time', 'Close app', 'Minimize to tray']:
            self.close_action.setCurrentText(self.values['close_action'])
        self.inputs['close_action'] = self.close_action
        form.addRow('When closing the window:', self.close_action)
        note = QLabel('Chat and speech continue in the tray. Tray menu → Quit always exits.')
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(startup)
        appearance = QGroupBox('Appearance')
        form = QFormLayout(appearance)
        self.appearance = QComboBox()
        self.appearance.addItems(['Plasma (system)', 'Breeze Light', 'Breeze Dark'])
        self.appearance.setCurrentText(self.values['appearance'])
        self.appearance.currentTextChanged.connect(self.apply_appearance)
        self.inputs['appearance'] = self.appearance
        form.addRow('Style:', self.appearance)
        layout.addWidget(appearance)
        engine = QGroupBox('Local speech engine')
        form = QFormLayout(engine)
        form.addRow('Piper executable:', self.entry('piper'))
        layout.addWidget(engine)
        login = QGroupBox('Optional Twitch login')
        form = QFormLayout(login)
        note = QLabel('Leave both fields empty for anonymous chat. Tokens are never saved.')
        note.setWordWrap(True)
        form.addRow(note)
        self.username, self.token = QLineEdit(), QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('Username:', self.username)
        form.addRow('OAuth token:', self.token)
        layout.addWidget(login)
        layout.addStretch()

    def apply_appearance(self, name):
        application = QApplication.instance()
        style_name = 'Breeze' if 'breeze' in {name.lower() for name in QStyleFactory.keys()} else 'Fusion'
        if application.style().objectName().lower() != style_name.lower():
            application.setStyle(style_name)
        config = configparser.ConfigParser(interpolation=None, strict=False)
        if name == 'Plasma (system)':
            config.read(Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'kdeglobals')
        if not config.has_section('Colors:Window'):
            scheme = 'BreezeDark' if name == 'Breeze Dark' else 'BreezeLight'
            config.read(f'/usr/share/color-schemes/{scheme}.colors')
        palette = application.style().standardPalette()
        roles = {
            QPalette.ColorRole.Window: ('Window', 'BackgroundNormal'),
            QPalette.ColorRole.WindowText: ('Window', 'ForegroundNormal'),
            QPalette.ColorRole.Base: ('View', 'BackgroundNormal'),
            QPalette.ColorRole.AlternateBase: ('View', 'BackgroundAlternate'),
            QPalette.ColorRole.Text: ('View', 'ForegroundNormal'),
            QPalette.ColorRole.Button: ('Button', 'BackgroundNormal'),
            QPalette.ColorRole.ButtonText: ('Button', 'ForegroundNormal'),
            QPalette.ColorRole.Highlight: ('Selection', 'BackgroundNormal'),
            QPalette.ColorRole.HighlightedText: ('Selection', 'ForegroundNormal'),
            QPalette.ColorRole.ToolTipBase: ('Tooltip', 'BackgroundNormal'),
            QPalette.ColorRole.ToolTipText: ('Tooltip', 'ForegroundNormal'),
            QPalette.ColorRole.Link: ('View', 'ForegroundLink'),
            QPalette.ColorRole.LinkVisited: ('View', 'ForegroundVisited'),
            QPalette.ColorRole.PlaceholderText: ('View', 'ForegroundInactive'),
        }
        for role, (section, key) in roles.items():
            value = config.get('Colors:' + section, key, fallback='')
            if re.fullmatch(r'\d+,\d+,\d+', value):
                palette.setColor(role, QColor(*map(int, value.split(','))))
        for role in [QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText]:
            palette.setColor(QPalette.ColorGroup.Disabled, role, palette.color(QPalette.ColorRole.PlaceholderText))
        application.setPalette(palette)
        icon_theme = 'breeze-dark' if palette.color(QPalette.ColorRole.Window).lightness() < 128 else 'breeze'
        if name == 'Plasma (system)':
            icon_theme = config.get('Icons', 'Theme', fallback=icon_theme)
        QIcon.setThemeName(icon_theme)
        self.values['appearance'] = name

    def add_voice(self, path):
        if path in self.voice_paths.values():
            return
        label = Path(path).stem
        if label in self.voice_paths:
            label = path
        self.voice_paths[label] = path
        self.voice_groups[label] = voice_language_group(path)

    def sync_voice(self):
        path = self.values['model']
        label = next((k for k, v in self.voice_paths.items() if v == path), '')
        group = self.voice_groups.get(label, '')
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItems(sorted(set(self.voice_groups.values()), key=str.casefold))
        self.language_combo.setCurrentText(group)
        self.language_combo.blockSignals(False)
        self.fill_voices(group, label)
        if label:
            self.last_voice[group] = label
        self.model_hint.setText(path)

    def fill_voices(self, group, selected=''):
        labels = sorted((k for k in self.voice_paths if self.voice_groups[k] == group), key=str.casefold)
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItems(labels)
        if selected in labels:
            self.voice_combo.setCurrentText(selected)
        self.voice_combo.blockSignals(False)

    def select_language(self, group):
        self.fill_voices(group, self.last_voice.get(group, ''))
        self.select_voice(self.voice_combo.currentText())

    def select_voice(self, label):
        if label not in self.voice_paths:
            return
        self.values['model'] = self.voice_paths[label]
        self.model_hint.setText(self.values['model'])
        self.inputs['speaker'].setValue(0)
        self.last_voice[self.language_combo.currentText()] = label

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose Piper voice', '', 'Piper voices (*.onnx)')
        if path:
            self.add_voice(path)
            self.values['model'] = path
            self.inputs['speaker'].setValue(0)
            self.sync_voice()

    def raw_settings(self):
        values = self.values.copy()
        for key, widget in self.inputs.items():
            if isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox, QSlider)):
                values[key] = str(widget.value())
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            else:
                values[key] = widget.text()
        values.update(self.aliases)
        return values

    def settings(self, aliases=None):
        s = self.raw_settings()
        s.update(aliases or {})
        if s['prefix_only'] and (not s['message_prefix'] or s['message_prefix'].isspace()):
            raise ValueError('Enter a non-empty message prefix, such as %.')
        for key in ('nickname_aliases', 'word_aliases'):
            s[key] = parse_aliases(s[key])
        s.update(speed=float(s['speed']), speaker=int(s['speaker']), limit=int(s['limit']), nickname_timeout=float(s['nickname_timeout']))
        if not .5 <= s['speed'] <= 2 or not 20 <= s['limit'] <= 1000 or not 1 <= s['nickname_timeout'] <= 3600:
            raise ValueError('Check speed, message length, and nickname timeout.')
        if s['engine'] == 'StyleTTS2 Ukrainian':
            s['style_python'] = shutil.which(os.path.expanduser(s['style_python'])) or ''
            if not s['style_python']:
                raise ValueError('Install StyleTTS2 using README, then choose its Python executable in Voice & filters.')
            if s['style_voice'] not in CATALOG['voices'] or s['style_device'] not in ('Auto', 'CPU', 'CUDA'):
                raise ValueError('Choose a valid StyleTTS2 voice and device.')
        else:
            s['model'] = str(Path(s['model']).expanduser())
            s['piper'] = shutil.which(os.path.expanduser(s['piper'])) or ''
            if not s['piper']:
                raise ValueError('Choose an installed Piper executable in Connection & setup.')
            if not Path(s['model']).is_file() or not Path(s['model'] + '.json').is_file():
                raise ValueError('Choose a voice with its matching .onnx.json config.')
            config = json.loads(Path(s['model'] + '.json').read_text())
            if not 0 <= s['speaker'] < config.get('num_speakers', 1):
                raise ValueError('Speaker ID is not available in this voice. Try 0.')
        if not (shutil.which('aplay') or shutil.which('ffplay')):
            raise ValueError('Install aplay or ffplay for audio playback.')
        return s

    def save(self):
        temporary = self.settings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.raw_settings(), indent=2))
        temporary.replace(self.settings_path)

    def apply_settings(self):
        changes = {key: editor.toPlainText() for key, editor in self.alias_editors.items()}
        previous = self.aliases.copy()
        try:
            self.settings(changes)
            self.aliases = changes
            self.save()
        except (ValueError, OSError) as exc:
            self.aliases = previous
            self.error(exc)
            return
        self.saved_status.setText('Settings saved · Alias edits applied')

    def error(self, error):
        QMessageBox.warning(self, 'Check settings', str(error))

    def emit(self, kind, value, session=None):
        try:
            self.events.put_nowait((kind, value, session))
        except queue.Full:
            pass

    def connect_chat(self, automatic=False):
        if self.chat:
            if automatic:
                return
            self.chat.close()
            self.chat = None
            self.session += 1
            self.speaker.clear()
            self.status.setText('Disconnected')
            self.connect_button.setText('Connect')
            return
        try:
            self.settings()
            channel = channel_name(self.channel.text())
            username, token = self.username.text().strip().lower(), self.token.text().strip()
            if bool(username) != bool(token) or (username and not re.fullmatch(r'[a-z0-9_]{1,25}', username)) or re.search(r'\s', token):
                raise ValueError('Supply both a Twitch username and a token without whitespace, or leave both empty.')
            self.save()
        except (ValueError, OSError) as exc:
            if automatic:
                self.status.setText(f'Auto-connect could not start: {exc}')
                self.append(self.status.text())
            else:
                self.error(exc)
            return
        self.session += 1
        session = self.session
        self.seen.clear()
        self.chat = Chat(channel, lambda k, v: self.emit(k, v, session), username, token)
        self.chat.start()
        self.connect_button.setText('Disconnect')

    def test_voice(self):
        try:
            settings = self.settings()
            self.save()
            body = speech_text(self.sample.text(), settings)
            if body:
                self.speaker.enqueue(body, settings, self.sample_name.text().strip() or 'Piper')
                if self.speaker.paused:
                    self.status.setText('Test queued · Resume playback to hear it')
            else:
                self.status.setText('Test message was empty or excluded by a filter')
        except (ValueError, OSError) as exc:
            self.error(exc)

    def pause(self):
        with self.speaker.cv:
            self.speaker.paused = not self.speaker.paused
            if self.speaker.paused:
                self.speaker.skip()
            self.pause_button.setText('Resume' if self.speaker.paused else 'Pause')
            self.pause_button.setIcon(QIcon.fromTheme('media-playback-start' if self.speaker.paused else 'media-playback-pause'))
            self.speaker.cv.notify_all()

    def append(self, text, name=None):
        prefix = f'<b>{html.escape(name)}:</b> ' if name else ''
        self.log.append(prefix + html.escape(text))

    def poll(self):
        self.load_label.setText(self.resources.latest)
        for _ in range(100):
            try:
                kind, value, session = self.events.get_nowait()
            except queue.Empty:
                break
            if session is not None and session != self.session:
                continue
            if kind == 'chat':
                name, text, message_id, login = value
                if message_id and message_id in self.seen:
                    continue
                if message_id:
                    self.seen.append(message_id)
                self.append(text, name)
                try:
                    settings = self.settings()
                    ignored = {x.strip().casefold() for x in settings['ignored'].split(',')}
                    if login.casefold() in ignored or name.casefold() in ignored:
                        continue
                    body = speech_text(text, settings)
                    if body and not self.speaker.paused:
                        self.speaker.enqueue(body, settings, name)
                except (ValueError, OSError) as exc:
                    self.status.setText(str(exc))
            elif kind == 'status':
                self.status.setText(value)
            elif kind == 'engine':
                self.engine_message, busy = value
                if busy and self.engine_started is None:
                    self.engine_started = time.monotonic()
                elif not busy:
                    self.engine_started = None
                self.engine_progress.setVisible(busy)
            elif kind == 'speech':
                self.speaking = value
            elif kind == 'moderation':
                self.speaker.clear()
                self.append('Moderation event: speech queue cleared.')
            elif kind in ('error', 'notice'):
                self.append(f'{kind.title()}: {value}')
        elapsed = f' · {int(time.monotonic() - self.engine_started)}s elapsed' if self.engine_started is not None else ''
        self.engine_status.setText(self.engine_message + elapsed)
        self.now.setText('Paused' if self.speaker.paused else ('Speaking: ' + self.speaking[:140] if self.speaking else 'Speech idle'))
        self.queue_status.setText(f'{len(self.speaker.items)} / 30 queued')

    def startup_connect(self):
        if self._closing or self._startup_attempted:
            return
        self._startup_attempted = True
        if not self.inputs['auto_connect'].isChecked() or self.chat:
            return
        if not self.channel.text().strip():
            self.status.setText('Auto-connect skipped · Set a channel and save settings first')
            return
        self.connect_chat(automatic=True)

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('Twitch × Piper')
        self.tray_menu = QMenu(self)
        self.tray_toggle = self.tray_menu.addAction('Show / hide window')
        self.tray_toggle.triggered.connect(self.toggle_tray_window)
        self.tray_pause = self.tray_menu.addAction('Pause speech')
        self.tray_pause.triggered.connect(self.pause)
        self.tray_menu.addAction('Skip message', self.speaker.skip)
        self.tray_menu.addAction('Clear queue', self.speaker.clear)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction('Quit', self.quit_app)
        self.tray_menu.aboutToShow.connect(self.refresh_tray_menu)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def refresh_tray_menu(self):
        self.tray_toggle.setText('Hide window' if self.isVisible() and not self.isMinimized() else 'Show window')
        self.tray_pause.setText('Resume speech' if self.speaker.paused else 'Pause speech')

    def hide_to_tray(self):
        if self._closing:
            return False
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.status.setText('System tray unavailable · Window remains accessible in the taskbar')
            return False
        if not self.isMinimized():
            self._restore_maximized = self.isMaximized()
        self.tray.show()
        self.hide()
        return True

    def restore_window(self):
        if self._restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def toggle_tray_window(self):
        if self.isVisible() and not self.isMinimized():
            self.hide_to_tray()
        else:
            self.restore_window()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_tray_window()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.restore_window()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and not self._closing and 'minimize_to_tray' in self.inputs
                and self.inputs['minimize_to_tray'].isChecked()):
            self._restore_maximized = bool(event.oldState() & Qt.WindowState.WindowMaximized)
            QTimer.singleShot(0, self.minimize_to_tray_if_needed)

    def minimize_to_tray_if_needed(self):
        if not self._closing and self.isMinimized() and self.inputs['minimize_to_tray'].isChecked():
            self.hide_to_tray()

    def quit_app(self):
        self._force_quit = True
        self.shutdown()
        if self._close_dialog is not None:
            self._close_dialog.reject()
        self.close()
        QApplication.instance().quit()

    def make_close_dialog(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Close Twitch × Piper?')
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText('Close the app or keep reading chat in the tray?')
        available = QSystemTrayIcon.isSystemTrayAvailable()
        dialog.setInformativeText(
            'Minimize to tray keeps chat and speech running. Change this choice later in Connection & setup.'
            if available else
            'The system tray is unavailable. You can close the app or cancel. Your tray preference will be kept.')
        close_button = dialog.addButton('Close app', QMessageBox.ButtonRole.DestructiveRole)
        tray_button = dialog.addButton('Minimize to tray', QMessageBox.ButtonRole.ActionRole)
        tray_button.setEnabled(available)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(tray_button if available else cancel)
        dialog.setEscapeButton(cancel)
        dialog.remember_checkbox = QCheckBox('Remember my choice', dialog)
        dialog.setCheckBox(dialog.remember_checkbox)
        return dialog, close_button, tray_button

    def ask_close_action(self):
        dialog, close_button, tray_button = self.make_close_dialog()
        self._close_dialog = dialog
        try:
            dialog.exec()
            clicked = dialog.clickedButton()
            action = ('Close app' if clicked is close_button else
                      'Minimize to tray' if clicked is tray_button else None)
            return action, dialog.remember_checkbox.isChecked()
        finally:
            self._close_dialog = None
            dialog.deleteLater()

    def remember_close_action(self, action):
        previous = self.close_action.currentText()
        self.close_action.setCurrentText(action)
        try:
            self.save()
        except OSError as exc:
            self.close_action.setCurrentText(previous)
            self.restore_window()
            self.error(exc)
            return False
        return True

    def closeEvent(self, event):
        if self._closing:
            event.accept()
            return
        if self._close_prompt_open and not self._force_quit:
            event.ignore()
            return
        if not self._closing and not self._force_quit:
            action = self.close_action.currentText()
            remember = False
            if action == 'Ask every time' or (action == 'Minimize to tray' and not QSystemTrayIcon.isSystemTrayAvailable()):
                self._close_prompt_open = True
                try:
                    action, remember = self.ask_close_action()
                finally:
                    self._close_prompt_open = False
            if self._closing:
                event.accept()
                return
            if action is None:
                event.ignore()
                return
            if action == 'Minimize to tray':
                if self.hide_to_tray() and remember:
                    self.remember_close_action(action)
                event.ignore()
                return
            if remember and not self.remember_close_action(action):
                event.ignore()
                return
        self.shutdown()
        event.accept()

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.startup_timer.stop()
        self.resources.close()
        self.tray.hide()
        self.timer.stop()
        if self.chat:
            self.chat.close()
        self.speaker.close()
        try:
            self.save()
        except OSError:
            pass


def main():
    application = QApplication(sys.argv)
    application.setApplicationName('Twitch Piper')
    application.setDesktopFileName('twitch-piper')
    application.setWindowIcon(QIcon(str(BASE / 'assets' / 'twitch-piper.png')))
    application.setOrganizationName('TwitchPiper')
    window = Window()
    application.aboutToQuit.connect(window.shutdown)
    window.show()
    return application.exec()
