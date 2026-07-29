# -*- coding: utf-8 -*-
"""
NetMountX - Windows 网络驱动器自动挂载/卸载工具
=====================================================

功能
----
1. 监听系统网络变化(IPHLPAPI 事件 + 兜底轮询), 网络切换后立即检测
   网络驱动器服务器是否在当前网络下可连接(或严格同一子网 + SMB 实测,
   避免搬到同网段的另一个网络时误判设备存在);
2. 不在同一网络 -> 自动卸载; 恢复可达 -> 自动重新挂载;
3. GUI 添加/编辑/删除网络驱动器, 凭据通过 cmdkey 存入 Windows 凭据管理器;
4. 支持开机自动运行(注册表 HKCU Run 键, 无需管理员), 开机时仅在网络匹配时挂载;
5. 每次启动时扫描系统中未被本软件管理的网络驱动器(含已断开但保留映射的),
   发现未管理且未忽略的设备即弹出导入对话框;
6. "启动自动挂载"开关: 关闭后软件启动的第一轮检测只检测不自动挂载;
7. 主表格最右列是每盘独立的"自动挂载"开关: 关闭后该盘不再被自动挂载;
8. 点击表格"状态说明"列可弹出该盘的状态详情窗口。

GUI: PyQt6 + PyQt-Fluent-Widgets
依赖安装: pip install PyQt6-Fluent-Widgets

命令行
------
    python -m NetMountX             启动 GUI
    python -m NetMountX --minimized 启动并最小化到托盘(供开机自启使用)
    python -m NetMountX --selftest  运行自检(不启动 GUI, 无需 GUI 依赖)
	    python netmountx.py            等价于 python -m NetMountX

	打包 exe (Windows 上运行)
	-------------------------
	    pip install pyinstaller PyQt6-Fluent-Widgets
	    build_exe.bat                   一键打包
	    产物: dist\\NetMountX.exe  (单文件, 图标已内嵌)

配置文件: %APPDATA%\\NetMountX\\config.json  (不保存明文密码)
日志文件: %APPDATA%\\NetMountX\\netmountx.log
"""

import os
import sys

__version__ = "1.4.2"
APP_NAME = "NetMountX"
OLD_APP_NAME = "NetDriveKeeper"   # 旧版名称, 用于一次性迁移配置目录和自启动项
APP_TITLE = "NetMountX - 网络驱动器自动挂载"

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LOG_FILE = os.path.join(CONFIG_DIR, "netmountx.log")


def resource_path(name: str) -> str:
    """资源文件路径: PyInstaller 打包后从 _MEIPASS 读取, 否则从脚本目录读取。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)
