"""SMB folder transfer. SMB stays read-only; all task state lives on the Deck."""
import hashlib
from contextlib import closing
import json
import os
import re
import shutil
import stat
import threading
import time
import uuid
from pathlib import PurePosixPath
from smb_download_dependencies import dependency_status, require_smbclient


class TransferPaused(Exception):
    pass


def relative_path(value):
    value = str(value or "").replace("\\", "/")
    if value.startswith("/") or ":" in value or "\x00" in value:
        raise ValueError("请输入共享目录内的相对路径")
    parts = value.split("/")
    if ".." in parts:
        raise ValueError("路径不能包含 ..")
    return "/".join(p for p in parts if p and p != ".")


def local_child(root, relative):
    root = os.path.realpath(root)
    target = os.path.join(root, *relative_path(relative).split("/"))
    if os.path.commonpath([root, os.path.realpath(target)]) != root:
        raise ValueError("目标路径超出游戏目录（可能包含符号链接）")
    return target


def atomic_json(path, value):
    temp = path + ".tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class SMBSource:
    def __init__(self, config):
        self.smb = require_smbclient()
        os.environ.setdefault("SMB_EXPERIMENTAL_TRANSPORT_RECEIVE_TIMEOUT", "15")
        self.config = config
        self.cache = {}  # Keep credentials and sockets isolated from other tasks/plugins.
        self.options = dict(username=config["username"], password=config["password"],
                            port=config["port"], connection_timeout=15,
                            connection_cache=self.cache)
        try:
            session = self.smb.register_session(config["host"], auth_protocol="ntlm", **self.options)
            # smbclient's file operations otherwise wait indefinitely for responses.
            # Bound only this source's isolated connection, not a global library method.
            receive = session.connection.receive
            def bounded_receive(request, wait=True, timeout=None, resolve_symlinks=True):
                return receive(request, wait=wait, timeout=30 if timeout is None else timeout,
                               resolve_symlinks=resolve_symlinks)
            session.connection.receive = bounded_receive
        except Exception:
            self.close()
            raise
        self.root = "\\\\" + config["host"] + "\\" + config["share"]
        if config["source_path"]:
            self.root += "\\" + config["source_path"].replace("/", "\\")

    def path(self, relative):
        relative = relative_path(relative)
        return self.root + ("\\" + relative.replace("/", "\\") if relative else "")

    def _check(self, path):
        info = self.smb.stat(path, follow_symlinks=False, **self.options)
        if getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("暂不支持 SMB 符号链接或目录联接")
        return info

    def check_parents(self, relative):
        # Also inspect ancestors of the configured source root within the share.
        path = "\\\\" + self.config["host"] + "\\" + self.config["share"]
        self._check(path)
        combined = "/".join(p for p in (self.config["source_path"], relative_path(relative)) if p)
        for part in combined.split("/"):
            if part:
                path += "\\" + part
                self._check(path)

    def list(self, relative):
        relative = relative_path(relative)
        self.check_parents(relative)
        items = []
        # smbprotocol 1.15.0 returns a generator, not an os.scandir context manager.
        # Closing it also releases the SMB directory handle if iteration fails.
        with closing(self.smb.scandir(self.path(relative), **self.options)) as entries:
            for entry in entries:
                name = entry.name
                if relative_path(name) != name or "/" in name:
                    raise ValueError("SMB 文件名包含不支持的路径字符")
                info = entry.stat(follow_symlinks=False)
                blocked = bool(getattr(info, "st_file_attributes", 0) & 0x400)
                items.append(dict(name=name, path="/".join(p for p in (relative, name) if p),
                                  directory=stat.S_ISDIR(info.st_mode), blocked=blocked,
                                  size=int(info.st_size), mtime=int(info.st_mtime_ns)))
        return sorted(items, key=lambda item: (not item["directory"], item["name"].casefold()))

    def info(self, relative):
        return self._check(self.path(relative))

    def open(self, relative):
        return self.smb.open_file(self.path(relative), mode="rb", share_access="r", **self.options)

    def close(self):
        # Don't wait for SMB logoff/close responses on a disconnected SMB.
        for connection in list(self.cache.values()):
            connection.disconnect(close=False)
        self.cache.clear()


class SmbDownloadService:
    def __init__(self, state_dir, logger, source_factory=SMBSource):
        self.state_dir = state_dir
        self.logger = logger
        self.source_factory = source_factory
        self.lock = threading.RLock()
        self.thread = None
        self.active_task_id = None
        self.cancel = threading.Event()
        self.closed = False
        os.makedirs(state_dir, exist_ok=True)
        self.config_path = os.path.join(state_dir, "smb-download-settings.json")
        self.tasks_path = os.path.join(state_dir, "smb-download-tasks.json")
        self.config = self._read(self.config_path, dict(host="", share="", username="", password="",
                            port=445, source_path="", install_path="/home/deck/Games"))
        self.tasks = self._read(self.tasks_path, {})
        for task in self.tasks.values():
            if task["status"] == "deleting":
                task.update(status="delete_error", error="删除被中断，请重试删除")
            if task.get("cancel_requested") or task["status"] == "cancelling":
                self._finish_cancel(task)
                continue
            if task["status"] in ("scanning", "downloading", "pausing"):
                task.update(status="paused", error="插件已重启，可继续下载")
                # Recover a process exit between atomic directory rename and state save.
                if not os.path.exists(task["stage"]) and os.path.isdir(task["install_dir"]) and task.get("manifest"):
                    files = task["manifest"]["files"]
                    if all(os.path.isfile(local_child(task["install_dir"], f["path"])) and
                           os.path.getsize(local_child(task["install_dir"], f["path"])) == f["size"] for f in files):
                        task.update(status="downloaded", completed=task["total"], error="", current_file="")
            if task["status"] == "importing":
                task.update(status="import_pending", error="入库被中断，请确认 Steam 条目后继续")
        self._save_tasks()

    @staticmethod
    def _read(path, default):
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)

    def _save_tasks(self):
        with self.lock:
            atomic_json(self.tasks_path, self.tasks)

    def settings(self):
        with self.lock:
            result = {k: v for k, v in self.config.items() if k != "password"}
            result["has_password"] = bool(self.config["password"])
            result["keep_awake"] = self.config.get("keep_awake", True)
            result.update({k: v for k, v in dependency_status().items() if k != "traceback"})
            return result

    def save_settings(self, values):
        with self.lock:
            config = dict(self.config)
            if "keep_awake" in values:
                if type(values["keep_awake"]) is not bool:
                    raise ValueError("下载亮屏开关必须为布尔值")
                config["keep_awake"] = values["keep_awake"]
            for key in ("host", "share", "username", "source_path", "install_path"):
                if key in values:
                    config[key] = str(values[key]).strip()
            if "password" in values:  # Omitted = preserve; empty string = clear.
                config["password"] = str(values["password"])
            config["port"] = int(values.get("port", config["port"]))
            if not config["host"] or any(c in config["host"] for c in "/\\:@ \x00"):
                raise ValueError("SMB 地址应为 IP 或主机名，不包含 smb:// 或端口")
            if not config["share"] or any(c in config["share"] for c in "/\\:\x00") or config["share"] in (".", ".."):
                raise ValueError("请输入单个 SMB 共享名称")
            if not 1 <= config["port"] <= 65535:
                raise ValueError("端口范围为 1–65535")
            if not config["username"]:
                raise ValueError("请输入 SMB 用户名（首版使用账号密码认证）")
            config["source_path"] = relative_path(config["source_path"])
            if not os.path.isabs(config["install_path"]):
                raise ValueError("本地安装目录必须是绝对路径")
            config["install_path"] = os.path.realpath(config["install_path"])
            atomic_json(self.config_path, config)
            self.config = config
            return self.settings()

    def browse(self, path=""):
        with self.lock:
            config = dict(self.config)
        source = self.source_factory(config)
        try:
            return dict(path=relative_path(path), entries=source.list(path))
        finally:
            source.close()

    def views(self):
        with self.lock:
            return [self._view(t) for t in sorted(self.tasks.values(), key=lambda t: t["created"], reverse=True)]

    @staticmethod
    def _view(task):
        # Whitelist fields: connection snapshots include passwords and never leave the backend.
        keys = ("id", "title", "source", "install_dir", "status", "total", "completed", "current_file",
                "error", "created", "exe", "working_dir", "launch_options", "compat_tool", "appid", "steam_removed")
        view = {k: task.get(k, "") for k in keys}
        view["steam_removed"] = bool(task.get("steam_removed"))
        view["progress"] = round(task.get("completed", 0) / task["total"] * 100, 1) if task.get("total") else 0
        if task["status"] in ("downloaded", "import_pending", "importing", "complete"):
            view["progress"] = 100
        return view

    def _task(self, task_id):
        if task_id not in self.tasks:
            raise ValueError("下载任务不存在")
        return self.tasks[task_id]

    def start(self, source_path, title):
        relative = relative_path(source_path)
        if not relative:
            raise ValueError("请选择游戏文件夹，不能下载整个源目录")
        title = str(title or PurePosixPath(relative).name).strip()
        if not title or len(title) > 200 or "\x00" in title:
            raise ValueError("请输入有效游戏名称（最多 200 字）")
        with self.lock:
            if self.closed:
                raise ValueError("插件正在停止")
            if self.thread and self.thread.is_alive():
                raise ValueError("已有下载任务运行，请等待完成或暂停")
            config = dict(self.config)
            if not config["host"] or not config["share"]:
                raise ValueError("请先保存 SMB 连接设置")
            identity = hashlib.sha256(json.dumps([config["host"].lower(), config["port"], config["share"],
                                       config["source_path"], relative, config["install_path"]]).encode()).hexdigest()[:12]
            for task in self.tasks.values():
                if task["identity"] == identity and task["status"] != "cancelled":
                    raise ValueError("该文件夹已有任务，请在下载任务中继续下载或入库")
            root = config["install_path"]
            os.makedirs(root, exist_ok=True)
            slug = re.sub(r'[/\\:\x00-\x1f]', "_", PurePosixPath(relative).name).strip(". ")[:80] or "game"
            destination = local_child(root, slug + "-" + identity)
            if os.path.lexists(destination):
                raise ValueError("目标目录已存在，选择其他安装目录，避免覆盖游戏")
            task_id = uuid.uuid4().hex
            stage = local_child(root, ".smb-download/" + task_id)
            os.makedirs(stage, exist_ok=False)
            task = dict(id=task_id, identity=identity, title=title, source=relative, connection=config,
                        stage=stage, install_dir=destination, status="paused", total=0, completed=0,
                        created=time.time(), manifest=None, current_file="", error="", exe="",
                        working_dir="", launch_options="", compat_tool="proton_experimental", appid=0)
            self.tasks[task_id] = task
            self._save_tasks()
            self._launch(task)
            return self._view(task)

    def resume(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("paused", "error"):
                raise ValueError("只有暂停或失败的下载可以继续")
            if self.closed or (self.thread and self.thread.is_alive()):
                raise ValueError("已有下载运行或插件正在停止")
            self._launch(task)
            return self._view(task)

    def _launch(self, task):
        self.cancel.clear()
        self.active_task_id = task["id"]
        task["cancel_requested"] = False
        task.update(status="scanning", error="")
        self._save_tasks()
        self.thread = threading.Thread(target=self._transfer, args=(task,), name="smb_download-transfer", daemon=True)
        self.thread.start()

    def pause(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if task["status"] in ("scanning", "downloading"):
                task["status"] = "pausing"
                self.cancel.set()
                self._save_tasks()
            return self._view(task)

    def cancel_download(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if task["status"] == "cancelled":
                return self._view(task)
            if task["status"] not in ("scanning", "downloading", "pausing", "paused", "error", "cancelling", "cancel_error"):
                raise ValueError("下载已完成，取消下载不会删除已安装的游戏")
            task.update(cancel_requested=True, status="cancelling", error="")
            self._save_tasks()  # Restart recovery must finish cleaning this same task.
            if self.active_task_id == task_id and self.thread and self.thread.is_alive():
                self.cancel.set()
                # Worker cleans up only after all open files and SMB handles are closed.
            else:
                self._finish_cancel(task)
            return self._view(task)

    def _finish_cancel(self, task):
        with self.lock:
            try:
                task_id = task["id"]
                if not re.fullmatch(r"[0-9a-f]{32}", task_id):
                    raise ValueError("临时目录标识无效，拒绝清理")
                root = os.path.realpath(task["connection"]["install_path"])
                expected = os.path.join(root, ".smb-download", task_id)
                # Check the exact absolute target before recursive removal. Never follow
                # a replaced staging directory/parent symlink or touch install_dir.
                if (os.path.abspath(task["stage"]) != expected or os.path.realpath(expected) != expected
                        or os.path.commonpath([root, expected]) != root):
                    raise ValueError("临时目录路径发生变化，拒绝清理")
                if os.path.lexists(expected):
                    if not os.path.isdir(expected):
                        raise ValueError("临时下载路径不是文件夹，拒绝清理")
                    shutil.rmtree(expected)
                task.update(status="cancelled", cancel_requested=False, completed=0, total=0,
                            current_file="", error="", manifest=None)
                task["connection"]["password"] = ""
                self.logger.info("SMB download cancelled; temporary files removed: task=%s", task_id)
            except Exception as exc:
                task.update(status="cancel_error", error=str(exc) if isinstance(exc, ValueError)
                            else "临时文件清理失败，请检查存储权限后重试取消")
                self.logger.warning("SMB temporary cleanup failed: task=%s type=%s", task["id"], type(exc).__name__)
            self._save_tasks()

    def _checkpoint(self):
        if self.cancel.is_set():
            raise TransferPaused()

    def _delete_target(self, task):
        config = task["connection"]
        root = os.path.abspath(config["install_path"])
        relative = relative_path(task["source"])
        identity = hashlib.sha256(json.dumps([config["host"].lower(), config["port"], config["share"],
                                   config["source_path"], relative, config["install_path"]]).encode()).hexdigest()[:12]
        slug = re.sub(r'[/\\:\x00-\x1f]', "_", PurePosixPath(relative).name).strip(". ")[:80] or "game"
        expected = os.path.join(root, slug + "-" + identity)
        if (not relative or identity != task["identity"] or os.path.realpath(root) != root or
                os.path.abspath(task["install_dir"]) != expected or os.path.realpath(expected) != expected or
                os.path.commonpath([root, expected]) != root):
            raise ValueError("游戏目录路径发生变化，拒绝删除")
        if os.path.lexists(expected) and not os.path.isdir(expected):
            raise ValueError("游戏路径不是文件夹，拒绝删除")
        for other in self.tasks.values():
            if other["id"] != task["id"] and other["status"] != "cancelled":
                other_path = os.path.abspath(other["install_dir"])
                if os.path.splitdrive(other_path)[0] == os.path.splitdrive(expected)[0] and os.path.commonpath([expected, other_path]) == expected:
                    raise ValueError("游戏目录包含另一个任务，拒绝删除")
        return expected

    def begin_delete(self, task_id, recovery_appid=0):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("downloaded", "import_pending", "importing", "complete", "deleting", "delete_error"):
                raise ValueError("仅可删除已下载的游戏；未完成下载请使用取消下载")
            if self.active_task_id == task_id:
                raise ValueError("下载尚未完全结束，请稍后删除")
            self._delete_target(task)  # Validate before changing Steam or deleting files.
            appid = int(recovery_appid or task.get("appid") or 0)
            if appid and (not 0x80000000 <= appid <= 0xffffffff or task["appid"] and appid != task["appid"]):
                raise ValueError("Steam 快捷方式 ID 无效或与任务不一致，拒绝删除")
            task.update(appid=appid, status="deleting", error="")
            task.setdefault("steam_removed", False)
            self._save_tasks()
            return self._view(task)

    def shortcut_removed(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("deleting", "delete_error"):
                raise ValueError("任务尚未开始删除")
            task["steam_removed"] = True
            self._save_tasks()
            return self._view(task)

    def delete_failed(self, task_id, message):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("deleting", "delete_error"):
                raise ValueError("任务尚未开始删除")
            task.update(status="delete_error", error=str(message)[:500])
            self._save_tasks()
            return self._view(task)

    def delete_game(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("deleting", "delete_error"):
                raise ValueError("任务尚未开始删除")
            try:
                if task["appid"] and not task.get("steam_removed"):
                    raise ValueError("请先确认 Steam 条目已移除，本地游戏文件仍保留")
                target = self._delete_target(task)
                if os.path.lexists(target):
                    shutil.rmtree(target)
                del self.tasks[task_id]
                try:
                    self._save_tasks()
                except Exception:
                    self.tasks[task_id] = task
                    raise
                self.logger.info("SMB game deleted: task=%s", task_id)
                return {"deleted": True}
            except Exception as exc:
                task.update(status="delete_error", error=str(exc) if isinstance(exc, ValueError)
                            else "本地游戏删除失败，请检查文件权限后重试")
                self._save_tasks()
                raise RuntimeError(task["error"]) from exc

    def _scan(self, source, relative):
        source.check_parents(relative)
        files, directories, stack = [], [], [(relative, "")]
        while stack:
            self._checkpoint()
            remote, local = stack.pop()
            if len(PurePosixPath(local).parts) > 64:
                raise ValueError("目录层级超过 64 层")
            for entry in source.list(remote):
                self._checkpoint()
                if entry["blocked"]:
                    raise ValueError("游戏目录含符号链接或目录联接，请改为普通文件夹")
                rel = "/".join(p for p in (local, entry["name"]) if p)
                if entry["directory"]:
                    directories.append(rel)
                    stack.append((entry["path"], rel))
                else:
                    files.append(dict(path=rel, size=entry["size"], mtime=entry["mtime"]))
                if len(files) + len(directories) > 200000:
                    raise ValueError("游戏文件数超过 200000")
        return dict(files=sorted(files, key=lambda f: f["path"]), directories=sorted(directories))

    def _transfer(self, task):
        source = None
        last_save = 0
        try:
            source = self.source_factory(task["connection"])
            manifest = self._scan(source, task["source"])
            if not manifest["files"]:
                raise ValueError("文件夹为空，没有可下载文件")
            with self.lock:
                if task["manifest"] is not None and task["manifest"] != manifest:
                    raise ValueError("SMB 文件发生变化，停止续传。请使用另一安装目录新建任务")
                task["manifest"] = manifest
                task["total"] = sum(f["size"] for f in manifest["files"])
            stage = local_child(task["connection"]["install_path"], ".smb-download/" + task["id"])
            if os.path.realpath(task["stage"]) != stage:
                raise ValueError("下载临时目录发生变化")
            # All files are staging files; their lengths are safe resume offsets after a crash.
            offsets = {f["path"]: os.path.getsize(local_child(stage, f["path"]))
                       if os.path.isfile(local_child(stage, f["path"])) else 0 for f in manifest["files"]}
            if any(offsets[f["path"]] > f["size"] for f in manifest["files"]):
                raise ValueError("本地临时文件大小异常，停止续传")
            with self.lock:
                self._checkpoint()
                task["completed"] = sum(offsets.values())
                task["status"] = "downloading"
                self._save_tasks()
            required = task["total"] - task["completed"]
            if shutil.disk_usage(stage).free < required + 64 * 1024 * 1024:
                raise ValueError("本地可用空间不足（额外保留 64 MiB）")
            for directory in manifest["directories"]:
                os.makedirs(local_child(stage, directory), exist_ok=True)
            for item in manifest["files"]:
                self._checkpoint()
                remote = task["source"] + "/" + item["path"]
                before = source.info(remote)
                if before.st_size != item["size"] or before.st_mtime_ns != item["mtime"]:
                    raise ValueError("SMB 文件在下载期间发生变化，请停止并检查文件")
                target = local_child(stage, item["path"])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                offset = offsets[item["path"]]
                with self.lock:
                    task["current_file"] = item["path"]
                # Always create zero-byte files as well.
                with open(target, "ab") as local:
                    if offset < item["size"]:
                        with source.open(remote) as remote_file:
                            remote_file.seek(offset)
                            while offset < item["size"]:
                                self._checkpoint()
                                chunk = remote_file.read(min(1024 * 1024, item["size"] - offset))
                                if not chunk:
                                    raise IOError("SMB 文件提前结束，保留临时文件以便重试")
                                local.write(chunk)
                                local.flush()
                                offset += len(chunk)
                                with self.lock:
                                    task["completed"] += len(chunk)
                                    if time.monotonic() - last_save > 1:
                                        self._save_tasks()
                                        last_save = time.monotonic()
                    os.fsync(local.fileno())
                after = source.info(remote)
                if after.st_size != item["size"] or after.st_mtime_ns != item["mtime"]:
                    raise ValueError("SMB 文件在下载期间发生变化，不能完成安装")
                os.utime(target, ns=(item["mtime"], item["mtime"]))
                if target.lower().endswith((".sh", ".appimage")):
                    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
            with self.lock:
                self._checkpoint()
                destination = local_child(task["connection"]["install_path"], os.path.basename(task["install_dir"]))
                if os.path.lexists(destination):
                    raise ValueError("目标目录已存在，拒绝覆盖")
                os.rename(stage, destination)
                task.update(status="downloaded", current_file="", completed=task["total"], error="")
            self.logger.info("SMB download completed: task=%s", task["id"])
        except TransferPaused:
            with self.lock:
                task.update(status="cancelling" if task.get("cancel_requested") else "paused", error="")
        except Exception as exc:
            with self.lock:
                # Don't include raw SMB exceptions: they may contain connection/credential data.
                task.update(status="error", error=str(exc) if isinstance(exc, (ValueError, RuntimeError))
                            else "SMB 传输失败，请检查网络、权限及可用空间后继续下载")
            self.logger.warning("SMB transfer failed: task=%s type=%s", task["id"], type(exc).__name__)
        finally:
            if source:
                try:
                    source.close()
                except Exception:
                    pass
            with self.lock:
                if self.active_task_id == task["id"]:
                    self.active_task_id = None
                if task.get("cancel_requested"):
                    self._finish_cancel(task)
                else:
                    self._save_tasks()

    def local_browse(self, task_id, path=""):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("downloaded", "import_pending", "importing", "complete"):
                raise ValueError("请先完成下载")
            root = task["install_dir"]
        path = relative_path(path)
        target = local_child(root, path)
        if not os.path.isdir(target):
            raise ValueError("请选择存在的游戏文件夹")
        entries = []
        # Keep immediate folders for optional navigation, but flatten every EXE
        # below the current directory so selecting one needs no folder drilling.
        pending = [(path, 0)]
        examined = 0
        while pending:
            folder, depth = pending.pop()
            with os.scandir(local_child(root, folder)) as children:
                for child in children:
                    examined += 1
                    if examined > 200000:
                        raise ValueError("游戏目录文件过多，请缩小启动程序搜索目录")
                    if child.is_symlink() or getattr(child.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                        continue
                    rel = "/".join(p for p in (folder, child.name) if p)
                    directory = child.is_dir(follow_symlinks=False)
                    if directory:
                        if depth >= 64:
                            raise ValueError("游戏目录层级过深，请缩小启动程序搜索目录")
                        pending.append((rel, depth + 1))
                    if (directory and depth == 0) or (child.is_file(follow_symlinks=False) and child.name.lower().endswith(".exe")):
                        entries.append(dict(name=child.name, path=rel, directory=directory,
                                            size=0 if directory else child.stat(follow_symlinks=False).st_size, blocked=False))
        return dict(path=path, entries=sorted(entries, key=lambda e: (not e["directory"], e["name"].casefold(), e["path"].casefold())))

    def prepare_import(self, task_id, values):
        with self.lock:
            task = self._task(task_id)
            if task["status"] not in ("downloaded", "import_pending", "importing", "complete"):
                raise ValueError("请先完成下载")
            exe = relative_path(values.get("exe", ""))
            executable = local_child(task["install_dir"], exe)
            if not exe or not os.path.isfile(executable):
                raise ValueError("请选择存在的启动程序")
            title = str(values.get("title", task["title"])).strip()
            if not title or len(title) > 200 or "\x00" in title:
                raise ValueError("请输入有效游戏名称")
            working = relative_path(values.get("working_dir", "")) or str(PurePosixPath(exe).parent)
            working = relative_path(working)
            if not os.path.isdir(local_child(task["install_dir"], working)):
                raise ValueError("工作目录不存在")
            if not exe.lower().endswith(".exe"):
                os.chmod(executable, os.stat(executable).st_mode | stat.S_IXUSR)
            if any("\x00" in str(values.get(k, "")) for k in ("launch_options", "compat_tool")):
                raise ValueError("启动参数不能包含空字符")
            task.update(title=title, exe=exe, working_dir=working,
                        launch_options=str(values.get("launch_options", "")),
                        compat_tool=str(values.get("compat_tool", "")), status="import_pending", error="")
            self._save_tasks()
            view = self._view(task)
            view.update(executable=executable, start_dir=local_child(task["install_dir"], working))
            return view

    def record_appid(self, task_id, appid):
        with self.lock:
            task = self._task(task_id)
            appid = int(appid) & 0xffffffff
            if not appid or task["status"] not in ("import_pending", "importing", "complete"):
                raise ValueError("Steam 入库状态或 AppID 无效")
            if task["appid"] and task["appid"] != appid:
                raise ValueError("任务已关联另一个 Steam 条目")
            task.update(appid=appid, status="importing" if task["status"] != "complete" else "complete")
            self._save_tasks()
            return self._view(task)

    def finish_import(self, task_id):
        with self.lock:
            task = self._task(task_id)
            if not task["appid"] or task["status"] not in ("importing", "import_pending", "complete"):
                raise ValueError("尚未关联 Steam 快捷方式")
            task.update(status="complete", error="")
            # Credentials are no longer needed once the mission is finished.
            task["connection"]["password"] = ""
            task["manifest"] = None
            self._save_tasks()
            return self._view(task)

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel.set()
        if self.thread:
            self.thread.join()  # Caller runs this in an executor; never block Decky's event loop.
