# -*- coding: utf-8 -*-
"""自检: 验证各子系统能在当前环境正常工作。不依赖 GUI。"""

import os
import queue
import threading
from typing import Any

from . import CONFIG_DIR, __version__
from .autostart import _autostart_command
from .config import Config
from .core import (
    find_unmanaged_drives,
    get_local_networks_safe,
    get_mapped_remote,
    in_same_subnet,
    list_system_mappings,
    same_unc,
    scan_system_network_drives,
    server_from_unc,
    used_drive_letters,
)
from .monitor import Monitor, NetworkChangeWatcher
from .gui import (
    initial_window_size,
    responsive_column_widths,
    table_minimum_content_width,
)


def selftest() -> int:
    results: list[tuple[str, bool, str]] = []

    def t(name: str, fn: callable) -> None:
        try:
            detail = str(fn() or "")
            results.append((name, True, detail))
        except Exception as e:
            results.append((name, False, f"{e.__class__.__name__}: {e}"))

    def t_config() -> str:
        cfg = Config.load()
        orig = cfg.snapshot()
        try:
            cfg.upsert({
                "letter": "Z", "path": r"\\NAS\share", "username": "u",
                "mode": "reachable", "enabled": True,
            })
            cfg.save()
            cfg2 = Config.load()
            assert cfg2.get("Z"), "配置回读失败"
        finally:
            cfg.drives = orig
            cfg.save()
        return f"配置目录 {CONFIG_DIR}"

    def t_wnet() -> None:
        get_mapped_remote("Z9")   # 不应抛异常

    def t_unc() -> None:
        assert server_from_unc(r"\\NAS\share\dir") == "NAS"
        assert server_from_unc(r"\\192.168.1.10\docs") == "192.168.1.10"
        assert same_unc(r"\\NAS\Share", r"\\nas\share\\")

    def t_subnet() -> None:
        assert in_same_subnet("192.168.1.20", [("192.168.1.5", 24)])
        assert not in_same_subnet("10.0.0.20", [("192.168.1.5", 24)])

    def t_nets() -> str:
        nets = get_local_networks_safe()
        assert nets is not None, "本机网络信息获取失败"
        return f"发现 {len(nets)} 个 IPv4 接口"

    def t_watcher() -> None:
        fired = threading.Event()
        w = NetworkChangeWatcher(fired.set)
        assert w.start(), "NotifyIpInterfaceChange 注册失败"
        w.stop()

    def t_autostart_cmd() -> str:
        cmd = _autostart_command()
        assert "--minimized" in cmd
        return cmd

    def t_used_letters() -> str:
        used = used_drive_letters()
        assert "C" in used, "C 盘应被识别为已占用"
        free = [chr(c) for c in range(ord("D"), ord("Z") + 1) if chr(c) not in used]
        assert free, "应存在可用盘符"
        return f"已占用 {' '.join(sorted(used))} -> 下一个可用 {free[0]}"

    def t_mappings() -> str:
        maps = list_system_mappings()
        return (
            f"当前系统已挂载 {len(maps)} 个网络驱动器"
            + (": " + ", ".join(f"{l}:->{u}" for l, u in maps) if maps else "")
        )

    def t_auto_mount_default() -> None:
        assert Config().settings.get("auto_mount_on_start") is True, \
            "默认应开启启动自动挂载"

    def t_ignore_list() -> None:
        cfg = Config.load()
        orig = list(cfg.ignored)
        cfg.add_ignore(r"\\SelfTestHost\Share")
        cfg.save()
        try:
            cfg2 = Config.load()
            assert cfg2.is_ignored(r"\\selftesthost\share"), \
                "忽略列表回读/规范化失败"
            assert not cfg2.is_ignored(r"\\other\share"), \
                "未忽略的设备被误判"
        finally:
            cfg.ignored = orig
            cfg.save()

    def t_startup_scan() -> str:
        items = scan_system_network_drives()
        unmanaged = find_unmanaged_drives(set(), [])
        return (
            f"系统网络驱动器 {len(items)} 个, 未管理 {len(unmanaged)} 个"
            + (": " + ", ".join(
                f"{i['letter']}:->{i['unc']}"
                f"({'已连接' if i['connected'] else '未挂载'})"
                for i in unmanaged
            ) if unmanaged else "")
        )

    def t_per_drive_auto_mount() -> None:
        """每盘自动挂载开关 + 启动第一轮开关叠加逻辑验证。
        通过直接替换 Monitor 实例方法来模拟, 避免 globals() 猴子补丁。"""
        mon = Monitor(Config(), queue.Queue())
        calls: list[str] = []

        saved_check = Monitor._check_reachable
        saved_do_mount = Monitor._do_mount
        # 直接打桩 Monitor 类方法 (仅影响 mon 实例的 bound method)
        mon._check_reachable = lambda d, n: (True, "测试可达")  # type: ignore[assignment]
        mon._do_mount = lambda d, manual=False: calls.append(str(d["letter"]))  # type: ignore[assignment]
        try:
            mon._reconcile_drive(
                {"letter": "Y", "path": r"\\NAS\a", "auto_mount": False},
                None, allow_mount=True,
            )
            assert not calls, "该盘自动挂载关闭时不应自动挂载"
            mon._reconcile_drive(
                {"letter": "Y", "path": r"\\NAS\a", "auto_mount": True},
                None, allow_mount=True,
            )
            assert calls == ["Y"], "该盘自动挂载开启时应自动挂载"
            calls.clear()
            mon._reconcile_drive(
                {"letter": "Y", "path": r"\\NAS\a", "auto_mount": True},
                None, allow_mount=False,
            )
            assert not calls, "启动第一轮禁用挂载时不应挂载"
        finally:
            Monitor._check_reachable = saved_check  # type: ignore[assignment]
            Monitor._do_mount = saved_do_mount  # type: ignore[assignment]

    def t_ui_layout_rules() -> None:
        """窗口和表格的响应式尺寸约束不依赖 GUI 环境。"""
        assert table_minimum_content_width() >= 855, "表格列最小宽度不足"
        assert initial_window_size(False) == (980, 660)
        assert initial_window_size(True) == (980, 660)
        narrow = responsive_column_widths(700, range(6))
        assert narrow[1] == 160 and narrow[2] == 280, "内容列不应被压缩"

    t("配置读写", t_config)
    t("WNet 映射查询", t_wnet)
    t("UNC 解析", t_unc)
    t("子网判断", t_subnet)
    t("本机网络获取", t_nets)
    t("网络变化监听注册", t_watcher)
    t("自启动命令构造", t_autostart_cmd)
    t("已用盘符识别", t_used_letters)
    t("枚举已挂载驱动器", t_mappings)
    t("启动自动挂载默认值", t_auto_mount_default)
    t("忽略列表读写", t_ignore_list)
    t("启动扫描(未管理设备)", t_startup_scan)
    t("每盘自动挂载开关", t_per_drive_auto_mount)
    t("界面尺寸约束", t_ui_layout_rules)

    def _safe_print(s: str) -> None:
        """跨编码安全打印: 优先 stdout 编码, 失败则转 ASCII。"""
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", errors="replace").decode("ascii"))

    _safe_print("=" * 60)
    ok = True
    for name, passed, detail in results:
        _safe_print(f"[{'PASS' if passed else 'FAIL'}] {name}"
                    + (f"  -- {detail}" if detail else ""))
        ok = ok and passed
    _safe_print("=" * 60)
    _safe_print("ALL PASSED" if ok else "SOME FAILED")
    return 0 if ok else 1
