import os
import subprocess
import sys
import tempfile
import unittest
from PySide6.QtWidgets import QApplication
from single_instance import SingleInstance

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

class InstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_second_launch_activates_and_lock_releases(self):
        with tempfile.TemporaryDirectory() as root:
            first = SingleInstance(root)
            activated = []
            try:
                self.assertTrue(first.owned)
                first.listen(lambda: activated.append(True))
                second = SingleInstance(root)
                try:
                    self.assertFalse(second.owned)
                    self.assertTrue(second.notify_existing())
                    self.app.processEvents()
                    self.assertEqual(activated, [True])
                finally:
                    second.close()
            finally:
                first.close()
            third = SingleInstance(root)
            self.assertTrue(third.owned)
            third.close()

    def test_crash_does_not_leave_locked_instance(self):
        with tempfile.TemporaryDirectory() as root:
            code = 'import os,sys; from single_instance import SingleInstance; g=SingleInstance(sys.argv[1]); assert g.owned; os._exit(0)'
            subprocess.run([sys.executable, '-c', code, root], check=True)
            guard = SingleInstance(root)
            try:
                self.assertTrue(guard.owned)
            finally:
                guard.close()
