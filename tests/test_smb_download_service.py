import io
import json
import logging
import os
import stat
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))
from smb_download_service import SmbDownloadService, SMBSource, relative_path, local_child


class LocalSource:
    root = None
    interrupt = None
    bytes_read = 0

    def __init__(self, config):
        self.base = Path(self.root)

    def check_parents(self, path):
        if not (self.base / path).exists():
            raise FileNotFoundError(path)

    def list(self, path):
        return [dict(name=p.name, path="/".join(filter(None, [path, p.name])),
                     directory=p.is_dir(), blocked=False, size=p.stat().st_size, mtime=p.stat().st_mtime_ns)
                for p in (self.base / path).iterdir()]

    def info(self, path):
        return (self.base / path).stat()

    def open(self, path):
        owner = self
        class Reader:
            def __init__(self):
                self.stream = open(owner.base / path, "rb")
            def __enter__(self): return self
            def __exit__(self, *_): self.stream.close()
            def seek(self, offset): return self.stream.seek(offset)
            def read(self, count):
                data = self.stream.read(count)
                LocalSource.bytes_read += len(data)
                if LocalSource.interrupt:
                    LocalSource.interrupt()
                return data
        return Reader()

    def close(self): pass


class SmbDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote"
        (self.remote / "中文 游戏" / "bin").mkdir(parents=True)
        (self.remote / "中文 游戏" / "bin" / "game.exe").write_bytes(b"test" * 600000)
        (self.remote / "中文 游戏" / "empty").mkdir()
        (self.remote / "中文 游戏" / "zero.txt").write_bytes(b"")
        LocalSource.root = self.remote
        LocalSource.interrupt = None
        LocalSource.bytes_read = 0
        self.service = SmbDownloadService(str(self.root / "state"), logging.getLogger("test"), LocalSource)
        self.service.save_settings(dict(host="smb_download", share="Games", username="user", password="test-secret",
                                        install_path=str(self.root / "install")))

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def start(self):
        task = self.service.start("中文 游戏", "我的游戏")
        self.service.thread.join(10)
        self.assertFalse(self.service.thread.is_alive())
        return self.service.views()[0]

    def test_download_and_import_and_duplicate(self):
        task = self.start()
        self.assertEqual(task["status"], "downloaded")
        dest = Path(task["install_dir"])
        self.assertEqual((dest / "bin/game.exe").read_bytes(), (self.remote / "中文 游戏/bin/game.exe").read_bytes())
        self.assertTrue((dest / "empty").is_dir())
        self.assertTrue((dest / "zero.txt").is_file())
        prepared = self.service.prepare_import(task["id"], dict(exe="bin/game.exe", title="新名称"))
        self.assertEqual(prepared["start_dir"], str(dest / "bin"))
        self.assertEqual(prepared["executable"], str(dest / "bin/game.exe"))
        self.service.record_appid(task["id"], -123)
        # Configuration failure retry must preserve the existing shortcut ID.
        again = self.service.prepare_import(task["id"], dict(exe="bin/game.exe"))
        self.assertEqual(again["appid"], (-123 & 0xffffffff))
        self.service.finish_import(task["id"])
        self.service.finish_import(task["id"])
        repair = self.service.prepare_import(task["id"], dict(exe="bin/game.exe", title="新名称"))
        self.assertEqual(repair["appid"], (-123 & 0xffffffff))
        self.assertEqual(repair["status"], "import_pending")
        self.service.finish_import(task["id"])
        with self.assertRaises(ValueError): self.service.start("中文 游戏", "另一个名称")
        self.assertEqual(self.service.tasks[task["id"]]["connection"]["password"], "")

    def test_pause_resume_and_restart(self):
        LocalSource.interrupt = self.service.cancel.set
        task = self.start()
        self.assertEqual(task["status"], "paused")
        self.assertGreater(task["completed"], 0)
        copied = task["completed"]
        self.service.close()
        LocalSource.interrupt = None
        self.service = SmbDownloadService(str(self.root / "state"), logging.getLogger("test"), LocalSource)
        # Changes in global settings must not redirect an existing task.
        self.service.save_settings(dict(install_path=str(self.root / "other"), password="new-secret"))
        self.service.resume(task["id"])
        self.service.thread.join(10)
        done = self.service.views()[0]
        self.assertEqual(done["status"], "downloaded")
        self.assertEqual(done["install_dir"], task["install_dir"])
        self.assertEqual(LocalSource.bytes_read, done["total"])
        self.assertLess(copied, done["total"])

    def test_cancel_active_transfer_waits_for_file_close_then_removes_temporary_files(self):
        entered, release = threading.Event(), threading.Event()
        def block_read():
            entered.set()
            release.wait(10)
        LocalSource.interrupt = block_read
        task = self.service.start("中文 游戏", "我的游戏")
        try:
            self.assertTrue(entered.wait(10))
            stage = Path(self.service.tasks[task["id"]]["stage"])
            result = self.service.cancel_download(task["id"])
            self.assertEqual(result["status"], "cancelling")
            self.assertTrue(stage.exists())  # Open file must not be removed before the worker stops.
        finally:
            release.set()
        self.service.thread.join(10)
        self.assertFalse(self.service.thread.is_alive())
        done = self.service.views()[0]
        self.assertEqual(done["status"], "cancelled")
        self.assertFalse(stage.exists())
        self.assertFalse(Path(done["install_dir"]).exists())
        self.assertTrue((self.remote / "中文 游戏/bin/game.exe").exists())

    def test_cancel_paused_preserves_other_files_and_allows_new_download(self):
        LocalSource.interrupt = self.service.cancel.set
        task = self.start()
        stage = Path(self.service.tasks[task["id"]]["stage"])
        unrelated = stage.parent / "other-task"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")
        result = self.service.cancel_download(task["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(stage.exists())
        self.assertTrue((unrelated / "keep.txt").exists())
        self.assertEqual(self.service.tasks[task["id"]]["connection"]["password"], "")
        self.assertEqual(self.service.cancel_download(task["id"])["status"], "cancelled")
        LocalSource.interrupt = None
        new = self.service.start("中文 游戏", "我的游戏")
        self.service.thread.join(10)
        self.assertNotEqual(new["id"], task["id"])
        self.assertEqual(self.service.tasks[new["id"]]["status"], "downloaded")

    def test_cancel_never_deletes_finished_game(self):
        task = self.start()
        with self.assertRaises(ValueError): self.service.cancel_download(task["id"])
        self.assertTrue(Path(task["install_dir"]).is_dir())

    def test_cancel_rejects_changed_temporary_path_and_retries(self):
        LocalSource.interrupt = self.service.cancel.set
        task = self.start()
        saved = self.service.tasks[task["id"]]
        correct_stage = saved["stage"]
        outside = self.root / "unrelated"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        saved["stage"] = str(outside)
        result = self.service.cancel_download(task["id"])
        self.assertEqual(result["status"], "cancel_error")
        self.assertTrue((outside / "keep.txt").exists())
        saved["stage"] = correct_stage
        self.assertEqual(self.service.cancel_download(task["id"])["status"], "cancelled")

    def test_restart_finishes_pending_cancellation(self):
        LocalSource.interrupt = self.service.cancel.set
        task = self.start()
        saved = self.service.tasks[task["id"]]
        stage = Path(saved["stage"])
        saved.update(status="cancelling", cancel_requested=True)
        self.service._save_tasks()
        self.service.close()
        self.service = SmbDownloadService(str(self.root / "state"), logging.getLogger("test"), LocalSource)
        self.assertEqual(self.service.views()[0]["status"], "cancelled")
        self.assertFalse(stage.exists())

    def test_changed_remote_is_not_appended(self):
        LocalSource.interrupt = self.service.cancel.set
        task = self.start()
        (self.remote / "中文 游戏/bin/game.exe").write_bytes(b"changed")
        LocalSource.interrupt = None
        self.service.resume(task["id"])
        self.service.thread.join(10)
        failed = self.service.views()[0]
        self.assertEqual(failed["status"], "error")
        self.assertIn("SMB 文件发生变化", failed["error"])
        self.assertFalse(Path(task["install_dir"]).exists())

    def test_password_never_returned(self):
        task = self.start()
        self.assertNotIn("test-secret", json.dumps(self.service.settings()))
        self.assertNotIn("test-secret", json.dumps(self.service.views()))
        self.assertNotIn("connection", task)
        self.service.save_settings(dict(username="changed"))
        self.assertEqual(self.service.config["password"], "test-secret")
        self.service.save_settings(dict(password=""))
        self.assertFalse(self.service.settings()["has_password"])

    def test_paths_and_missing_launch_file(self):
        for path in ("../outside", "/etc/passwd", "C:/file", "a/../../file", "\\\\smb_download\\share"):
            with self.assertRaises(ValueError): relative_path(path)
        self.assertEqual(relative_path("bin\\game.exe"), "bin/game.exe")
        task = self.start()
        for exe in ("../game.exe", "missing.exe"):
            with self.assertRaises(ValueError): self.service.prepare_import(task["id"], dict(exe=exe))

    def test_symlink_escape(self):
        inside, outside = self.root / "inside", self.root / "outside"
        inside.mkdir(); outside.mkdir()
        try: (inside / "link").symlink_to(outside, target_is_directory=True)
        except OSError: self.skipTest("Windows symlink privilege unavailable")
        with self.assertRaises(ValueError): local_child(str(inside), "link/file")

    def test_delete_downloaded_removes_only_managed_game_and_task(self):
        task = self.start()
        other = Path(task["install_dir"]).parent / "keep"
        other.mkdir()
        (other / "save.txt").write_text("keep")
        self.service.begin_delete(task["id"])
        self.service.delete_game(task["id"])
        self.assertNotIn(task["id"], self.service.tasks)
        self.assertFalse(Path(task["install_dir"]).exists())
        self.assertTrue((other / "save.txt").exists())
        self.assertTrue((self.remote / "中文 游戏/bin/game.exe").exists())
        self.assertEqual(self.start()["status"], "downloaded")

    def test_delete_imported_requires_steam_acknowledgement_and_recovers_restart(self):
        task = self.start()
        self.service.prepare_import(task["id"], dict(exe="bin/game.exe"))
        self.service.record_appid(task["id"], 0x80000001)
        self.service.finish_import(task["id"])
        self.service.begin_delete(task["id"])
        with self.assertRaises(RuntimeError):
            self.service.delete_game(task["id"])
        self.assertTrue(Path(task["install_dir"]).exists())
        self.service.begin_delete(task["id"])
        self.service.shortcut_removed(task["id"])
        self.service.close()
        self.service = SmbDownloadService(str(self.root / "state"), logging.getLogger("test"), LocalSource)
        recovered = self.service.begin_delete(task["id"])
        self.assertTrue(recovered["steam_removed"])
        self.service.delete_game(task["id"])
        self.assertFalse(Path(task["install_dir"]).exists())

    def test_delete_refuses_changed_path_before_and_after_steam_removal(self):
        task = self.start()
        saved = self.service.tasks[task["id"]]
        original = saved["install_dir"]
        saved["install_dir"] = str(self.root)
        with self.assertRaises(ValueError):
            self.service.begin_delete(task["id"])
        self.assertEqual(saved["status"], "downloaded")
        saved["install_dir"] = original
        self.service.begin_delete(task["id"])
        saved["install_dir"] = str(self.root)
        with self.assertRaises(RuntimeError):
            self.service.delete_game(task["id"])
        self.assertTrue(Path(original).exists())
        self.assertTrue(self.remote.exists())

    def test_delete_partial_failure_can_retry_and_keeps_task(self):
        task = self.start()
        self.service.begin_delete(task["id"])
        with patch("smb_download_service.shutil.rmtree", side_effect=PermissionError("busy")):
            with self.assertRaises(RuntimeError):
                self.service.delete_game(task["id"])
        self.assertEqual(self.service.tasks[task["id"]]["status"], "delete_error")
        self.assertTrue(Path(task["install_dir"]).exists())
        self.service.begin_delete(task["id"])
        self.service.delete_game(task["id"])
        self.assertNotIn(task["id"], self.service.tasks)

    def test_cancelled_history_does_not_block_deleting_redownloaded_game(self):
        LocalSource.interrupt = self.service.cancel.set
        old = self.start()
        self.service.cancel_download(old["id"])
        LocalSource.interrupt = None
        task = self.start()
        self.service.begin_delete(task["id"])
        self.service.delete_game(task["id"])
        self.assertIn(old["id"], self.service.tasks)
        self.assertNotIn(task["id"], self.service.tasks)

    def test_delete_validates_recovered_steam_id_and_rejects_unfinished_task(self):
        task = self.start()
        with self.assertRaises(ValueError):
            self.service.begin_delete(task["id"], 123)
        self.assertEqual(self.service.begin_delete(task["id"], 0x80000002)["appid"], 0x80000002)
        self.service.tasks[task["id"]]["status"] = "paused"
        with self.assertRaises(ValueError):
            self.service.begin_delete(task["id"])

    def test_executable_picker_flattens_subfolders_and_filters_other_files(self):
        game = self.remote / "中文 游戏"
        (game / "bin/deeper").mkdir()
        (game / "bin/deeper/GAME.EXE").write_bytes(b"nested")
        (game / "game.exe").write_bytes(b"root")
        (game / "game.exe.txt").write_bytes(b"not executable")
        (game / "bin/library.dll").write_bytes(b"not executable")
        task = self.start()
        entries = self.service.local_browse(task["id"])["entries"]
        self.assertEqual({e["path"] for e in entries if e["directory"]}, {"bin", "empty"})
        self.assertEqual({e["path"] for e in entries if not e["directory"]},
                         {"game.exe", "bin/game.exe", "bin/deeper/GAME.EXE"})
        nested = self.service.local_browse(task["id"], "bin")
        self.assertEqual(nested["path"], "bin")
        self.assertEqual({e["path"] for e in nested["entries"] if not e["directory"]},
                         {"bin/game.exe", "bin/deeper/GAME.EXE"})
        prepared = self.service.prepare_import(task["id"], dict(exe="bin/deeper/GAME.EXE"))
        self.assertEqual(prepared["start_dir"], str(Path(task["install_dir"]) / "bin/deeper"))

    def test_executable_picker_requires_finished_download_and_rejects_escape(self):
        task = self.start()
        with self.assertRaises(ValueError):
            self.service.local_browse(task["id"], "../")
        self.service.tasks[task["id"]]["status"] = "paused"
        with self.assertRaises(ValueError):
            self.service.local_browse(task["id"])

    def test_crash_after_directory_publish_recovers_download(self):
        task = self.start()
        saved = self.service.tasks[task["id"]]
        saved["status"] = "downloading"  # Simulate last state before the directory rename.
        self.service._save_tasks()
        self.service.close()
        self.service = SmbDownloadService(str(self.root / "state"), logging.getLogger("test"), LocalSource)
        recovered = self.service.views()[0]
        self.assertEqual(recovered["status"], "downloaded")
        self.assertEqual(recovered["progress"], 100)

    def test_smb_session_options_and_bounded_io(self):
        responses, disconnects = [], []
        class Connection:
            def receive(self, request, **kwargs): responses.append(kwargs); return "response"
            def disconnect(self, close=True): disconnects.append(close)
        connection = Connection()
        def register(server, username=None, password=None, port=445, connection_timeout=60,
                     connection_cache=None, auth_protocol=None):
            self.assertEqual(server, "smb_download")
            self.assertEqual(auth_protocol, "ntlm")
            self.assertEqual(connection_timeout, 15)
            connection_cache[server] = connection
            return SimpleNamespace(connection=connection)
        fake = SimpleNamespace(register_session=register)
        with patch.dict(sys.modules, {"smbclient": fake}):
            source = SMBSource(self.service.config)
            self.assertNotIn("auth_protocol", source.options)
            self.assertEqual(source.path("中文 游戏/bin/game.exe"), "\\\\smb_download\\Games\\中文 游戏\\bin\\game.exe")
            connection.receive("request")
            self.assertEqual(responses[0]["timeout"], 30)
            connection.receive("request", timeout=5)
            self.assertEqual(responses[1]["timeout"], 5)
            source.close()
        self.assertEqual(disconnects, [False])

    def test_directory_generator_is_iterated_and_closed(self):
        # Match smbprotocol 1.15.0: scandir yields entries and is NOT a context manager.
        source = object.__new__(SMBSource)
        source.options = {}
        source.root = "\\\\smb_download\\Games"
        source.check_parents = lambda path: None
        closed = []
        def scandir(*args, **kwargs):
            try:
                for name, mode in (("game.exe", stat.S_IFREG), ("中文 游戏", stat.S_IFDIR)):
                    info = SimpleNamespace(st_mode=mode, st_size=10, st_mtime_ns=123, st_file_attributes=0)
                    yield SimpleNamespace(name=name, stat=lambda follow_symlinks=False, info=info: info)
            finally:
                closed.append(True)
        source.smb = SimpleNamespace(scandir=scandir)
        result = source.list("")
        self.assertEqual([entry["name"] for entry in result], ["中文 游戏", "game.exe"])
        self.assertEqual(result[0]["directory"], True)
        self.assertEqual(closed, [True])

    def test_directory_generator_closed_when_entry_processing_fails(self):
        source = object.__new__(SMBSource)
        source.options = {}
        source.root = "\\\\smb_download\\Games"
        source.check_parents = lambda path: None
        closed = []
        def scandir(*args, **kwargs):
            try:
                yield SimpleNamespace(name="../invalid")
            finally:
                closed.append(True)
        source.smb = SimpleNamespace(scandir=scandir)
        with self.assertRaises(ValueError):
            source.list("")
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
