import os
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
from smb_download_dependencies import dependency_status, require_smbclient


class DependencyTests(unittest.TestCase):
    def test_import_error_reports_actual_missing_module(self):
        with patch("smb_download_dependencies.importlib.import_module", side_effect=ModuleNotFoundError("No module named '_cffi_backend'")):
            result = dependency_status()
            self.assertFalse(result["smb_available"])
            self.assertIn("_cffi_backend", result["smb_error"])
            self.assertIn("Traceback", result["traceback"])
            self.assertIn("python_version", result)
            with self.assertLogs("SMB-Download.SMB", level="ERROR"):
                with self.assertRaisesRegex(RuntimeError, "_cffi_backend"):
                    require_smbclient()

    def test_native_loader_error_is_diagnosable(self):
        with patch("smb_download_dependencies.importlib.import_module", side_effect=OSError("GLIBC_2.28 not found")):
            result = dependency_status()
            self.assertIn("OSError: GLIBC_2.28 not found", result["smb_error"])

    def test_success_reports_module_location(self):
        with patch("smb_download_dependencies.importlib.import_module", return_value=SimpleNamespace(__file__="/plugin/py_modules/smb_download_vendor/smbclient/__init__.py")):
            result = dependency_status()
            self.assertTrue(result["smb_available"])
            self.assertEqual(result["smb_error"], "")
            self.assertIn("smb_download_vendor", result["smb_module"])

    def test_frozen_runtime_missing_logging_config_and_socketserver(self):
        # A separate interpreter prevents the test suite's imports from masking
        # the exact missing-module condition reported by the user's Deck.
        script = '''
import importlib, importlib.abc, importlib.machinery, logging, logging.handlers, pathlib, sys
sys.path.insert(0, str(pathlib.Path("py_modules").resolve()))
from smb_download_dependencies import prepare_stdlib
original_logging = logging
fallback = pathlib.Path("py_modules/smb_download_stdlib").resolve()
logging.__path__ = []
class MissingFrozenModules(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name in ("socketserver", "configparser"):
            if str(fallback) not in sys.path:
                raise ModuleNotFoundError("No module named '" + name + "'")
            return importlib.machinery.PathFinder.find_spec(name, [str(fallback)])
sys.meta_path.insert(0, MissingFrozenModules())
for name in ("logging.config", "socketserver", "configparser"):
    sys.modules.pop(name, None)
try:
    importlib.import_module("logging.config")
except ModuleNotFoundError as exc:
    assert exc.name == "logging.config", exc
else:
    raise AssertionError("Missing-module condition was not reproduced")
prepare_stdlib()
config = importlib.import_module("logging.config")
assert "smb_download_stdlib" in config.__file__, config.__file__
assert sys.modules["logging"] is original_logging
assert "smb_download_stdlib" in sys.modules["socketserver"].__file__
config.dictConfig({"version": 1, "disable_existing_loggers": False})
config.fileConfig(__import__("io").StringIO("[loggers]\\nkeys=root\\n[handlers]\\nkeys=\\n[formatters]\\nkeys=\\n[logger_root]\\nlevel=INFO\\nhandlers=\\n"))
assert "smb_download_stdlib" in sys.modules["configparser"].__file__
print("Frozen-runtime logging.config regression passed")
'''
        completed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
