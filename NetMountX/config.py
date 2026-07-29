# -*- coding: utf-8 -*-
"""配置管理: JSON 文件读写, 驱动器增删改查, 忽略列表。"""

import json
import logging
import os
import shutil
import threading
from typing import Any

from . import CONFIG_DIR, CONFIG_FILE, __version__
from .constants import DEFAULT_SETTINGS
from .core import norm_unc

log = logging.getLogger("NetMountX")


class Config:
    """线程安全的配置管理器。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.drives: list[dict[str, object]] = []
        self.settings: dict[str, object] = dict(DEFAULT_SETTINGS)
        self.ignored: list[str] = []   # 启动扫描中被忽略的设备(规范化 UNC)

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.drives = [
                d for d in data.get("drives", [])
                if d.get("letter") and d.get("path")
            ]
            cfg.settings.update(data.get("settings", {}))
            for u in data.get("ignored", []):
                if isinstance(u, str) and norm_unc(u) not in cfg.ignored:
                    cfg.ignored.append(norm_unc(u))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("读取配置失败, 使用空配置: %s", e)
        return cfg

    def save(self) -> None:
        with self.lock:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            data: dict[str, object] = {
                "version": __version__,
                "drives": self.drives,
                "settings": self.settings,
                "ignored": self.ignored,
            }
            # 绝不把密码写入磁盘
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            # 覆盖前保留上一版的备份, 防止内存数据异常导致永久丢失
            if os.path.exists(CONFIG_FILE):
                try:
                    shutil.copy2(CONFIG_FILE, CONFIG_FILE + ".bak")
                except OSError:
                    pass
            os.replace(tmp, CONFIG_FILE)

    def snapshot(self) -> list[dict[str, object]]:
        with self.lock:
            return json.loads(json.dumps(self.drives))

    def get(self, letter: str) -> dict[str, object] | None:
        with self.lock:
            for d in self.drives:
                if str(d["letter"]).upper() == letter.upper():
                    return dict(d)
        return None

    def upsert(self, drive: dict[str, object]) -> None:
        with self.lock:
            target = str(drive["letter"]).upper()
            for i, d in enumerate(self.drives):
                if str(d["letter"]).upper() == target:
                    self.drives[i] = drive
                    return
            self.drives.append(drive)
            self.drives.sort(key=lambda x: str(x["letter"]).upper())

    def remove(self, letter: str) -> None:
        with self.lock:
            self.drives = [
                d for d in self.drives
                if str(d["letter"]).upper() != letter.upper()
            ]

    def is_ignored(self, unc: str) -> bool:
        with self.lock:
            return norm_unc(unc) in self.ignored

    def add_ignore(self, unc: str) -> None:
        with self.lock:
            n = norm_unc(unc)
            if n and n not in self.ignored:
                self.ignored.append(n)
