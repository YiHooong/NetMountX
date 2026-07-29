# -*- coding: utf-8 -*-
"""
底层工具: 进程调用、WNet API、网络检测、挂载/卸载、盘符枚举。
纯函数, 不依赖 GUI 或配置。
"""

import ctypes
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
from ctypes import wintypes
from typing import Any

from .constants import CREATE_NO_WINDOW, TCP_TIMEOUT

log = logging.getLogger("NetMountX")

# ---------------------------------------------------------------------------
# 进程 / 编码
# ---------------------------------------------------------------------------


def _decode_console(data: bytes) -> str:
    """net use / ipconfig 等命令在中文系统输出 GBK, 英文系统输出 ASCII。"""
    for enc in ("utf-8", "mbcs", "gbk"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", "replace")


def run_hidden(cmd: list[str], timeout: float = 15) -> tuple[int, str]:
    """静默执行命令, 返回 (returncode, 输出文本)。绝不弹出黑窗。"""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    try:
        p = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW, startupinfo=si,
        )
        out = (p.stdout or b"")
        if p.stderr:
            out += (b"\n" if out else b"") + p.stderr
        return p.returncode, _decode_console(out).strip()
    except subprocess.TimeoutExpired:
        return -1, "命令执行超时"
    except Exception as e:
        return -1, str(e)


# ---------------------------------------------------------------------------
# WNet API (盘符映射查询)
# ---------------------------------------------------------------------------

_mpr: Any = None


def _get_mpr() -> Any:
    global _mpr
    if _mpr is None:
        _mpr = ctypes.WinDLL("mpr")
    return _mpr


def get_mapped_remote(letter: str) -> str | None:
    """查询盘符当前映射到的 UNC 路径; 未映射返回 None。
    使用 WNetGetConnectionW, 与系统语言无关。"""
    letter = letter.upper().rstrip(":") + ":"
    buf = ctypes.create_unicode_buffer(2048)
    size = wintypes.DWORD(2048)
    rc = _get_mpr().WNetGetConnectionW(letter, buf, ctypes.byref(size))
    if rc == 0:  # NO_ERROR
        return buf.value
    return None   # 2250 = ERROR_NOT_CONNECTED 等


def used_drive_letters() -> set[str]:
    """已占用盘符集合: 本地驱动器 + 网络映射(含已断开但仍占用盘符的映射)。"""
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    used = {chr(65 + i) for i in range(26) if (mask >> i) & 1}
    for c in range(ord("A"), ord("Z") + 1):
        letter = chr(c)
        if letter not in used and get_mapped_remote(letter):
            used.add(letter)
    return used


def list_system_mappings() -> list[tuple[str, str]]:
    """枚举当前系统中所有已映射的网络驱动器 -> [(letter, unc), ...]。
    含已断开但仍保留映射的网络驱动器。"""
    result: list[tuple[str, str]] = []
    for c in range(ord("A"), ord("Z") + 1):
        letter = chr(c)
        remote = get_mapped_remote(letter)
        if remote:
            result.append((letter, remote))
    return result


def drive_accessible(letter: str) -> bool:
    """盘符当前可访问(已连接); 已断开但保留映射的网络驱动器返回 False。"""
    root = letter.upper().rstrip(":") + ":\\"
    try:
        return os.path.isdir(root)
    except Exception:
        return False


def list_remembered_mappings() -> list[tuple[str, str]]:
    """读取注册表 HKCU\\Network 下的持久映射 -> [(letter, unc), ...]。
    持久映射即使当前已断开(资源管理器中显示红叉)也会保留在这里。"""
    import winreg  # Windows-only, 惰性导入以兼容 Linux CI

    result: list[tuple[str, str]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Network") as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if len(name) != 1 or not name.isalpha():
                    continue
                try:
                    with winreg.OpenKey(root, name) as k:
                        remote, _ = winreg.QueryValueEx(k, "RemotePath")
                    if remote:
                        result.append((name.upper(), remote))
                except OSError:
                    continue
    except OSError:
        pass
    return result


def scan_system_network_drives() -> list[dict[str, object]]:
    """汇总系统中所有网络驱动器映射 -> [{letter, unc, connected}, ...]。
    来源: WNet 枚举 + 注册表持久映射; connected 表示当前是否真实可访问。"""
    found: dict[str, dict[str, object]] = {}
    for letter, unc in list_system_mappings():
        found[letter.upper()] = {
            "letter": letter.upper(), "unc": unc,
            "connected": drive_accessible(letter),
        }
    for letter, unc in list_remembered_mappings():
        if letter not in found:
            found[letter] = {
                "letter": letter, "unc": unc,
                "connected": drive_accessible(letter),
            }
    return [found[k] for k in sorted(found)]


def find_unmanaged_drives(
    managed_letters: set[str], ignored_uncs: list[str],
) -> list[dict[str, object]]:
    """从系统映射中筛出既未被本软件管理、也未被用户忽略的网络驱动器。"""
    ignored = {norm_unc(u) for u in ignored_uncs}
    items: list[dict[str, object]] = []
    for it in scan_system_network_drives():
        letter = str(it["letter"])
        if letter in managed_letters:
            continue
        if norm_unc(str(it["unc"])) in ignored:
            continue
        items.append(it)
    return items


# ---------------------------------------------------------------------------
# UNC 解析与规范化
# ---------------------------------------------------------------------------


def server_from_unc(unc: str) -> str:
    m = re.match(r"^[\\/]{2}([^\\/]+)", unc.strip())
    if not m:
        raise ValueError(f"非法的网络路径: {unc!r} (应形如 \\\\服务器\\共享名)")
    return m.group(1)


def norm_unc(s: str) -> str:
    """规范化 UNC 用于比较/去重: 统一反斜杠、去尾部斜杠、转小写。"""
    return str(s).strip().replace("/", "\\").rstrip("\\").lower()


def same_unc(a: str, b: str) -> bool:
    return norm_unc(a) == norm_unc(b)


# ---------------------------------------------------------------------------
# 网络检测
# ---------------------------------------------------------------------------


def resolve_host(host: str) -> str | None:
    """解析主机名 -> IPv4。先试系统解析, 再借助 ping 的输出(可覆盖 NetBIOS 名称)。"""
    try:
        infos = socket.getaddrinfo(host, 445, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    rc, out = run_hidden(["ping", "-4", "-n", "1", "-w", "800", host], timeout=5)
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", out)
    return m.group(1) if m else None


def tcp_reachable(
    host: str, ports: tuple[int, ...] = (445, 139),
    timeout: float = TCP_TIMEOUT, rounds: int = 2,
) -> bool:
    for _ in range(rounds):
        for port in ports:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except Exception:
                continue
    return False


def in_same_subnet(ip_str: str, networks: list[tuple[str, int]]) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for lip, plen in networks:
        try:
            if addr in ipaddress.ip_network(f"{lip}/{plen}", strict=False):
                return True
        except ValueError:
            continue
    return False


def list_local_networks() -> list[tuple[str, int]]:
    """返回 [(本机IPv4, 前缀长度), ...]。优先 PowerShell CIM(与语言无关),
    失败时回退解析 ipconfig 输出。"""
    ps = (
        "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
        "Where-Object {$_.IPAddress -ne '127.0.0.1'} | "
        "Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress"
    )
    rc, out = run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=20,
    )
    if rc == 0 and out:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        nets: list[tuple[str, int]] = []
        for it in data or []:
            ip = it.get("IPAddress")
            plen = it.get("PrefixLength")
            if ip and plen is not None:
                nets.append((ip, int(plen)))
        return nets
    raise RuntimeError(f"PowerShell 查询失败: {out}")


def _list_networks_ipconfig() -> list[tuple[str, int]]:
    rc, out = run_hidden(["ipconfig", "/all"], timeout=10)
    nets: list[tuple[str, int]] = []
    pending_ip: str | None = None
    for line in out.splitlines():
        m = re.search(r"IPv4.*?:\s*(\d{1,3}(?:\.\d{1,3}){3})", line)
        if m:
            pending_ip = m.group(1)
            continue
        if pending_ip and (("Subnet Mask" in line) or ("掩码" in line)):
            m2 = re.search(r":\s*(\d{1,3}(?:\.\d{1,3}){3})", line)
            if m2:
                plen = ipaddress.IPv4Network(f"0.0.0.0/{m2.group(1)}").prefixlen
                nets.append((pending_ip, plen))
                pending_ip = None
    return nets


def get_local_networks_safe() -> list[tuple[str, int]] | None:
    """成功返回列表(可为空), 彻底失败返回 None。"""
    try:
        return list_local_networks()
    except Exception:
        try:
            return _list_networks_ipconfig()
        except Exception:
            return None


def get_default_gateway() -> str:
    ps = (
        "Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | "
        "Sort-Object RouteMetric,InterfaceMetric | "
        "Select-Object -First 1 -ExpandProperty NextHop"
    )
    rc, out = run_hidden(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=15,
    )
    if rc == 0 and re.match(r"^\d{1,3}(\.\d{1,3}){3}$", out.strip()):
        return out.strip()
    rc, out = run_hidden(["route", "print", "-4"], timeout=10)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "0.0.0.0" and parts[1] == "0.0.0.0":
            return parts[2]
    return ""


# ---------------------------------------------------------------------------
# 挂载 / 卸载
# ---------------------------------------------------------------------------


def mount_drive(
    letter: str, unc: str, username: str = "", password: str = "",
    save_cred: bool = True,
) -> tuple[bool, str]:
    """返回 (成功与否, 说明)。非持久映射(/persistent:no), 生命周期由本程序管理。"""
    letter = letter.upper().rstrip(":")
    remote = get_mapped_remote(letter)
    if remote:
        if same_unc(remote, unc):
            return True, "已挂载"
        return False, f"盘符 {letter}: 已被 {remote} 占用"
    if username and password:
        if save_cred:
            host = server_from_unc(unc)
            run_hidden(
                ["cmdkey", f"/add:{host}", f"/user:{username}", f"/pass:{password}"],
                timeout=10,
            )
        cmd: list[str] = [
            "net", "use", f"{letter}:", unc, password, f"/user:{username}",
            "/persistent:no",
        ]
    else:
        # 匿名共享, 或凭据此前已存入 Windows 凭据管理器
        cmd = ["net", "use", f"{letter}:", unc, "/persistent:no"]
    rc, out = run_hidden(cmd, timeout=30)
    if rc == 0 and get_mapped_remote(letter):
        return True, "挂载成功"
    return False, (out.splitlines()[-1] if out else f"net use 返回码 {rc}")


def unc_to_mountpoints_key(unc: str) -> str:
    """\\server\share -> ##server#share (注册表 MountPoints2 键名)。"""
    clean = unc.replace("/", "\\").strip("\\")
    parts = clean.split("\\", 1)
    server = parts[0]
    share = parts[1] if len(parts) > 1 else ""
    return f"##{server}#{share}"


def set_explorer_label(unc: str, label: str) -> None:
    """设置网络驱动器在 Windows 资源管理器中显示的名称。
    通过注册表 MountPoints2\\_LabelFromReg 实现。
    label 为空则恢复默认名称。"""
    key = unc_to_mountpoints_key(unc)
    reg_path = (
        f"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\"
        f"Explorer\\MountPoints2\\{key}"
    )
    if label.strip():
        subprocess.run(
            ["reg", "add", reg_path, "/v", "_LabelFromReg",
             "/t", "REG_SZ", "/d", label.strip(), "/f"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )
    else:
        subprocess.run(
            ["reg", "delete", reg_path, "/v", "_LabelFromReg", "/f"],
            capture_output=True, creationflags=CREATE_NO_WINDOW,
        )


def unmount_drive(letter: str) -> tuple[bool, str]:
    letter = letter.upper().rstrip(":")
    rc, out = run_hidden(["net", "use", f"{letter}:", "/delete", "/y"], timeout=20)
    if get_mapped_remote(letter) is None:
        return True, "已卸载"
    return False, (out.splitlines()[-1] if out else "卸载失败")
