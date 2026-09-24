"""JSON interface catalogs with English fallback and stable settings values."""
import json
from pathlib import Path
import re
from string import Formatter
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox

ROOT = Path(__file__).with_name('locales')


def languages():
    result = {'en': 'English'}
    for path in sorted(ROOT.glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('language') == path.stem and data.get('name'):
                result[path.stem] = data['name']
        except (OSError, ValueError):
            pass
    return result


class Translator:
    def __init__(self, language='en'):
        self.messages = {}
        if language in languages() and language != 'en':
            try:
                self.messages = json.loads((ROOT / (language + '.json')).read_text(encoding='utf-8'))['messages']
            except (OSError, ValueError, KeyError):
                pass
        self.patterns = []
        for source, target in self.messages.items():
            if not target or '{' not in source:
                continue
            parts = list(Formatter().parse(source))
            fields = [field for _, field, _, _ in parts if field is not None]
            pattern = ''.join(re.escape(literal) + ('(.+?)' if field is not None else '')
                              for literal, field, _, _ in parts)
            self.patterns.append((re.compile('^' + pattern + '$', re.S), fields, target))

    def __call__(self, text):
        if not isinstance(text, str):
            return text
        if self.messages.get(text):
            return self.messages[text]
        if ' · ' in text:
            return ' · '.join(self(part) for part in text.split(' · '))
        for pattern, fields, target in self.patterns:
            match = pattern.fullmatch(text)
            if match:
                try:
                    return target.format(**dict(zip(fields, match.groups())))
                except (KeyError, ValueError):
                    return text
        return text


class LocalizedCombo(QComboBox):
    stableTextChanged = Signal(str)

    def __init__(self, translate):
        super().__init__()
        self.translate = translate
        self.currentIndexChanged.connect(lambda _: self.stableTextChanged.emit(self.currentText()))

    def addItems(self, items):
        for value in items:
            self.addItem(self.translate(value), value)

    def currentText(self):
        value = self.currentData()
        return value if value is not None else super().currentText()

    def setCurrentText(self, value):
        index = self.findData(value)
        if index >= 0:
            self.setCurrentIndex(index)
        else:
            super().setCurrentText(value)
