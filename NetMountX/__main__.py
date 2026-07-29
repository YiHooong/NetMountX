# -*- coding: utf-8 -*-
"""入口: 命令行解析 -> 自检 或 启动 GUI。"""

import argparse
import logging
import logging.handlers
import os
import sys

from . import APP_NAME, APP_TITLE, CONFIG_DIR, LOG_FILE
from .autostart import migrate_from_old_app
from .gui import GUI_IMPORT_ERROR

log = logging.getLogger(APP_NAME)


def setup_file_logging() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
    ))
    log.addHandler(fh)


def main() -> None:
    ap = argparse.ArgumentParser(description=APP_TITLE)
    ap.add_argument(
        "--minimized", action="store_true",
        help="启动后最小化到托盘(供开机自启)",
    )
    ap.add_argument(
        "--selftest", action="store_true",
        help="运行自检后退出(无需 GUI 依赖)",
    )
    args = ap.parse_args()

    migrate_from_old_app()

    if args.selftest:
        from .selftest import selftest
        sys.exit(selftest())

    if GUI_IMPORT_ERROR is not None:
        print("缺少 GUI 依赖, 请先执行:  pip install PyQt6-Fluent-Widgets")
        print(f"导入错误: {GUI_IMPORT_ERROR}")
        sys.exit(1)

    setup_file_logging()

    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QIcon
    from qfluentwidgets import Theme, setTheme

    from .gui import MainWindow, make_app_icon

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_app_icon())
    app.setQuitOnLastWindowClosed(False)
    setTheme(Theme.AUTO)
    w = MainWindow(minimized=args.minimized)
    if not args.minimized:
        w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
