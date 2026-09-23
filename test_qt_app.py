import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtGui import QPalette
from PySide6.QtCore import QTimer
from qt_app import Window


class BreezeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'settings.json'
        self.window = Window(self.path)
        self.window.show()
        self.application.processEvents()

    def tearDown(self):
        self.window._force_quit = True
        self.window.close()
        self.window.speaker.join(2)
        self.application.processEvents()
        self.temp.cleanup()

    def test_native_breeze_and_palette_switch(self):
        self.assertEqual(self.application.style().objectName().lower(), 'breeze')
        self.window.appearance.setCurrentText('Breeze Light')
        light = self.application.palette().color(QPalette.ColorRole.Window).lightness()
        self.window.appearance.setCurrentText('Breeze Dark')
        dark = self.application.palette().color(QPalette.ColorRole.Window).lightness()
        self.assertGreater(light, dark)
        self.assertEqual(self.window.tabs.count(), 4)

    def test_settings_aliases_and_secret_exclusion(self):
        self.window.alias_editors['nickname_aliases'].setPlainText('viewer = Alex')
        self.window.alias_editors['word_aliases'].setPlainText('gg = good game')
        self.window.inputs['nickname_timeout'].setValue(25)
        self.window.appearance.setCurrentText('Breeze Dark')
        self.window.token.setText('oauth:do-not-save')
        self.window.apply_settings()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['nickname_aliases'], 'viewer = Alex')
        self.assertEqual(float(saved['nickname_timeout']), 25)
        self.assertEqual(saved['appearance'], 'Breeze Dark')
        self.assertNotIn('do-not-save', self.path.read_text())
        self.assertNotIn('token', saved)

    def test_volume_slider_updates_speech_and_persists(self):
        self.window.volume_slider.setValue(37)
        self.assertEqual(self.window.speaker.volume, 37)
        self.assertEqual(self.window.volume_value.text(), '37%')
        self.window.save()
        self.assertEqual(int(json.loads(self.path.read_text())['volume']), 37)
        restored = Window(self.path)
        try:
            self.assertEqual(restored.volume_slider.value(), 37)
            self.assertEqual(restored.speaker.volume, 37)
        finally:
            restored._force_quit = True
            restored.close()
            restored.speaker.join(2)
        self.window.volume_slider.setValue(0)
        self.assertEqual(self.window.speaker.volume, 0)
        self.assertEqual(self.window.volume_value.text(), 'Muted')

    def test_auto_connect_saved_channel_runs_once(self):
        self.window.inputs['auto_connect'].setChecked(True)
        self.window.channel.setText('example')
        self.window.save()
        with patch('qt_app.Chat') as chat:
            restored = Window(self.path)
            try:
                self.application.processEvents()
                self.assertEqual(chat.call_args.args[0], 'example')
                chat.return_value.start.assert_called_once()
                restored.startup_connect()
                chat.return_value.start.assert_called_once()
                self.assertEqual(restored.connect_button.text(), 'Disconnect')
            finally:
                restored._force_quit = True
                restored.close()
                restored.speaker.join(2)

    def test_auto_connect_disabled_and_invalid_settings(self):
        with patch.object(self.window, 'connect_chat') as connect:
            self.window._startup_attempted = False
            self.window.startup_connect()
            connect.assert_not_called()
        self.window.inputs['auto_connect'].setChecked(True)
        self.window.channel.setText('invalid channel!')
        self.window._startup_attempted = False
        with patch('qt_app.Chat') as chat, patch.object(self.window, 'error') as error:
            self.window.startup_connect()
            chat.assert_not_called()
            error.assert_not_called()
            self.assertIn('Auto-connect could not start', self.window.status.text())

    def test_tray_hide_restore_and_unavailable_fallback(self):
        with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
            self.assertFalse(self.window.hide_to_tray())
            self.assertTrue(self.window.isVisible())
        with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True), patch.object(self.window.tray, 'show'):
            self.assertTrue(self.window.hide_to_tray())
            self.assertFalse(self.window.isVisible())
            self.assertTrue(self.window.timer.isActive())
            self.assertFalse(self.window.speaker.stopped)
            self.window.toggle_tray_window()
            self.assertTrue(self.window.isVisible())
            self.window.inputs['minimize_to_tray'].setChecked(True)
            self.window.showMinimized()
            self.application.processEvents()
            self.assertFalse(self.window.isVisible())
            self.window.restore_window()
            self.window.refresh_tray_menu()
            self.assertEqual(self.window.tray_toggle.text(), 'Hide window')
            self.window.pause()
            self.window.refresh_tray_menu()
            self.assertEqual(self.window.tray_pause.text(), 'Resume speech')
        self.window.save()
        self.assertTrue(json.loads(self.path.read_text())['minimize_to_tray'])

    def test_close_dialog_remembers_tray_and_can_reset_in_settings(self):
        def choose_tray():
            dialog = self.application.activeModalWidget()
            dialog.checkBox().setChecked(True)
            next(button for button in dialog.buttons() if button.text() == 'Minimize to tray').click()
        with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True), patch.object(self.window.tray, 'show'):
            QTimer.singleShot(0, choose_tray)
            self.window.close()
            self.assertFalse(self.window.isVisible())
            self.assertFalse(self.window.speaker.stopped)
            self.assertEqual(json.loads(self.path.read_text())['close_action'], 'Minimize to tray')
            self.window.restore_window()
            with patch.object(self.window, 'ask_close_action') as ask:
                self.window.close()
                ask.assert_not_called()
            self.window.restore_window()
            self.window.close_action.setCurrentText('Ask every time')
            self.window.apply_settings()
            self.assertEqual(json.loads(self.path.read_text())['close_action'], 'Ask every time')

    def test_cancel_and_unremembered_tray_keep_prompt_enabled(self):
        with patch.object(self.window, 'ask_close_action', return_value=(None, True)):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.close_action.currentText(), 'Ask every time')
        with patch.object(self.window, 'ask_close_action', return_value=('Minimize to tray', False)), patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=True), patch.object(self.window.tray, 'show'):
            self.window.close()
        self.assertFalse(self.window.speaker.stopped)
        self.assertEqual(self.window.close_action.currentText(), 'Ask every time')
        self.window.restore_window()

    def test_remember_close_and_missing_tray_fallback(self):
        self.window.close_action.setCurrentText('Minimize to tray')
        with patch.object(QSystemTrayIcon, 'isSystemTrayAvailable', return_value=False):
            dialog, close, tray = self.window.make_close_dialog()
            self.assertFalse(tray.isEnabled())
            dialog.deleteLater()
            with patch.object(self.window, 'ask_close_action', return_value=(None, False)) as ask:
                self.window.close()
                ask.assert_called_once()
            self.assertTrue(self.window.isVisible())
            self.assertEqual(self.window.close_action.currentText(), 'Minimize to tray')
            with patch.object(self.window, 'ask_close_action', return_value=('Close app', True)):
                self.window.close()
        self.assertTrue(self.window.speaker.stopped)
        self.assertEqual(json.loads(self.path.read_text())['close_action'], 'Close app')

    def test_quit_from_hidden_window_stops_workers(self):
        self.window.hide()
        with patch.object(self.application, 'quit') as quit_app:
            self.window.quit_app()
            quit_app.assert_called_once()
        self.assertTrue(self.window.speaker.stopped)
        self.assertFalse(self.window.timer.isActive())
        self.assertTrue(self.window._closing)

    def test_ignored_login_with_different_display_name_is_not_read(self):
        self.window.inputs['ignored'].setText('SomeUser')
        with patch.object(self.window.speaker, 'enqueue') as enqueue:
            self.window.emit('chat', ('視聴者', 'hello', 'login-test', 'someuser'), self.window.session)
            self.window.poll()
            enqueue.assert_not_called()
            self.assertIn('視聴者: hello', self.window.log.toPlainText())
            self.window.emit('chat', ('Different viewer', 'hello', 'login-test-2', 'allowed'), self.window.session)
            self.window.poll()
            enqueue.assert_called_once()
            self.assertEqual(enqueue.call_args.args[2], 'Different viewer')

    def test_tray_quit_during_close_dialog_runs_shutdown_once(self):
        self.window.chat = MagicMock()
        self.window.inputs['volume'].setValue(42)
        active_process = MagicMock()
        active_process.poll.return_value = None
        self.window.speaker.process = active_process
        def trigger_quit():
            self.window.tray_menu.actions()[-1].trigger()
        with patch.object(self.application, 'quit') as quit_app:
            QTimer.singleShot(0, trigger_quit)
            self.window.close()
            quit_app.assert_called_once()
        self.assertTrue(self.window._closing)
        self.assertTrue(self.window.speaker.stopped)
        self.assertFalse(self.window.timer.isActive())
        self.assertFalse(self.window.startup_timer.isActive())
        self.assertFalse(self.window.tray.isVisible())
        active_process.terminate.assert_called_once()
        self.window.chat.close.assert_called_once()
        self.assertEqual(int(json.loads(self.path.read_text())['volume']), 42)
        self.window.shutdown()
        self.window.chat.close.assert_called_once()
        active_process.terminate.assert_called_once()
        self.window.speaker.process = None

    def test_voice_group_and_browse_sync_preserve_speaker(self):
        combo = self.window.language_combo
        if combo.count() < 2:
            self.skipTest('Multiple installed voice languages required')
        original = self.window.values['model']
        original_group = combo.currentText()
        combo.setCurrentIndex((combo.currentIndex() + 1) % combo.count())
        group = combo.currentText()
        for i in range(self.window.voice_combo.count()):
            label = self.window.voice_combo.itemText(i)
            self.assertEqual(self.window.voice_groups[label], group)
        combo.setCurrentText(original_group)
        self.assertEqual(self.window.values['model'], original)
        self.window.inputs['speaker'].setValue(7)
        self.window.sync_voice()
        self.assertEqual(self.window.inputs['speaker'].value(), 7)

    def test_chat_uses_filters_and_preserves_author_for_repeat_logic(self):
        self.window.inputs['strip_percent'].setChecked(True)
        self.window.aliases['word_aliases'] = 'gg = good game'
        with patch.object(self.window.speaker, 'enqueue') as enqueue:
            self.window.emit('chat', ('Viewer', '%gg', 'unique', 'viewer'), self.window.session)
            self.window.poll()
            args = enqueue.call_args.args
            self.assertEqual(args[0], 'good game')
            self.assertEqual(args[2], 'Viewer')
            self.assertTrue(args[1]['skip_repeat_names'])
            self.window.emit('chat', ('Viewer', '%gg', 'unique', 'viewer'), self.window.session)
            self.window.poll()
            self.assertEqual(enqueue.call_count, 1)
        self.window.append('<img src="bad">', '<b>name</b>')
        self.assertIn('<b>name</b>: <img src="bad">', self.window.log.toPlainText())

    def test_styletts2_settings_without_piper(self):
        import sys
        self.window.engine_combo.setCurrentText('StyleTTS2 Ukrainian')
        self.window.inputs['style_python'].setText(sys.executable)
        self.window.inputs['piper'].setText('/missing-piper')
        self.window.values['model'] = '/missing-model'
        self.window.inputs['style_voice'].setCurrentIndex(5)
        self.window.inputs['style_device'].setCurrentText('CPU')
        self.assertTrue(self.window.inputs['style_numbers'].isChecked())
        self.window.inputs['style_numbers'].setChecked(False)
        self.assertTrue(self.window.piper_group.isHidden())
        self.assertFalse(self.window.style_group.isHidden())
        settings = self.window.settings()
        self.assertEqual(settings['engine'], 'StyleTTS2 Ukrainian')
        self.assertEqual(settings['style_device'], 'CPU')
        self.window.save()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['style_voice'], settings['style_voice'])
        self.assertFalse(saved['style_numbers'])
        self.window.engine_combo.setCurrentText('Piper')
        with self.assertRaisesRegex(ValueError, 'Piper executable'):
            self.window.settings()

    def test_styletts2_missing_environment(self):
        self.window.engine_combo.setCurrentText('StyleTTS2 Ukrainian')
        self.window.inputs['style_python'].setText('/missing-style-python')
        with self.assertRaisesRegex(ValueError, 'Install StyleTTS2'):
            self.window.settings()

    def test_engine_progress_and_terminal_states(self):
        self.window.emit('engine', ('Loading model', True))
        self.window.poll()
        self.assertFalse(self.window.engine_progress.isHidden())
        started = self.window.engine_started
        self.window.engine_started -= 5
        self.window.poll()
        self.assertIn('5s elapsed', self.window.engine_status.text())
        self.window.emit('engine', ('Generating speech · part 1/2', True))
        self.window.poll()
        self.assertEqual(self.window.engine_started, started - 5)
        for state in ('Playing audio', 'Ready', 'Stopped', 'Engine error'):
            self.window.emit('engine', (state, False))
            self.window.poll()
            self.assertTrue(self.window.engine_progress.isHidden())
            self.assertIsNone(self.window.engine_started)
            self.assertEqual(self.window.engine_status.text(), state)


if __name__ == '__main__':
    unittest.main()
