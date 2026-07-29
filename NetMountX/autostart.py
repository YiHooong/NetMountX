# -*- coding: utf-8 -*-
"""开机自启管理 (HKCU Run 键, 无需管理员) + 旧版迁移。"""

import os
import shutil
import sys
import winreg

from . import APP_NAME, CONFIG_DIR, CONFIG_FILE, LOG_FILE, OLD_APP_NAME
from .constants import REG_RUN_PATH


def _autostart_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    # 指向项目根目录下的顶层 netmountx.py 启动脚本
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(pkg_dir)
    entry = os.path.join(root_dir, "netmountx.py")
    # 兜底: 如果顶层脚本不存在, 使用 python -m NetMountX
    if not os.path.isfile(entry):
        return f'"{exe}" -m NetMountX --minimized'
    return f'"{exe}" "{entry}" --minimized'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(enable: bool) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0, winreg.KEY_SET_VALUE,
    ) as k:
        if enable:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def migrate_from_old_app() -> None:
    """从旧版 NetDriveKeeper 一次性迁移配置目录和开机启动项到 NetMountX。幂等。"""
    old_dir = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~")), OLD_APP_NAME,
    )
    old_cfg = os.path.join(old_dir, "config.json")
    try:
        if os.path.isfile(old_cfg) and not os.path.exists(CONFIG_FILE):
            os.makedirs(CONFIG_DIR, exist_ok=True)
            shutil.copy2(old_cfg, CONFIG_FILE)
            old_log = os.path.join(old_dir, "netdrive_keeper.log")
            if os.path.isfile(old_log) and not os.path.exists(LOG_FILE):
                shutil.copy2(old_log, LOG_FILE)
        # 新配置就位后, 尽力清理旧目录(失败不碍事)
        if os.path.exists(CONFIG_FILE) and os.path.isdir(old_dir):
            shutil.rmtree(old_dir, ignore_errors=True)
    except OSError as e:
        print(f"迁移旧配置目录失败: {e}")
    # 开机启动项: 先写新键名再删旧键名, 避免中间态丢失自启
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as k:
            try:
                winreg.QueryValueEx(k, OLD_APP_NAME)
            except FileNotFoundError:
                return
            # 先写新键再删旧键, 防止 DeleteValue 成功但 SetValueEx 失败导致自启丢失
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
            winreg.DeleteValue(k, OLD_APP_NAME)
    except OSError:
        pass
