import logging
import os
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath("py_modules"))
from smb_download_power import POWER_KEYS, PowerJournal, read_power_settings


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.config = os.path.join(self.home, ".local/share/Steam/config/config.vdf")
        os.makedirs(os.path.dirname(self.config))
        self.write([123, 456, 789, 0])

    def write(self, values):
        with open(self.config, "w", encoding="utf-8") as stream:
            stream.write('"InstallConfigStore" {\n')
            for key, value in zip(POWER_KEYS, values):
                stream.write(f'"{key}" "{value}"\n')
            stream.write('}\n')

    def test_original_values_survive_restart_and_repeated_begin(self):
        journal = PowerJournal(self.home, self.home)
        self.assertEqual(journal.begin(), [123, 456, 789, 0])
        self.write([0, 0, 0, 0])
        self.assertEqual(journal.begin(), [123, 456, 789, 0])
        restarted = PowerJournal(self.home, self.home)
        self.assertEqual(restarted.begin(), [123, 456, 789, 0])
        restarted.report(restored=True)
        self.assertIsNone(PowerJournal(self.home, self.home).snapshot)

    def test_missing_or_ambiguous_values_never_create_recovery_record(self):
        self.write([123])
        journal = PowerJournal(self.home, self.home)
        with self.assertRaises(RuntimeError):
            journal.begin()
        self.assertFalse(os.path.exists(journal.path))
        self.write([123, 456, 789, 0])
        with open(self.config, "a") as stream:
            stream.write(f'"{POWER_KEYS[0]}" "999"')
        with self.assertRaises(RuntimeError):
            journal.begin()

    def test_only_running_worker_requires_awake(self):
        service = SimpleNamespace(lock=threading.RLock(), config={}, closed=False,
                                  active_task_id="task", thread=SimpleNamespace(is_alive=lambda: True))
        journal = PowerJournal(self.home, self.home)
        self.assertTrue(journal.state(service)["wanted"])
        service.config["keep_awake"] = False
        self.assertFalse(journal.state(service)["wanted"])
        service.config["keep_awake"] = True
        service.closed = True
        self.assertFalse(journal.state(service)["wanted"])
        service.closed = False
        service.active_task_id = None
        self.assertFalse(journal.state(service)["wanted"])

    def test_config_is_read_only_and_user_zero_is_preserved(self):
        with open(self.config, "rb") as stream:
            before = stream.read()
        journal = PowerJournal(self.home, self.home)
        journal.begin()
        journal.report(active=True)
        journal.report(error="test")
        with open(self.config, "rb") as stream:
            self.assertEqual(stream.read(), before)
        self.assertEqual(journal.snapshot, [123, 456, 789, 0])


if __name__ == "__main__":
    unittest.main()
