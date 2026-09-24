import json
from pathlib import Path
import tempfile
import unittest
from PySide6.QtWidgets import QApplication
from localization import Translator, LocalizedCombo, languages
from qt_app import Window

class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fallback_and_placeholders(self):
        tr = Translator('uk')
        self.assertEqual(tr('Connect'), 'Підключити')
        self.assertEqual(tr('7 / 30 queued'), 'У черзі: 7 / 30')
        self.assertEqual(tr('StyleTTS2 · Generating speech on CUDA · part 1/2'), 'StyleTTS2 · Синтез мовлення на CUDA · частина 1/2')
        self.assertEqual(tr('Unknown external error'), 'Unknown external error')
        self.assertEqual(Translator('nonexistent')('Connect'), 'Connect')
        self.assertIn('uk', languages())

    def test_combo_uses_stable_values_and_signals(self):
        combo = LocalizedCombo(Translator('uk'))
        combo.addItems(['Ask every time', 'Close app'])
        changes=[]
        combo.stableTextChanged.connect(changes.append)
        combo.setCurrentText('Close app')
        self.assertEqual(combo.itemText(1), 'Закрити застосунок')
        self.assertEqual(combo.currentText(), 'Close app')
        self.assertEqual(changes, ['Close app'])

    def test_ukrainian_window_saves_english_setting_ids(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json'
            path.write_text(json.dumps({'ui_language':'uk','close_action':'Close app','appearance':'Breeze Light'}))
            w=Window(path)
            try:
                self.assertEqual(w.tabs.tabText(0), 'Чат')
                self.assertEqual(w.connect_button.text(), 'Підключити')
                self.assertEqual(w.close_action.currentText(), 'Close app')
                self.assertEqual(w.inputs['ui_language'].currentText(), 'uk')
                w.emit('engine', ('StyleTTS2 · Ready',False));w.poll()
                self.assertEqual(w.engine_status.text(), 'StyleTTS2 · Готово')
                w.save(); saved=json.loads(path.read_text())
                self.assertEqual(saved['close_action'], 'Close app')
                self.assertEqual(saved['ui_language'], 'uk')
            finally:
                w.shutdown();w.close();w.speaker.join(2)
