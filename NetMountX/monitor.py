# -*- coding: utf-8 -*-
"""后台监控引擎: 网络变化监听 + 驱动器自动调和。"""

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from datetime import datetime
from typing import Any

from .config import Config
from .constants import DriveMode, DriveState, MOUNT_RETRY_COOLDOWN, NET_DEBOUNCE
from .core import (
    get_default_gateway,
    get_local_networks_safe,
    get_mapped_remote,
    in_same_subnet,
    mount_drive,
    resolve_host,
    same_unc,
    server_from_unc,
    tcp_reachable,
    unmount_drive,
)

log = logging.getLogger("NetMountX")


# ---------------------------------------------------------------------------
# 网络变化监听 (IPHLPAPI NotifyIpInterfaceChange)
# ---------------------------------------------------------------------------


class NetworkChangeWatcher:
    """事件驱动的网络变化监听; 注册失败时调用方应退化为纯轮询。"""

    def __init__(self, on_change: callable) -> None:
        self.on_change = on_change
        self._cb_type = ctypes.WINFUNCTYPE(
            None, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD,
        )
        self._cb = self._cb_type(self._handler)   # 必须保持引用
        self._handle = wintypes.HANDLE()
        self.ok = False

    def _handler(self, ctx: Any, row: Any, ntype: int) -> None:
        try:
            self.on_change()
        except Exception:
            pass

    def start(self) -> bool:
        try:
            api = ctypes.windll.iphlpapi
            api.NotifyIpInterfaceChange.restype = wintypes.DWORD
            api.NotifyIpInterfaceChange.argtypes = [
                wintypes.WORD, ctypes.c_void_p, ctypes.c_void_p,
                wintypes.BOOLEAN, ctypes.POINTER(wintypes.HANDLE),
            ]
            rc = api.NotifyIpInterfaceChange(
                0, self._cb, None, False, ctypes.byref(self._handle),
            )
            self.ok = (rc == 0)
        except Exception:
            self.ok = False
        return self.ok

    def stop(self) -> None:
        if self.ok:
            try:
                ctypes.windll.iphlpapi.CancelMibChangeNotify2(self._handle)
            except Exception:
                pass
            self.ok = False


# ---------------------------------------------------------------------------
# Monitor 后台线程
# ---------------------------------------------------------------------------


class Monitor(threading.Thread):
    def __init__(self, cfg: Config, ui_q: queue.Queue) -> None:
        super().__init__(daemon=True, name="Monitor")
        self.cfg = cfg
        self.ui_q = ui_q
        self.tasks: queue.Queue = queue.Queue()
        self.wake = threading.Event()
        self.stop_flag = threading.Event()
        self.net_changed = threading.Event()
        self.mount_fail_ts: dict[str, float] = {}   # letter -> 上次挂载失败时间
        self._session_pw: dict[str, str] = {}        # letter -> 会话内临时密码(不落盘)
        self._pw_lock = threading.Lock()             # 保护 session_pw 的线程安全
        self.skip_mount_once = False  # "启动自动挂载"关闭时, 第一轮检测只检测不挂载
        self.watcher = NetworkChangeWatcher(self._on_net_event)
        self.last_check = ""

    # -- session_pw 线程安全接口 ---------------------------------------------

    def set_session_pw(self, letter: str, password: str) -> None:
        """UI 线程调用, 设置会话内临时密码。"""
        with self._pw_lock:
            self._session_pw[letter.upper()] = password

    def _get_session_pw(self, letter: str) -> str:
        """Monitor 线程内部读取密码。"""
        with self._pw_lock:
            return self._session_pw.get(letter.upper(), "")

    # -- 外部接口 ------------------------------------------------------------

    def trigger(self, reason: str = "手动刷新") -> None:
        log.info("触发检测: %s", reason)
        self.wake.set()

    def request_stop(self) -> None:
        self.stop_flag.set()
        self.wake.set()

    def manual(self, action: str, letter: str) -> None:
        self.tasks.put((action, letter))
        self.wake.set()

    def _on_net_event(self) -> None:
        self.net_changed.set()
        self.wake.set()

    # -- 主循环 --------------------------------------------------------------

    def run(self) -> None:
        if self.watcher.start():
            log.info(
                "已注册系统网络变化监听 (事件驱动 + %ds 兜底轮询)",
                self._poll_interval(),
            )
        else:
            log.warning(
                "注册网络变化监听失败, 退化为每 %ds 轮询", self._poll_interval(),
            )
        while not self.stop_flag.is_set():
            self.wake.wait(timeout=self._poll_interval())
            self.wake.clear()
            if self.stop_flag.is_set():
                break
            self._drain_tasks()
            if self.net_changed.is_set():
                self.net_changed.clear()
                time.sleep(NET_DEBOUNCE)
                self.reconcile_all("网络发生变化")
            else:
                # 程序启动的第一轮检测尊重"启动自动挂载"开关
                allow = not self.skip_mount_once
                self.skip_mount_once = False
                self.reconcile_all("周期检测", quiet=True, allow_mount=allow)
        self.watcher.stop()

    def _poll_interval(self) -> int:
        try:
            return max(15, int(self.cfg.settings.get("poll_interval", 60)))  # type: ignore[arg-type]
        except Exception:
            return 60

    def _drain_tasks(self) -> None:
        while True:
            try:
                action, letter = self.tasks.get_nowait()
            except queue.Empty:
                return
            try:
                drive = self.cfg.get(letter)
                if action == "mount" and drive:
                    self._do_mount(drive, manual=True)
                elif action == "unmount":
                    ok, msg = unmount_drive(letter)
                    log.info("手动卸载 %s: -> %s", letter, msg)
                    self._push_status(
                        letter,
                        DriveState.UNMOUNTED if ok else DriveState.ERROR,
                        msg, manual=True,
                    )
                self._push_netinfo()
            except Exception as e:
                log.error("处理任务 %s/%s 时出错: %s", action, letter, e)

    # -- 检测与调和 -----------------------------------------------------------

    def reconcile_all(
        self, reason: str, quiet: bool = False, allow_mount: bool = True,
    ) -> None:
        drives = self.cfg.snapshot()
        if not drives:
            return
        nets = get_local_networks_safe()
        if nets is None:
            log.warning("无法获取本机网络信息, 同子网模式的驱动器本轮跳过")
        for drive in drives:
            if not drive.get("enabled", True):
                self._push_status(str(drive["letter"]), DriveState.DISABLED, "已停用")
                continue
            try:
                self._reconcile_drive(drive, nets, allow_mount)
            except Exception as e:
                log.error("检测 %s: 出错: %s", drive["letter"], e)
                self._push_status(str(drive["letter"]), DriveState.ERROR, str(e))
        self.last_check = datetime.now().strftime("%H:%M:%S")
        self._push_netinfo()
        if not quiet:
            log.info("本轮检测完成 (%s)", reason)

    def _check_reachable(
        self, drive: dict[str, object], nets: list[tuple[str, int]] | None,
    ) -> tuple[bool | None, str]:
        """返回 (是否同网络/可连接, 原因说明)。"""
        host = server_from_unc(str(drive["path"]))
        if drive.get("mode", DriveMode.REACHABLE) == DriveMode.SUBNET:
            if nets is None:
                return None, "本机网络信息不可用"
            ip = resolve_host(host)
            if not ip:
                return False, f"无法解析服务器 {host}"
            if not in_same_subnet(ip, nets):
                return False, f"服务器 {ip} 不在同一子网"
            if tcp_reachable(host):
                return True, f"服务器 {ip} 在同一子网, SMB 可连接"
            return False, f"服务器 {ip} 网段匹配但 SMB 不可连接(设备不在本网络)"
        ip = resolve_host(host)
        target = ip or host
        ok = tcp_reachable(target)
        return ok, f"服务器 {host} ({ip or '解析失败'}) {'可' if ok else '不可'}连接 (SMB)"

    def _reconcile_drive(
        self, drive: dict[str, object], nets: list[tuple[str, int]] | None,
        allow_mount: bool = True,
    ) -> None:
        letter = str(drive["letter"]).upper()
        unc = str(drive["path"])
        current = get_mapped_remote(letter)
        reachable, why = self._check_reachable(drive, nets)
        if reachable is None:      # 信息不足, 保持现状最安全
            self._push_status(letter, DriveState.CHECKING, f"{why}, 本轮保持现状")
            return
        if reachable:
            can_mount = allow_mount and bool(drive.get("auto_mount", True))
            if current and same_unc(current, unc):
                self._push_status(letter, DriveState.MOUNTED, f"已挂载 ({why})")
            elif current:
                if drive.get("force") and can_mount:
                    log.warning("%s: 被 %s 占用, 按配置强制重挂", letter, current)
                    unmount_drive(letter)
                    self._do_mount(drive)
                else:
                    self._push_status(
                        letter, DriveState.CONFLICT,
                        f"盘符被 {current} 占用, 未处理",
                    )
            elif can_mount:
                self._do_mount(drive)
            elif not drive.get("auto_mount", True):
                self._push_status(
                    letter, DriveState.UNMOUNTED,
                    f"服务器可达, 该盘自动挂载已关闭 ({why})",
                )
            else:
                self._push_status(
                    letter, DriveState.UNMOUNTED,
                    f"服务器可达, 启动自动挂载已关闭, 本轮不挂载 ({why})",
                )
        else:
            if current and same_unc(current, unc):
                ok, msg = unmount_drive(letter)
                log.info("%s: 网络不可达 (%s), %s", letter, why, msg)
                self._push_status(
                    letter,
                    DriveState.UNMOUNTED if ok else DriveState.ERROR,
                    f"网络不可达, {msg}",
                )
            elif current:
                self._push_status(
                    letter, DriveState.CONFLICT,
                    f"盘符被 {current} 占用, 不处理",
                )
            else:
                self._push_status(letter, DriveState.UNMOUNTED, why)

    def _do_mount(
        self, drive: dict[str, object], manual: bool = False,
    ) -> None:
        letter = str(drive["letter"]).upper()
        unc = str(drive["path"])
        if not manual:
            last_fail = self.mount_fail_ts.get(letter, 0)
            if time.time() - last_fail < MOUNT_RETRY_COOLDOWN:
                self._push_status(letter, DriveState.ERROR, "上次挂载失败, 冷却中")
                return
        pw = self._get_session_pw(letter)
        ok, msg = mount_drive(
            letter, unc, str(drive.get("username", "")), pw,
            save_cred=bool(drive.get("save_cred", True)),
        )
        if ok:
            self.mount_fail_ts.pop(letter, None)
            log.info("%s: -> %s %s", letter, unc, msg)
            self._push_status(letter, DriveState.MOUNTED, msg, manual=manual)
        else:
            self.mount_fail_ts[letter] = time.time()
            log.error("挂载 %s: (%s) 失败: %s", letter, unc, msg)
            self._push_status(
                letter, DriveState.ERROR, f"挂载失败: {msg}", manual=manual,
            )

    # -- 状态推送 -------------------------------------------------------------

    def _push_status(
        self, letter: str, state: str, text: str, manual: bool = False,
    ) -> None:
        self.ui_q.put(("status", {
            "letter": letter.upper(), "state": state, "text": text,
            "manual": manual,
            "time": datetime.now().strftime("%H:%M:%S"),
        }))

    def _push_netinfo(self) -> None:
        nets = get_local_networks_safe() or []
        gw = get_default_gateway()
        self.ui_q.put(("netinfo", {
            "ips": ", ".join(ip for ip, _ in nets) or "无",
            "gateway": gw or "无",
            "last": self.last_check,
        }))
