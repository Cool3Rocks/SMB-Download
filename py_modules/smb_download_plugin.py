"""Decky entry points for the SMB-only workflow."""
import asyncio
import logging
import os

import decky
from smb_download_service import SmbDownloadService
from smb_download_dependencies import dependency_status
from smb_download_power import PowerJournal


class SmbDownloadPluginMixin:
    async def _main(self):
        home = getattr(decky, "DECKY_USER_HOME", os.path.expanduser("~"))
        state = os.path.join(home, ".local", "share", "SMB-Download")
        os.makedirs(state, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        self._smb_download_log = RotatingFileHandler(os.path.join(state, "plugin.log"), maxBytes=5 * 1024 * 1024,
                                           backupCount=2, encoding="utf-8")
        self._smb_download_log.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        decky.logger.addHandler(self._smb_download_log)
        self._smb_download = SmbDownloadService(state, decky.logger)
        self._power = PowerJournal(state, home)
        decky.logger.info("SMB downloader ready")
        diagnostics = dependency_status()
        decky.logger.info("SMB runtime: Python=%s platform=%s vendor=%s", diagnostics["python_version"],
                          diagnostics["platform"], diagnostics["vendor_path"])
        if not diagnostics["smb_available"]:
            decky.logger.error("SMB import failed:\n%s", diagnostics.get("traceback", diagnostics["smb_error"]))

    async def _unload(self):
        if getattr(self, "_smb_download", None):
            await asyncio.to_thread(self._smb_download.close)
        if getattr(self, "_smb_download_log", None):
            decky.logger.removeHandler(self._smb_download_log)
            self._smb_download_log.close()

    async def smb_download_action(self, payload=None):
        payload = payload if isinstance(payload, dict) else {}
        action = payload.get("action", "state")
        service = getattr(self, "_smb_download", None)
        if service is None:
            return {"status": "error", "message": "SMB 服务尚未就绪"}

        def run():
            if action == "state":
                return {"settings": service.settings(), "tasks": service.views(), "power": self._power.state(service)}
            if action == "power_state":
                return self._power.state(service)
            if action == "power_begin":
                return self._power.begin()
            if action == "power_report":
                return self._power.report(payload.get("active", False), payload.get("error", ""),
                                          payload.get("restored", False))
            if action == "diagnostics":
                return dependency_status()
            if action == "settings":
                return service.save_settings(payload.get("settings", {}))
            if action == "browse":
                return service.browse(payload.get("path", ""))
            if action == "start":
                return service.start(payload.get("path", ""), payload.get("title", ""))
            if action == "resume":
                return service.resume(payload.get("id", ""))
            if action == "pause":
                return service.pause(payload.get("id", ""))
            if action == "cancel":
                return service.cancel_download(payload.get("id", ""))
            if action == "begin_delete":
                return service.begin_delete(payload.get("id", ""), payload.get("recovery_appid", 0))
            if action == "shortcut_removed":
                return service.shortcut_removed(payload.get("id", ""))
            if action == "delete_game":
                return service.delete_game(payload.get("id", ""))
            if action == "delete_failed":
                return service.delete_failed(payload.get("id", ""), payload.get("message", "删除失败，请重试"))
            if action == "local_browse":
                return service.local_browse(payload.get("id", ""), payload.get("path", ""))
            if action == "prepare_import":
                return service.prepare_import(payload.get("id", ""), payload)
            if action == "record_appid":
                return service.record_appid(payload.get("id", ""), payload.get("appid", 0))
            if action == "finish_import":
                return service.finish_import(payload.get("id", ""))
            if action == "log":
                path = os.path.join(service.state_dir, "plugin.log")
                with open(path, "rb") as stream:
                    stream.seek(max(0, os.path.getsize(path) - 32768))
                    return {"log": stream.read().decode("utf-8", errors="replace")}
            raise ValueError("未知 SMB 操作")

        try:
            return {"status": "success", "data": await asyncio.to_thread(run)}
        except (ValueError, RuntimeError) as exc:
            return {"status": "error", "message": str(exc)}
        except Exception as exc:
            decky.logger.warning("SMB action failed: action=%s type=%s", action, type(exc).__name__)
            return {"status": "error", "message": "操作失败，请检查 SMB 地址、账号、共享目录和网络连接"}
