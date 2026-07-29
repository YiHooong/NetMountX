# -*- coding: utf-8 -*-
"""常量与枚举定义。"""

from enum import StrEnum


class DriveMode(StrEnum):
    """挂载策略模式。"""
    REACHABLE = "reachable"   # 自动检测 (可连接即挂载)
    SUBNET = "subnet"         # 严格同一子网 + SMB 实测


class DriveState(StrEnum):
    """驱动器当前状态。"""
    MOUNTED = "mounted"
    UNMOUNTED = "unmounted"
    ERROR = "error"
    CONFLICT = "conflict"
    CHECKING = "checking"
    DISABLED = "disabled"


# 挂载策略显示标签
MODE_LABELS: dict[str, str] = {
    DriveMode.REACHABLE: "自动检测 (可连接即挂载)",
    DriveMode.SUBNET: "严格同一子网",
}

# 状态显示标签与颜色
STATE_LABELS: dict[str, tuple[str, str]] = {
    DriveState.MOUNTED: ("已挂载", "#1b7f37"),
    DriveState.UNMOUNTED: ("未挂载", "#6e7781"),
    DriveState.ERROR: ("异常", "#cf222e"),
    DriveState.CONFLICT: ("盘符冲突", "#bf8700"),
    DriveState.CHECKING: ("检测中", "#0969da"),
    DriveState.DISABLED: ("已停用", "#6e7781"),
    "": ("待检测", "#6e7781"),
}

# 超时/间隔常量
CREATE_NO_WINDOW = 0x08000000
MOUNT_RETRY_COOLDOWN = 90       # 挂载失败后的重试冷却(秒)
TCP_TIMEOUT = 1.5               # SMB 端口探测超时(秒)
NET_DEBOUNCE = 3                # 网络变化事件去抖(秒)

# 默认设置
DEFAULT_SETTINGS: dict[str, object] = {
    "poll_interval": 60,
    "tray": True,
    "auto_mount_on_start": True,
}

# 注册表路径
REG_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
