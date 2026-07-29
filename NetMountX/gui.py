# -*- coding: utf-8 -*-
"""GUI (PyQt6 + PyQt-Fluent-Widgets): 对话框、页面、主窗口、托盘图标。"""

import logging
import os
import queue
import threading

from . import APP_NAME, APP_TITLE, CONFIG_DIR, CONFIG_FILE, LOG_FILE, __version__
from .autostart import autostart_enabled, set_autostart
from .config import Config
from .constants import MODE_LABELS, STATE_LABELS, DriveMode, DriveState
from .core import (
    get_mapped_remote,
    list_system_mappings,
    resolve_host,
    run_hidden,
    same_unc,
    server_from_unc,
    tcp_reachable,
    unmount_drive,
    used_drive_letters,
)
from .monitor import Monitor

log = logging.getLogger("NetMountX")

# ---------------------------------------------------------------------------
# GUI 依赖检查
# ---------------------------------------------------------------------------

GUI_IMPORT_ERROR: Exception | None = None
try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap, QBrush
    from PyQt6.QtWidgets import (
        QAbstractItemView, QApplication, QFormLayout, QHBoxLayout,
        QHeaderView, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget,
    )
    from qfluentwidgets import (
        Action, BodyLabel, CaptionLabel, CheckBox, ComboBox,
        FluentIcon, FluentWindow, InfoBar, InfoBarPosition, LineEdit,
        MessageBox, MessageBoxBase, NavigationItemPosition,
        PasswordLineEdit, PlainTextEdit, PrimaryPushButton, PushButton,
        RoundMenu, SubtitleLabel, SwitchButton, TableWidget, Theme,
        setTheme,
    )
except ImportError as e:   # 缺少 GUI 依赖时仍允许 --selftest 运行
    GUI_IMPORT_ERROR = e


# ---------------------------------------------------------------------------
# QueueLogHandler
# ---------------------------------------------------------------------------


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put(("log", self.format(record)))
        except Exception:
            pass


# 仅在 GUI 可用时定义后续类
if GUI_IMPORT_ERROR is None:

    # -----------------------------------------------------------------------
    # 工具
    # -----------------------------------------------------------------------

    def make_app_icon() -> QIcon:
        """应用图标: 优先加载 netmountx.ico, 缺失时回退到程序绘制图标。"""
        from . import resource_path
        ico = resource_path("netmountx.ico")
        if os.path.exists(ico):
            return QIcon(ico)
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0f6cbd"))
        p.drawRoundedRect(4, 18, 56, 28, 8, 8)
        p.drawRect(14, 46, 36, 8)
        p.setBrush(QColor("white"))
        p.drawEllipse(26, 28, 12, 12)
        p.end()
        return QIcon(pm)

    # -----------------------------------------------------------------------
    # 对话框
    # -----------------------------------------------------------------------

    class DriveDialog(MessageBoxBase):
        """添加/编辑驱动器对话框 (Fluent 风格模态框)。"""

        test_done = pyqtSignal(str, bool)

        def __init__(
            self, parent: QWidget, title: str,
            drive: dict | None = None,
            letters: list[str] | None = None,
            used_letters: list[str] | None = None,
        ) -> None:
            super().__init__(parent)
            drive = drive or {}
            self._letters = list(letters or [])

            self.viewLayout.addWidget(SubtitleLabel(title, self))

            form = QFormLayout()
            form.setSpacing(10)

            # 盘符 + 顺延按钮
            letter_row = QHBoxLayout()
            self.cmb_letter = ComboBox(self)
            if self._letters:
                self.cmb_letter.addItems(self._letters)
            default_letter = str(drive.get("letter") or (self._letters[0] if self._letters else ""))
            if default_letter:
                self.cmb_letter.setCurrentText(default_letter)
            self.cmb_letter.setMinimumWidth(90)
            btn_next = PushButton("顺延到下一个可用", self)
            btn_next.clicked.connect(self._next_free_letter)
            letter_row.addWidget(self.cmb_letter)
            letter_row.addWidget(btn_next)
            letter_row.addStretch(1)
            form.addRow("盘符:", letter_row)
            if used_letters:
                cap = CaptionLabel("已占用: " + " ".join(used_letters), self)
                cap.setTextColor(QColor(0x6e, 0x77, 0x81), QColor(0x9a, 0x9a, 0x9a))
                form.addRow("", cap)

            self.edt_label = LineEdit(self)
            self.edt_label.setPlaceholderText("留空则显示网络路径")
            self.edt_label.setText(str(drive.get("label", "")))
            self.edt_label.setClearButtonEnabled(True)
            form.addRow("名称:", self.edt_label)

            self.edt_path = LineEdit(self)
            self.edt_path.setPlaceholderText(r"形如 \\NAS\共享文件夹")
            self.edt_path.setText(str(drive.get("path", "")))
            self.edt_path.setClearButtonEnabled(True)
            form.addRow("网络路径:", self.edt_path)

            self.edt_user = LineEdit(self)
            self.edt_user.setText(str(drive.get("username", "")))
            self.edt_user.setClearButtonEnabled(True)
            form.addRow("用户名:", self.edt_user)

            self.edt_pw = PasswordLineEdit(self)
            self.edt_pw.setPlaceholderText("留空 = 匿名共享或使用已保存的凭据")
            form.addRow("密码:", self.edt_pw)

            self.chk_save = CheckBox("将凭据保存到 Windows 凭据管理器", self)
            self.chk_save.setChecked(bool(drive.get("save_cred", True)))
            form.addRow("", self.chk_save)

            self.cmb_mode = ComboBox(self)
            self.cmb_mode.addItems(list(MODE_LABELS.values()))
            self.cmb_mode.setCurrentText(
                MODE_LABELS.get(str(drive.get("mode", DriveMode.REACHABLE)))
            )
            form.addRow("挂载策略:", self.cmb_mode)

            self.chk_enabled = CheckBox("启用此项", self)
            self.chk_enabled.setChecked(bool(drive.get("enabled", True)))
            form.addRow("", self.chk_enabled)
            self.chk_force = CheckBox("盘符被其他映射占用时强制重挂 (谨慎)", self)
            self.chk_force.setChecked(bool(drive.get("force", False)))
            form.addRow("", self.chk_force)

            btn_row = QHBoxLayout()
            self.btn_test = PushButton(FluentIcon.WIFI, "测试连接", self)
            self.btn_test.clicked.connect(self._test)
            btn_row.addWidget(self.btn_test)
            btn_row.addStretch(1)
            form.addRow("", btn_row)

            self.viewLayout.addLayout(form)
            self.yesButton.setText("确定")
            self.cancelButton.setText("取消")
            self.widget.setMinimumWidth(560)
            self.test_done.connect(self._on_test_done)

        # -- 盘符顺延 --------------------------------------------------------

        def _next_free_letter(self) -> None:
            if not self._letters:
                return
            cur = self.cmb_letter.currentText().strip().upper()
            idx = (
                (self._letters.index(cur) + 1) % len(self._letters)
                if cur in self._letters else 0
            )
            self.cmb_letter.setCurrentIndex(idx)

        # -- 测试连接 --------------------------------------------------------

        def _test(self) -> None:
            path = self.edt_path.text().strip()
            try:
                host = server_from_unc(path)
            except ValueError as e:
                InfoBar.error(
                    "路径错误", str(e), parent=self,
                    position=InfoBarPosition.TOP, duration=3000,
                )
                return

            def work() -> None:
                try:
                    ip = resolve_host(host)
                    ok = tcp_reachable(host)
                    msg = (
                        f"服务器 {host} ({ip or '解析失败'}) "
                        f"{'SMB 端口可连接' if ok else 'SMB 端口不可连接'}"
                    )
                    self.test_done.emit(msg, ok)
                except Exception as e:
                    self.test_done.emit(f"测试连接异常: {e}", False)

            threading.Thread(target=work, daemon=True).start()

        def _on_test_done(self, msg: str, ok: bool) -> None:
            (InfoBar.success if ok else InfoBar.error)(
                "测试结果", msg, parent=self,
                position=InfoBarPosition.TOP, duration=4000,
            )

        # -- 校验与结果 -------------------------------------------------------

        def _validate(self) -> bool:
            if not self.cmb_letter.currentText().strip():
                InfoBar.warning(
                    "缺少盘符", "请选择盘符", parent=self,
                    position=InfoBarPosition.TOP, duration=3000,
                )
                return False
            try:
                server_from_unc(self.edt_path.text().strip())
            except ValueError as e:
                InfoBar.error(
                    "路径错误", str(e), parent=self,
                    position=InfoBarPosition.TOP, duration=3000,
                )
                return False
            return True

        def result_drive(self) -> tuple[dict[str, object], str]:
            mode: str = (
                DriveMode.SUBNET
                if self.cmb_mode.currentText() == MODE_LABELS[DriveMode.SUBNET]
                else DriveMode.REACHABLE
            )
            return {
                "letter": self.cmb_letter.currentText().strip().upper().rstrip(":"),
                "label": self.edt_label.text().strip(),
                "path": self.edt_path.text().strip(),
                "username": self.edt_user.text().strip(),
                "save_cred": self.chk_save.isChecked(),
                "mode": mode,
                "enabled": self.chk_enabled.isChecked(),
                "force": self.chk_force.isChecked(),
            }, self.edt_pw.text()

    # -----------------------------------------------------------------------

    class ImportDialog(MessageBoxBase):
        """列出系统当前已挂载的网络驱动器, 勾选后导入管理。"""

        def __init__(
            self, parent: QWidget, mappings: list[tuple[str, str]],
            managed_letters: set[str],
        ) -> None:
            super().__init__(parent)
            self.viewLayout.addWidget(
                SubtitleLabel("导入系统已挂载的网络驱动器", self),
            )
            self._checks: list[tuple[str, str, CheckBox]] = []
            if not mappings:
                self.viewLayout.addWidget(
                    BodyLabel("当前系统中没有已挂载的网络驱动器。", self),
                )
                self.yesButton.setEnabled(False)
            for letter, unc in mappings:
                managed = letter in managed_letters
                text = f"{letter}:  →  {unc}" + ("  (已在管理列表)" if managed else "")
                cb = CheckBox(text, self)
                cb.setChecked(not managed)
                cb.setEnabled(not managed)
                self.viewLayout.addWidget(cb)
                self._checks.append((letter, unc, cb))
            self.yesButton.setText("导入")
            self.cancelButton.setText("取消")
            self.widget.setMinimumWidth(480)

        def selected(self) -> list[tuple[str, str]]:
            return [
                (letter, unc) for letter, unc, cb in self._checks
                if cb.isChecked() and cb.isEnabled()
            ]

    # -----------------------------------------------------------------------

    class StartupScanDialog(MessageBoxBase):
        """启动扫描结果: 列出系统中未被本软件管理的网络驱动器。
        勾选的设备将被导入管理并尝试挂载; 点"忽略此设备"后, 以后启动扫描不再提示该设备。"""

        def __init__(
            self, parent: QWidget, items: list[dict], cfg: Config,
        ) -> None:
            super().__init__(parent)
            self.cfg = cfg
            self._rows: list[tuple[str, str, CheckBox, QWidget]] = []
            self.viewLayout.addWidget(
                SubtitleLabel("发现未在管理列表中的网络驱动器", self),
            )
            tip = CaptionLabel(
                "勾选的设备将导入本软件管理并尝试挂载; "
                "点击「忽略此设备」后, 以后启动时不再提示该设备。",
                self,
            )
            tip.setTextColor(QColor(0x6e, 0x77, 0x81), QColor(0x9a, 0x9a, 0x9a))
            self.viewLayout.addWidget(tip)
            self._list = QVBoxLayout()
            self._list.setSpacing(6)
            self.viewLayout.addLayout(self._list)
            self._empty = BodyLabel("所有设备均已忽略, 以后启动将不再提示。", self)
            self._empty.hide()
            self.viewLayout.addWidget(self._empty)
            for it in items:
                self._add_row(it)
            self.yesButton.setText("导入")
            self.cancelButton.setText("关闭")
            self.widget.setMinimumWidth(560)

        def _add_row(self, it: dict) -> None:
            row = QWidget(self)
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            status = "已连接" if it["connected"] else "未挂载"
            cb = CheckBox(
                f"{it['letter']}:  →  {it['unc']}    ({status})", row,
            )
            cb.setChecked(True)
            btn = PushButton("忽略此设备", row)
            btn.setFixedWidth(100)
            btn.clicked.connect(lambda *_, r=row: self._ignore_row(r))
            h.addWidget(cb, 1)
            h.addWidget(btn)
            self._list.addWidget(row)
            self._rows.append((str(it["letter"]), str(it["unc"]), cb, row))

        def _ignore_row(self, row: QWidget) -> None:
            for i, (letter, unc, cb, w) in enumerate(self._rows):
                if w is not row:
                    continue
                self.cfg.add_ignore(unc)
                try:
                    self.cfg.save()
                except Exception as e:
                    log.warning("保存忽略列表失败: %s", e)
                log.info("已忽略设备 %s (%s:), 以后启动扫描不再提示", unc, letter)
                self._rows.pop(i)
                self._list.removeWidget(row)
                row.hide()
                row.deleteLater()
                break
            if not self._rows:
                self._empty.show()
                self.yesButton.setEnabled(False)

        def selected(self) -> list[tuple[str, str]]:
            return [
                (letter, unc) for letter, unc, cb, _ in self._rows
                if cb.isChecked()
            ]

    # -----------------------------------------------------------------------

    class StatusDetailDialog(MessageBoxBase):
        """单个驱动器的状态详情: 当前状态、配置摘要和最近状态记录。"""

        def __init__(
            self, parent: QWidget, letter: str, drive: dict,
            status: dict, history: list[tuple[str, str, str]],
        ) -> None:
            super().__init__(parent)
            self.viewLayout.addWidget(
                SubtitleLabel(f"{letter}: 状态详情", self),
            )
            state = str(status.get("state", ""))
            label, _color = STATE_LABELS.get(state, STATE_LABELS[""])
            lines = [
                f"当前状态: {label}",
                f"状态说明: {status.get('text') or '-'}",
                f"更新时间: {status.get('time') or '-'}",
                "",
                f"名称: {drive.get('label') or drive.get('path', '-')}",
                f"网络路径: {drive.get('path', '-')}",
                f"挂载策略: {MODE_LABELS.get(str(drive.get('mode', DriveMode.REACHABLE)), '-')}",
                f"自动挂载: {'开' if drive.get('auto_mount', True) else '关'}",
                f"启用此项: {'是' if drive.get('enabled', True) else '否'}",
                f"强制重挂: {'是' if drive.get('force') else '否'}",
                "",
                "最近状态记录 (新的在前):",
            ]
            if history:
                for t, st, tx in reversed(history[-30:]):
                    lb, _c = STATE_LABELS.get(st, STATE_LABELS[""])
                    lines.append(f"[{t}] {lb} - {tx}")
            else:
                lines.append("(暂无记录, 等待下一轮检测)")
            box = PlainTextEdit(self)
            box.setReadOnly(True)
            box.setPlainText("\n".join(lines))
            self.viewLayout.addWidget(box)
            self.yesButton.setText("关闭")
            self.cancelButton.hide()
            self.widget.setMinimumWidth(620)
            self.widget.setMinimumHeight(430)

    # -----------------------------------------------------------------------
    # 页面
    # -----------------------------------------------------------------------

    class DrivePage(QWidget):
        """主页面: 工具栏 + 驱动器表格 + 日志 + 状态栏。"""

        def __init__(self, main: "MainWindow") -> None:  # noqa: F821
            super().__init__(main)
            self.main = main
            self.setObjectName("drivePage")

            v = QVBoxLayout(self)
            v.setContentsMargins(24, 16, 24, 10)
            v.setSpacing(10)
            v.addWidget(SubtitleLabel("网络驱动器", self))

            # 工具栏
            bar = QHBoxLayout()
            self.btn_add = PrimaryPushButton("添加", self)
            self.btn_add.clicked.connect(self.add_drive)
            bar.addWidget(self.btn_add)
            for text, slot in [
                ("编辑", self.edit_drive),
                ("删除", self.remove_drive),
                ("读取已挂载", self.import_mounted),
                ("立即挂载", self.manual_mount),
                ("立即卸载", self.manual_unmount),
                ("刷新状态", self.check_now),
            ]:
                btn = PushButton(text, self)
                btn.clicked.connect(slot)
                bar.addWidget(btn)
            bar.addStretch(1)
            bar.addWidget(BodyLabel("开机自动运行", self))
            self.sw_autostart = SwitchButton(self)
            self.sw_autostart.setOnText("开")
            self.sw_autostart.setOffText("关")
            self.sw_autostart.setChecked(autostart_enabled())
            self.sw_autostart.checkedChanged.connect(self.toggle_autostart)
            bar.addWidget(self.sw_autostart)
            bar.addWidget(BodyLabel("启动自动挂载", self))
            self.sw_mount_start = SwitchButton(self)
            self.sw_mount_start.setOnText("开")
            self.sw_mount_start.setOffText("关")
            self.sw_mount_start.setChecked(
                bool(self.main.cfg.settings.get("auto_mount_on_start", True)),
            )
            self.sw_mount_start.checkedChanged.connect(self.toggle_mount_on_start)
            bar.addWidget(self.sw_mount_start)
            v.addLayout(bar)

            # 驱动器表格
            self.table = TableWidget(self)
            self.table.setColumnCount(7)
            self.table.setHorizontalHeaderLabels(
                ["盘符", "名称", "网络路径", "挂载策略", "状态", "状态说明", "自动挂载"],
            )
            self.table.verticalHeader().hide()
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows,
            )
            self.table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection,
            )
            self.table.setBorderVisible(True)
            self.table.setBorderRadius(8)
            self.table.doubleClicked.connect(lambda *_: self.edit_drive())
            self.table.cellClicked.connect(self._cell_clicked)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
            # 名称列给足默认宽度, 网络路径列按内容自适应
            header.resizeSection(1, 130)
            v.addWidget(self.table, 3)

            # 日志
            v.addWidget(BodyLabel("运行日志", self))
            self.logbox = PlainTextEdit(self)
            self.logbox.setReadOnly(True)
            self.logbox.setMaximumBlockCount(1200)
            v.addWidget(self.logbox, 2)

            # 状态栏
            sb = QHBoxLayout()
            self.lbl_monitor = CaptionLabel("监控: 运行中", self)
            self.lbl_net = CaptionLabel("本机 IP: -    网关: -    上次检测: -", self)
            sb.addWidget(self.lbl_monitor)
            sb.addStretch(1)
            sb.addWidget(self.lbl_net)
            v.addLayout(sb)

            self.refresh_table()

        # -- 显示刷新 ---------------------------------------------------------

        def refresh_table(self) -> None:
            drives = self.main.cfg.snapshot()
            self.table.setRowCount(len(drives))
            for row, d in enumerate(drives):
                letter = str(d["letter"]).upper()
                st = self.main.statuses.get(letter, {})
                state = str(st.get("state", ""))
                label, color = STATE_LABELS.get(state, STATE_LABELS[""])
                info = str(st.get("text", ""))
                if st.get("time"):
                    info = f"[{st['time']}] {info}"
                display_label = str(d.get("label") or d["path"])
                values = [
                    letter + ":", display_label, str(d["path"]),
                    MODE_LABELS.get(str(d.get("mode", DriveMode.REACHABLE)), ""),
                    label, info,
                ]
                for col, val in enumerate(values):
                    item = QTableWidgetItem(val)
                    if col == 4:    # 状态列着色
                        item.setForeground(QBrush(QColor(color)))
                    self.table.setItem(row, col, item)
                # 每盘"自动挂载"开关
                sw = SwitchButton(self.table)
                sw.setOnText("开")
                sw.setOffText("关")
                sw.setChecked(bool(d.get("auto_mount", True)))
                sw.checkedChanged.connect(
                    lambda checked, l=letter: self.toggle_auto_mount(l, checked),
                )
                wrap = QWidget(self.table)
                hl = QHBoxLayout(wrap)
                hl.setContentsMargins(6, 0, 6, 0)
                hl.addWidget(sw)
                self.table.setCellWidget(row, 6, wrap)

        def _cell_clicked(self, row: int, col: int) -> None:
            """点击"状态说明"列 -> 弹出该盘的状态详情与最近记录。"""
            if col != 5:
                return
            item = self.table.item(row, 0)
            if not item:
                return
            letter = item.text().rstrip(":").upper()
            StatusDetailDialog(
                self.main, letter,
                self.main.cfg.get(letter) or {},
                self.main.statuses.get(letter, {}),
                self.main.history.get(letter, []),
            ).exec()

        def toggle_auto_mount(self, letter: str, checked: bool) -> None:
            """表格"自动挂载"列开关。"""
            d = self.main.cfg.get(letter)
            if not d:
                return
            d = dict(d)
            d["auto_mount"] = bool(checked)
            self.main.cfg.upsert(d)
            try:
                self.main.cfg.save()
            except Exception as e:
                InfoBar.error(
                    "错误", f"保存设置失败: {e}", parent=self.main,
                    position=InfoBarPosition.TOP, duration=4000,
                )
                return
            log.info("%s: 自动挂载%s", letter, "已开启" if checked else "已关闭")
            self.main.monitor.trigger(f"{letter}: 切换自动挂载")

        def append_log(self, line: str) -> None:
            self.logbox.appendPlainText(line)
            sb = self.logbox.verticalScrollBar()
            sb.setValue(sb.maximum())

        def set_netinfo(self, payload: dict) -> None:
            self.lbl_net.setText(
                f"本机 IP: {payload['ips']}    网关: {payload['gateway']}    "
                f"上次检测: {payload.get('last') or '-'}",
            )

        def _selected_letter(self) -> str | None:
            row = self.table.currentRow()
            if row < 0 or not self.table.item(row, 0):
                InfoBar.warning(
                    "提示", "请先在列表中选择一个驱动器", parent=self.main,
                    position=InfoBarPosition.TOP, duration=2000,
                )
                return None
            return self.table.item(row, 0).text().rstrip(":")

        # -- 操作 -------------------------------------------------------------

        def _free_letters(self) -> list[str]:
            used = used_drive_letters() | {
                str(d["letter"]).upper() for d in self.main.cfg.snapshot()
            }
            return [chr(c) for c in range(ord("D"), ord("Z") + 1) if chr(c) not in used]

        def _used_letters_display(self) -> list[str]:
            used = used_drive_letters() | {
                str(d["letter"]).upper() for d in self.main.cfg.snapshot()
            }
            return sorted(used)

        def add_drive(self) -> None:
            dlg = DriveDialog(
                self.main, "添加网络驱动器", letters=self._free_letters(),
                used_letters=self._used_letters_display(),
            )
            if not dlg.exec():
                return
            result, password = dlg.result_drive()
            self._apply_dialog_result(result, password)

        def edit_drive(self) -> None:
            letter = self._selected_letter()
            if not letter:
                return
            drive = self.main.cfg.get(letter)
            letters = sorted(set(self._free_letters()) | {letter})
            dlg = DriveDialog(
                self.main, f"编辑 {letter}:", drive=drive, letters=letters,
                used_letters=self._used_letters_display(),
            )
            if not dlg.exec():
                return
            result, password = dlg.result_drive()
            self._apply_dialog_result(result, password, old_letter=letter)

        def _apply_dialog_result(
            self, result: dict[str, object], password: str,
            old_letter: str | None = None,
        ) -> None:
            cfg = self.main.cfg
            old = cfg.get(old_letter) if old_letter else None
            if old is not None:
                result["auto_mount"] = old.get("auto_mount", True)
            old_path = str((old or {}).get("path", ""))
            new_letter = str(result["letter"])
            new_path = str(result["path"])
            if old_letter and old_letter != new_letter:
                # 盘符变更: 先卸载旧盘符上的旧映射 -> 重新映射
                current = get_mapped_remote(old_letter)
                if current and (
                    same_unc(current, old_path) or same_unc(current, new_path)
                ):
                    log.info(
                        "盘符变更 %s: -> %s:, 卸载旧映射 %s",
                        old_letter, new_letter, current,
                    )
                    unmount_drive(old_letter)
                cfg.remove(old_letter)
                self.main.statuses.pop(old_letter, None)
                self.main.history.pop(old_letter, None)
            elif old_letter and not same_unc(old_path, new_path):
                # 盘符不变但路径变更: 卸掉旧映射
                current = get_mapped_remote(old_letter)
                if current and same_unc(current, old_path):
                    log.info(
                        "%s: 路径变更, 卸载旧映射 %s, 将重新挂载到 %s",
                        old_letter, current, new_path,
                    )
                    unmount_drive(old_letter)
            cfg.upsert(result)
            cfg.save()
            if password:
                if result.get("save_cred"):
                    try:
                        host = server_from_unc(new_path)
                        run_hidden(
                            ["cmdkey", f"/add:{host}",
                             f"/user:{result['username']}",
                             f"/pass:{password}"], timeout=10,
                        )
                        log.info("凭据已保存到 Windows 凭据管理器 (%s)", host)
                    except Exception as e:
                        log.warning("保存凭据失败: %s", e)
                self.main.monitor.set_session_pw(new_letter, password)
            self.refresh_table()
            self.main.monitor.trigger("配置变更")

        def remove_drive(self) -> None:
            letter = self._selected_letter()
            if not letter:
                return
            drive = self.main.cfg.get(letter)
            w = MessageBox(
                "确认删除",
                f"删除 {letter}: ({drive['path']}) 的配置?", self.main,
            )
            if not w.exec():
                return
            self.main.cfg.remove(letter)
            self.main.cfg.save()
            self.main.statuses.pop(letter, None)
            self.refresh_table()
            log.info(
                "%s: 的配置已删除。如不再需要其凭据, 请在 Windows 凭据管理器中手动删除。",
                letter,
            )

        def import_mounted(self) -> None:
            """读取系统当前已挂载的网络驱动器, 勾选后导入管理列表。"""
            mappings = list_system_mappings()
            managed = {str(d["letter"]).upper() for d in self.main.cfg.snapshot()}
            dlg = ImportDialog(self.main, mappings, managed)
            if not dlg.exec():
                return
            selected = dlg.selected()
            if not selected:
                return
            self.import_mappings(selected, "导入已挂载驱动器")

        def import_mappings(
            self, selected: list[tuple[str, str]], reason: str = "导入驱动器",
        ) -> None:
            """把 [(letter, unc), ...] 以默认配置导入管理列表并触发检测。"""
            for letter, unc in selected:
                self.main.cfg.upsert({
                    "letter": letter, "path": unc, "username": "",
                    "save_cred": True, "mode": DriveMode.REACHABLE,
                    "enabled": True, "force": False,
                })
            self.main.cfg.save()
            self.refresh_table()
            self.main.monitor.trigger(reason)
            log.info(
                "已导入 %d 个网络驱动器: %s",
                len(selected), ", ".join(l for l, _ in selected),
            )
            InfoBar.success(
                "导入完成", f"已导入 {len(selected)} 个网络驱动器",
                parent=self.main, position=InfoBarPosition.TOP, duration=3000,
            )

        def manual_mount(self) -> None:
            letter = self._selected_letter()
            if letter:
                self.main.monitor.manual("mount", letter)

        def manual_unmount(self) -> None:
            letter = self._selected_letter()
            if letter:
                self.main.monitor.manual("unmount", letter)

        def check_now(self) -> None:
            self.main.monitor.trigger("手动刷新")

        def toggle_autostart(self, checked: bool) -> None:
            try:
                set_autostart(checked)
                log.info("开机自动运行: %s", "已开启" if checked else "已关闭")
            except Exception as e:
                InfoBar.error(
                    "错误", f"设置开机启动失败: {e}", parent=self.main,
                    position=InfoBarPosition.TOP, duration=4000,
                )
                self.sw_autostart.setChecked(autostart_enabled())

        def toggle_mount_on_start(self, checked: bool) -> None:
            """"启动自动挂载"开关: 关闭后, 软件启动的第一轮检测只检测不自动挂载。"""
            self.main.cfg.settings["auto_mount_on_start"] = bool(checked)
            try:
                self.main.cfg.save()
            except Exception as e:
                InfoBar.error(
                    "错误", f"保存设置失败: {e}", parent=self.main,
                    position=InfoBarPosition.TOP, duration=4000,
                )
                return
            log.info("软件启动时自动尝试挂载: %s", "已开启" if checked else "已关闭")

    # -----------------------------------------------------------------------

    class AboutPage(QWidget):
        def __init__(self, main: QWidget) -> None:
            super().__init__(main)
            self.setObjectName("aboutPage")
            v = QVBoxLayout(self)
            v.setContentsMargins(24, 16, 24, 12)
            v.setSpacing(8)
            v.addWidget(SubtitleLabel(APP_TITLE, self))
            v.addWidget(BodyLabel(f"版本: {__version__}", self))
            v.addWidget(BodyLabel(f"配置文件: {CONFIG_FILE}", self))
            v.addWidget(BodyLabel(f"日志文件: {LOG_FILE}", self))
            v.addWidget(BodyLabel(
                "GUI: PyQt-Fluent-Widgets (github.com/zhiyiYo/PyQt-Fluent-Widgets)",
                self,
            ))
            v.addWidget(CaptionLabel(
                "关闭窗口将最小化到系统托盘继续后台监控; 右键托盘图标可退出程序。", self,
            ))
            v.addStretch(1)

    # -----------------------------------------------------------------------
    # 主窗口
    # -----------------------------------------------------------------------

    class MainWindow(FluentWindow):
        def __init__(self, minimized: bool = False) -> None:
            super().__init__()
            # 侧边栏
            self.navigationInterface.setExpandWidth(220)
            self.navigationInterface.setMinimumExpandWidth(99999)
            self.setWindowTitle(APP_TITLE)
            self.resize(980, 660)
            self._really_quit = False
            self._tray_hint_shown = False

            self.cfg = Config.load()
            self.ui_q: queue.Queue = queue.Queue()
            self.statuses: dict[str, dict] = {}
            self.history: dict[str, list[tuple[str, str, str]]] = {}

            self.page = DrivePage(self)
            self.about = AboutPage(self)
            self.addSubInterface(
                self.page, FluentIcon.FOLDER, "驱动器管理",
            )
            self.addSubInterface(
                self.about, FluentIcon.INFO, "关于",
                position=NavigationItemPosition.BOTTOM,
            )
            self.navigationInterface.panel.setReturnButtonVisible(False)

            h = QueueLogHandler(self.ui_q)
            h.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S",
            ))
            log.addHandler(h)

            self.monitor = Monitor(self.cfg, self.ui_q)
            if not self.cfg.settings.get("auto_mount_on_start", True):
                self.monitor.skip_mount_once = True
            self.monitor.start()
            self.monitor.trigger("程序启动")

            self._setup_tray()

            self.timer = QTimer(self)
            self.timer.timeout.connect(self._poll_ui)
            self.timer.start(150)

            # 启动扫描
            QTimer.singleShot(10000 if minimized else 1500, self._startup_scan)

            log.info("%s v%s 已启动", APP_TITLE, __version__)

        # -- 后台队列轮询 -----------------------------------------------------

        def _poll_ui(self) -> None:
            try:
                while True:
                    kind, payload = self.ui_q.get_nowait()
                    if kind == "log":
                        self.page.append_log(payload)
                    elif kind == "status":
                        letter = payload["letter"]
                        self.statuses[letter] = payload
                        hist = self.history.setdefault(letter, [])
                        hist.append((
                            str(payload.get("time", "")),
                            str(payload.get("state", "")),
                            str(payload.get("text", "")),
                        ))
                        if len(hist) > 50:
                            del hist[:-50]
                        if payload.get("manual"):
                            self._notify_status(letter, payload)
                        self.page.refresh_table()
                    elif kind == "netinfo":
                        self.page.set_netinfo(payload)
                    elif kind == "startup_scan":
                        self._show_startup_scan(payload)
            except queue.Empty:
                pass

        # -- 启动扫描 ---------------------------------------------------------

        def _startup_scan(self) -> None:
            """后台扫描系统中未被本软件管理、且未被忽略的网络驱动器。"""
            if self._really_quit:
                return
            managed = {str(d["letter"]).upper() for d in self.cfg.snapshot()}
            with self.cfg.lock:
                ignored = list(self.cfg.ignored)

            def work() -> None:
                try:
                    from .core import find_unmanaged_drives
                    items = find_unmanaged_drives(managed, ignored)
                except Exception as e:
                    log.warning("启动扫描未管理驱动器失败: %s", e)
                    return
                self.ui_q.put(("startup_scan", items))

            threading.Thread(target=work, daemon=True, name="StartupScan").start()

        def _show_startup_scan(self, items: list) -> None:
            if self._really_quit or not items:
                return
            log.info(
                "启动扫描: 发现 %d 个未管理的网络驱动器, 弹出导入对话框", len(items),
            )
            dlg = StartupScanDialog(self, items, self.cfg)
            if not dlg.exec():
                return
            selected = dlg.selected()
            if selected:
                self.page.import_mappings(selected, "导入启动扫描发现的驱动器")

        def _notify_status(self, letter: str, st: dict) -> None:
            state, text = str(st["state"]), str(st["text"])
            if state == DriveState.MOUNTED:
                InfoBar.success(
                    f"{letter}: 挂载成功", text, parent=self,
                    position=InfoBarPosition.TOP, duration=2500,
                )
            elif state == DriveState.UNMOUNTED:
                InfoBar.info(
                    f"{letter}:", text, parent=self,
                    position=InfoBarPosition.TOP, duration=2500,
                )
            elif state == DriveState.ERROR:
                InfoBar.error(
                    f"{letter}: 操作失败", text, parent=self,
                    position=InfoBarPosition.TOP, duration=4000,
                )

        # -- 托盘 -------------------------------------------------------------

        def _setup_tray(self) -> None:
            if not QSystemTrayIcon.isSystemTrayAvailable():
                self.tray = None
                return
            self.tray = QSystemTrayIcon(self)
            self.tray.setIcon(make_app_icon())
            self.tray.setToolTip(APP_TITLE)
            menu = RoundMenu(parent=self)
            menu.addAction(Action(
                FluentIcon.HOME, "显示主窗口", triggered=self._show_window,
            ))
            menu.addAction(Action(
                FluentIcon.SYNC, "刷新状态",
                triggered=lambda: self.monitor.trigger("托盘手动刷新"),
            ))
            menu.addSeparator()
            menu.addAction(Action(
                FluentIcon.CLOSE, "退出", triggered=self.quit_app,
            ))
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()

        def _on_tray_activated(self, reason: int) -> None:
            if reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            ):
                self._show_window()

        def _show_window(self) -> None:
            self.showNormal()
            self.raise_()
            self.activateWindow()

        # -- 关闭与退出 -------------------------------------------------------

        def closeEvent(self, event) -> None:  # type: ignore[override]
            if self._really_quit:
                event.accept()
                return
            if self.tray and self.tray.isVisible():
                event.ignore()
                self.hide()
                if not self._tray_hint_shown:
                    self._tray_hint_shown = True
                    self.tray.showMessage(
                        APP_TITLE,
                        "程序已最小化到系统托盘, 继续后台监控。",
                        self.tray.icon(), 3000,
                    )
            else:
                w = MessageBox("退出", "退出程序并停止后台监控?", self)
                if w.exec():
                    self.quit_app()
                event.ignore()

        def quit_app(self) -> None:
            if self._really_quit:
                return
            self._really_quit = True
            log.info("程序退出")
            try:
                self.cfg.save()
            except Exception:
                pass
            self.monitor.request_stop()
            if self.tray:
                self.tray.hide()
            QTimer.singleShot(300, QApplication.instance().quit)
