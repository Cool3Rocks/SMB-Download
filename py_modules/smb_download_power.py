"""Recovery journal for temporary Steam power timer overrides; never edits Steam VDF."""
import os
import re
import threading
from smb_download_service import atomic_json, SmbDownloadService

POWER_KEYS = ("IdleBacklightDimBatterySeconds", "IdleBacklightDimACSeconds",
              "IdleSuspendBatterySeconds", "IdleSuspendACSeconds")


def read_power_settings(paths):
    for path in paths:
        try:
            with open(path, encoding="utf-8") as stream:
                text = re.sub(r'(?m)^\s*//.*$', '', stream.read())
            values = []
            for key in POWER_KEYS:
                matches = re.findall(r'"' + key + r'"\s+"(\d+)"', text)
                if not matches or len(set(matches)) != 1:
                    break
                value = int(matches[0])
                if not 0 <= value <= 86400:
                    break
                values.append(value)
            if len(values) == 4:
                return values
        except (OSError, UnicodeError):
            continue
    raise RuntimeError("无法读取原来的 Steam 电源设置；请先在 Steam 设置 → 电源中设置息屏和休眠时间，再重试")


class PowerJournal:
    def __init__(self, state_dir, home):
        self.path = os.path.join(state_dir, "smb-download-power.json")
        self.paths = [os.path.join(home, p) for p in (
            ".local/share/Steam/config/config.vdf", ".steam/steam/config/config.vdf")]
        self.lock = threading.RLock()
        self.snapshot = SmbDownloadService._read(self.path, {}).get("snapshot")
        if self.snapshot is not None and (not isinstance(self.snapshot, list) or
                len(self.snapshot) != 4 or any(type(v) is not int or not 0 <= v <= 86400 for v in self.snapshot)):
            raise RuntimeError("亮屏恢复记录损坏，请检查 smb-download-power.json")
        self.active = False
        self.error = ""

    def state(self, service):
        with service.lock:
            wanted = bool(service.config.get("keep_awake", True) and not service.closed and
                          service.active_task_id and service.thread and service.thread.is_alive())
        with self.lock:
            return dict(wanted=wanted, snapshot=self.snapshot, active=self.active, error=self.error)

    def begin(self):
        with self.lock:
            if self.snapshot is None:
                snapshot = read_power_settings(self.paths)
                atomic_json(self.path, {"snapshot": snapshot})
                self.snapshot = snapshot
            return list(self.snapshot)

    def report(self, active=False, error="", restored=False):
        with self.lock:
            if restored:
                atomic_json(self.path, {})
                self.snapshot = None
            self.active = bool(active)
            self.error = str(error)[:500]
            return True
