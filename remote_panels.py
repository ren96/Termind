"""开发面板：端口转发 / 远程开发启动 / Git / 系统监控。"""
import os
import re
import shlex
import time

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QHeaderView, QMessageBox, QInputDialog,
    QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTabWidget,
    QPlainTextEdit, QDialog, QGroupBox, QProgressBar, QGridLayout,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QTextCursor


PORT_SERVICE_MAP = {
    22: "SSH", 21: "FTP", 80: "HTTP", 443: "HTTPS",
    3000: "Node/React", 3001: "Node",
    3306: "MySQL", 5000: "Flask", 5173: "Vite",
    5432: "PostgreSQL", 6379: "Redis",
    8000: "Django/FastAPI", 8080: "HTTP-Alt",
    8888: "Jupyter", 9000: "PHP-FPM", 9090: "Prometheus",
    27017: "MongoDB", 11211: "Memcached",
}

_MAX_EXEC_TIMEOUT = 20


class _AsyncExecMixin:
    def _init_exec_state(self):
        self._exec_worker = None
        self._pending = {}
        self._exec_seq = 0

    def _bind_exec_signal(self, worker):
        old = getattr(self, "_exec_worker", None)
        if old is worker:
            return
        if old is not None:
            try:
                old.remote_exec_done.disconnect(self._on_exec_done)
            except (TypeError, RuntimeError):
                pass
        self._pending = {}
        self._exec_worker = worker
        if worker is not None:
            worker.remote_exec_done.connect(self._on_exec_done)

    def _on_exec_done(self, cmd_id, out):
        cb = self._pending.pop(cmd_id, None)
        if cb is None:
            return
        try:
            cb(out)
        except Exception:
            pass

    def _exec(self, cmd, callback, timeout=_MAX_EXEC_TIMEOUT, prefix="p"):
        worker = getattr(self, "_exec_worker", None)
        if worker is None or not getattr(worker, "client", None):
            try:
                callback("")
            except Exception:
                pass
            return
        self._exec_seq += 1
        cid = f"{prefix}-{id(self)}-{self._exec_seq}"
        self._pending[cid] = callback
        QTimer.singleShot(
            (timeout + 10) * 1000,
            lambda c=cid: self._pending.pop(c, None))
        worker.exec_command_async(cmd, cid, timeout=timeout)


# ============================================================
# 端口转发
# ============================================================
def parse_listening_ports(raw):
    ports = set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        port = line.rsplit(":", 1)[-1]
        if port.isdigit():
            p = int(port)
            if 1 <= p <= 65535:
                ports.add(p)
    return sorted(ports)


class PortForwardPanel(_AsyncExecMixin, QWidget):
    def __init__(self, tab):
        QWidget.__init__(self)
        self.tab = tab
        self.tunnels = None
        self._init_exec_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        self.btn_scan = QPushButton("🔍 扫描远程监听端口")
        self.btn_scan.clicked.connect(self.scan)
        btn_manual = QPushButton("➕ 手动转发")
        btn_manual.clicked.connect(self.manual_add)
        top.addWidget(self.btn_scan)
        top.addWidget(btn_manual)
        top.addStretch()
        layout.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["远程端口", "服务", "本地端口", "状态", "操作"])
        hh = self.table.horizontalHeader()
        for col, mode in enumerate((
                QHeaderView.ResizeMode.ResizeToContents,
                QHeaderView.ResizeMode.Stretch,
                QHeaderView.ResizeMode.ResizeToContents,
                QHeaderView.ResizeMode.ResizeToContents,
                QHeaderView.ResizeMode.ResizeToContents)):
            hh.setSectionResizeMode(col, mode)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        self.hint = QLabel("扫描后点击「转发」即可将远程端口映射到本地。")
        self.hint.setObjectName("hintLabel")
        self.hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.hint)

    def bind_worker(self, worker):
        self._bind_exec_signal(worker)

    def set_tunnel_manager(self, manager):
        if self.tunnels is manager:
            return
        if self.tunnels is not None:
            try:
                self.tunnels.tunnel_opened.disconnect(self._on_tunnel_opened)
                self.tunnels.tunnel_closed.disconnect(self._on_tunnel_closed)
            except (TypeError, RuntimeError):
                pass
        self.tunnels = manager
        manager.tunnel_opened.connect(self._on_tunnel_opened)
        manager.tunnel_closed.connect(self._on_tunnel_closed)

    def scan(self):
        if not self.tab.worker or not self.tab.worker.client:
            return
        self.table.setRowCount(0)
        self.hint.setText("正在扫描远程监听端口…")
        self.btn_scan.setEnabled(False)
        cmd = (
            "ss -tln 2>/dev/null | awk 'NR>1{print $4}' "
            "|| netstat -tln 2>/dev/null | awk 'NR>2{print $4}'"
        )
        self._exec(cmd, self._on_scan_done, timeout=10, prefix="pf-scan")

    def _on_scan_done(self, raw):
        self.btn_scan.setEnabled(True)
        ports = parse_listening_ports(raw)
        if not ports:
            self.hint.setText("未检测到监听端口（或远程缺少 ss / netstat）")
            return
        self.hint.setText(f"检测到 {len(ports)} 个监听端口。")
        for p in ports:
            self._add_row(p, PORT_SERVICE_MAP.get(p, ""))

    def manual_add(self):
        text, ok = QInputDialog.getText(
            self, "手动转发", "远程端口（例如 8000）:")
        if not ok or not text.strip().isdigit():
            return
        p = int(text.strip())
        self._add_row(p, PORT_SERVICE_MAP.get(p, ""))

    def _add_row(self, remote_port, service):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == str(remote_port):
                return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(remote_port)))
        self.table.setItem(row, 1, QTableWidgetItem(service or "—"))
        self.table.setItem(row, 2, QTableWidgetItem("—"))
        self.table.setItem(row, 3, QTableWidgetItem("未转发"))
        btn = QPushButton("转发")
        btn.setObjectName("ghostBtn")
        btn.clicked.connect(
            lambda _=False, r=row, p=remote_port, s=service:
            self._forward(r, p, s))
        self.table.setCellWidget(row, 4, btn)

    def _forward(self, row, remote_port, service):
        if not self.tunnels:
            QMessageBox.warning(self, "提示", "隧道管理器未就绪")
            return
        if not self.tab._connected:
            QMessageBox.warning(self, "提示", "SSH 连接尚未就绪")
            return
        # ★ 修复：open() 可能因 transport 失效抛异常，此时不应把按钮改成“取消”
        try:
            tid = self.tunnels.open(
                "127.0.0.1", remote_port,
                label=f"{service}:{remote_port}", service=service)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"打开隧道失败: {e}")
            return
        self.table.setItem(row, 3, QTableWidgetItem("连接中…"))
        btn = self.table.cellWidget(row, 4)
        if btn:
            btn.setText("取消")
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
            btn.clicked.connect(
                lambda _=False, t=tid, r=row: self._cancel_tunnel(t, r))
        self.table.setItem(row, 2, QTableWidgetItem("…"))
        self.table.item(row, 3).setData(Qt.ItemDataRole.UserRole, tid)

    def _cancel_tunnel(self, tid, row):
        if self.tunnels:
            self.tunnels.close(tid)
        self._reset_row(row)

    def _on_tunnel_opened(self, rec):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item and item.data(Qt.ItemDataRole.UserRole) == rec.tunnel_id:
                new_item = QTableWidgetItem("✅ 已转发")
                new_item.setData(Qt.ItemDataRole.UserRole, rec.tunnel_id)
                self.table.setItem(row, 3, new_item)
                self.table.setItem(
                    row, 2, QTableWidgetItem(str(rec.local_port)))
                self.hint.setText(
                    f"本地访问: http://127.0.0.1:{rec.local_port}")
                break

    def _on_tunnel_closed(self, tid):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3)
            if item and item.data(Qt.ItemDataRole.UserRole) == tid:
                self._reset_row(row)
                break

    def _reset_row(self, row):
        self.table.setItem(row, 2, QTableWidgetItem("—"))
        self.table.setItem(row, 3, QTableWidgetItem("未转发"))
        btn = self.table.cellWidget(row, 4)
        if btn:
            btn.setText("转发")
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
            remote_port = int(self.table.item(row, 0).text())
            service = self.table.item(row, 1).text()
            btn.clicked.connect(
                lambda _=False, r=row, p=remote_port, s=service:
                self._forward(r, p, s))


# ============================================================
# 远程开发启动器
# ============================================================
class RemoteDevLauncher:
    SERVICES = {
        "code": {
            "label": "VS Code Server",
            "check": (
                "command -v code >/dev/null 2>&1 "
                "|| command -v code-server >/dev/null 2>&1"
            ),
            "start": (
                "if command -v code >/dev/null 2>&1; then "
                "(nohup code serve-web --host 0.0.0.0 --port {port} "
                "--without-connection-token "
                "> /tmp/termind-code.log 2>&1 &); "
                "else "
                "(nohup code-server --bind-addr 0.0.0.0:{port} "
                "--auth none "
                "> /tmp/termind-code.log 2>&1 &); "
                "fi"
            ),
            "default_port": 8000,
            "url_path": "/",
        },
        "jupyter": {
            "label": "Jupyter Lab",
            "check": (
                "command -v jupyter-lab >/dev/null 2>&1 "
                "|| command -v jupyter >/dev/null 2>&1"
            ),
            "start": (
                "nohup jupyter lab --no-browser "
                "--ip=127.0.0.1 --port {port} "
                "--ServerApp.token='' --ServerApp.password='' "
                "> /tmp/termind-jupyter.log 2>&1 &"
            ),
            "default_port": 8888,
            "url_path": "/lab",
        },
    }

    def __init__(self, tab, tunnels):
        self.tab = tab
        self.tunnels = tunnels


# ============================================================
# Git 面板
# ============================================================
class GitPanel(_AsyncExecMixin, QWidget):
    def __init__(self, tab):
        QWidget.__init__(self)
        self.tab = tab
        self.worker = None
        self._cwd = "."
        self._init_exec_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.path_input = QLineEdit(".")
        self.path_input.setPlaceholderText("仓库路径（相对家目录或绝对路径）")
        self.path_input.returnPressed.connect(self.refresh)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        top.addWidget(QLabel("仓库:"))
        top.addWidget(self.path_input, 1)
        top.addWidget(btn_refresh)
        layout.addLayout(top)

        self.branch_label = QLabel("")
        self.branch_label.setObjectName("hintLabel")
        layout.addWidget(self.branch_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        t1 = QLabel("工作区变更")
        t1.setObjectName("sectionLabel")
        left_layout.addWidget(t1)
        self.changes_list = QListWidget()
        self.changes_list.itemDoubleClicked.connect(self._show_diff)
        left_layout.addWidget(self.changes_list, 1)

        t2 = QLabel("最近提交")
        t2.setObjectName("sectionLabel")
        left_layout.addWidget(t2)
        self.log_list = QListWidget()
        self.log_list.itemDoubleClicked.connect(self._show_commit)
        left_layout.addWidget(self.log_list, 2)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(QFont("Cascadia Code", 10))
        self.diff_view.setStyleSheet(
            "QPlainTextEdit { background:#1e1e2e; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:6px; padding:6px; }")
        right_layout.addWidget(self.diff_view)

        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setSizes([320, 500])
        layout.addWidget(self.splitter, 1)

        btn_row = QHBoxLayout()
        self.btn_pull = QPushButton("⬇ git pull")
        self.btn_pull.clicked.connect(lambda: self._run_action("git pull"))
        self.btn_stash = QPushButton("📦 git stash")
        self.btn_stash.clicked.connect(lambda: self._run_action("git stash"))
        btn_row.addWidget(self.btn_pull)
        btn_row.addWidget(self.btn_stash)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def bind_worker(self, worker):
        self.worker = worker
        self._bind_exec_signal(worker)
        if worker:
            self.refresh()

    def _wrap(self, cmd):
        return f"cd {shlex.quote(self._cwd)} 2>/dev/null && {cmd} 2>&1"

    def _exec_git(self, cmd, cb, timeout=15):
        self._exec(self._wrap(cmd), cb, timeout=timeout, prefix="git")

    def refresh(self):
        if not self.worker:
            return
        self._cwd = self.path_input.text().strip() or "."
        self.changes_list.clear()
        self.log_list.clear()
        self.branch_label.setText("加载中…")
        self._exec_git("git status --porcelain=v2 --branch", self._apply_status)
        self._exec_git(
            "git log --oneline --graph --decorate -n 30", self._apply_log)

    def _apply_status(self, out):
        branch = ""
        self.changes_list.clear()
        for line in out.splitlines():
            if line.startswith("# branch.head "):
                branch = line.split(" ", 2)[2]
            elif line and not line.startswith("#"):
                self.changes_list.addItem(line)
        self.branch_label.setText(
            f"当前分支: {branch or '未知'}  ·  变更 {self.changes_list.count()} 项")

    def _apply_log(self, out):
        self.log_list.clear()
        for line in out.splitlines():
            item = QListWidgetItem(line)
            item.setData(Qt.ItemDataRole.UserRole, line)
            self.log_list.addItem(item)

    @staticmethod
    def _extract_path(line):
        if line.startswith("?") or line.startswith("!"):
            return line[2:].strip()
        if line[:1] in ("1", "2", "u"):
            parts = line.split(maxsplit=8)
            path = parts[-1] if parts else ""
            if "\0" in path:
                path = path.split("\0", 1)[0]
            # ★ 修复：porcelain=v2 的 rename (2 R.) 记录用 TAB 分隔源和目标，
            #   取源路径才能正确 diff。
            if "\t" in path:
                path = path.split("\t", 1)[0]
            return path
        parts = line.split()
        return parts[-1] if parts else ""

    def _show_diff(self, item):
        path = self._extract_path(item.text())
        if not path:
            return
        self.diff_view.setPlainText("正在加载 diff…")
        self._diff_step(path, 0)

    def _diff_step(self, path, stage):
        cmds = (
            f"git diff -- {shlex.quote(path)}",
            f"git diff --cached -- {shlex.quote(path)}",
            f"git diff --no-index /dev/null {shlex.quote(path)}",
        )
        if stage >= len(cmds):
            self.diff_view.setPlainText(f"(无 diff: {path})")
            return
        self._exec_git(
            cmds[stage],
            lambda out, s=stage, p=path: self._diff_after(p, s, out))

    def _diff_after(self, path, stage, out):
        if out.strip():
            self.diff_view.setPlainText(out)
        else:
            self._diff_step(path, stage + 1)

    def _show_commit(self, item):
        line = item.text().lstrip("*| \\ ")
        sha = line.split()[0] if line.split() else ""
        if not sha:
            return
        self.diff_view.setPlainText("加载中…")
        self._exec_git(
            f"git show --stat --oneline {shlex.quote(sha)}",
            lambda out: self.diff_view.setPlainText(out or "(无输出)"))

    def _run_action(self, cmd):
        if not self.worker:
            return
        self.diff_view.setPlainText("执行中…")
        self._exec_git(cmd, self._after_action, timeout=30)

    def _after_action(self, out):
        self.diff_view.setPlainText(out or "(无输出)")
        self.refresh()


# ============================================================
# 系统面板（含监控 / 实时日志）
# ============================================================
class SystemPanel(_AsyncExecMixin, QWidget):
    def __init__(self, tab):
        QWidget.__init__(self)
        self.tab = tab
        self.worker = None
        self._init_exec_state()

        self._log_stream_id = ""
        self._log_buf = []
        self._monitor_seq = 0
        self._monitor_pending = False
        self._monitor_pending_ts = 0.0      # ★ 新增：请求挂起时间戳
        self._prev_cpu = None
        self._prev_net = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.inner = QTabWidget()

        # ---- 端口占用 ----
        port_widget = QWidget()
        port_layout = QVBoxLayout(port_widget)
        port_layout.setContentsMargins(6, 6, 6, 6)
        port_layout.setSpacing(6)
        self.btn_port = QPushButton("🔍 查看监听端口")
        self.btn_port.clicked.connect(self._load_ports)
        port_layout.addWidget(self.btn_port)
        self.port_table = QTableWidget(0, 3)
        self.port_table.setHorizontalHeaderLabels(["端口", "进程", "操作"])
        self.port_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.port_table.verticalHeader().setVisible(False)
        port_layout.addWidget(self.port_table)

        # ---- systemd ----
        sys_widget = QWidget()
        sys_layout = QVBoxLayout(sys_widget)
        sys_layout.setContentsMargins(6, 6, 6, 6)
        sys_layout.setSpacing(6)
        row = QHBoxLayout()
        btn_sys = QPushButton("🔄 运行中的服务")
        btn_sys.clicked.connect(lambda: self._load_services("--state=running"))
        btn_failed = QPushButton("⚠ 失败的服务")
        btn_failed.clicked.connect(lambda: self._load_services("--state=failed"))
        row.addWidget(btn_sys)
        row.addWidget(btn_failed)
        row.addStretch()
        sys_layout.addLayout(row)
        self.sys_table = QTableWidget(0, 3)
        self.sys_table.setHorizontalHeaderLabels(["服务", "状态", "操作"])
        self.sys_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.sys_table.verticalHeader().setVisible(False)
        sys_layout.addWidget(self.sys_table)

        # ---- Docker ----
        docker_widget = QWidget()
        docker_layout = QVBoxLayout(docker_widget)
        docker_layout.setContentsMargins(6, 6, 6, 6)
        docker_layout.setSpacing(6)
        self.btn_docker = QPushButton("🐳 列出容器")
        self.btn_docker.clicked.connect(self._load_docker)
        docker_layout.addWidget(self.btn_docker)
        self.docker_table = QTableWidget(0, 4)
        self.docker_table.setHorizontalHeaderLabels(
            ["容器", "状态", "端口", "操作"])
        self.docker_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.docker_table.verticalHeader().setVisible(False)
        docker_layout.addWidget(self.docker_table)

        # ---- 监控 ----
        monitor_widget = QWidget()
        monitor_layout = QVBoxLayout(monitor_widget)
        monitor_layout.setContentsMargins(6, 6, 6, 6)
        monitor_layout.setSpacing(8)

        mon_ctl = QHBoxLayout()
        self.btn_monitor = QPushButton("▶ 开始监控")
        self.btn_monitor.setCheckable(True)
        self.btn_monitor.toggled.connect(self._toggle_monitor)
        self.monitor_status = QLabel("未启动")
        self.monitor_status.setObjectName("hintLabel")
        mon_ctl.addWidget(self.btn_monitor)
        mon_ctl.addWidget(self.monitor_status)
        mon_ctl.addStretch()
        monitor_layout.addLayout(mon_ctl)

        mon_grid = QGridLayout()
        mon_grid.setSpacing(6)
        self.cpu_bar = QProgressBar(); self.cpu_bar.setRange(0, 100)
        self.mem_bar = QProgressBar(); self.mem_bar.setRange(0, 100)
        self.swap_bar = QProgressBar(); self.swap_bar.setRange(0, 100)
        self.cpu_label = QLabel("—")
        self.mem_label = QLabel("—")
        self.swap_label = QLabel("—")
        self.load_label = QLabel("负载: —")
        self.net_label = QLabel("网络: —")
        mon_grid.addWidget(QLabel("CPU"),  0, 0)
        mon_grid.addWidget(self.cpu_bar,   0, 1)
        mon_grid.addWidget(self.cpu_label, 0, 2)
        mon_grid.addWidget(QLabel("内存"), 1, 0)
        mon_grid.addWidget(self.mem_bar,   1, 1)
        mon_grid.addWidget(self.mem_label, 1, 2)
        mon_grid.addWidget(QLabel("Swap"), 2, 0)
        mon_grid.addWidget(self.swap_bar,  2, 1)
        mon_grid.addWidget(self.swap_label, 2, 2)
        mon_grid.addWidget(self.load_label, 3, 0, 1, 3)
        mon_grid.addWidget(self.net_label,  4, 0, 1, 3)
        monitor_layout.addLayout(mon_grid)
        monitor_layout.addStretch()

        # ---- 实时日志 ----
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        log_layout.setContentsMargins(6, 6, 6, 6)
        log_layout.setSpacing(6)

        log_ctl = QHBoxLayout()
        log_ctl.setSpacing(6)
        self.log_cmd_input = QLineEdit()
        self.log_cmd_input.setPlaceholderText(
            "例如: tail -f /var/log/syslog  或  docker logs -f myapp")
        self.log_cmd_input.setText("tail -f /var/log/syslog")
        self.log_cmd_input.returnPressed.connect(self._start_log_stream)
        self.btn_log_start = QPushButton("▶ 开始")
        self.btn_log_start.clicked.connect(self._start_log_stream)
        self.btn_log_stop = QPushButton("■ 停止")
        self.btn_log_stop.setEnabled(False)
        self.btn_log_stop.clicked.connect(self._stop_log_stream)
        btn_log_clear = QPushButton("清空")
        btn_log_clear.clicked.connect(self.log_view_clear)
        log_ctl.addWidget(self.log_cmd_input, 1)
        log_ctl.addWidget(self.btn_log_start)
        log_ctl.addWidget(self.btn_log_stop)
        log_ctl.addWidget(btn_log_clear)
        log_layout.addLayout(log_ctl)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Cascadia Code", 10))
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background:#1e1e2e; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:6px; padding:6px; }")
        self.log_view.setMaximumBlockCount(20000)
        log_layout.addWidget(self.log_view)

        self.inner.addTab(port_widget, "端口占用")
        self.inner.addTab(sys_widget, "systemd")
        self.inner.addTab(docker_widget, "Docker")
        self.inner.addTab(monitor_widget, "监控")
        self.inner.addTab(log_widget, "实时日志")
        layout.addWidget(self.inner)

        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(self._poll_monitor)

        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(120)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_flush_timer.start()

    def bind_worker(self, worker):
        # 停止旧流 / 停止监控 / 断开旧信号
        if self._log_stream_id and self.worker is not None:
            try:
                self.worker.close_stream(self._log_stream_id)
            except Exception:
                pass
        self._log_stream_id = ""
        self.btn_log_start.setEnabled(True)
        self.btn_log_stop.setEnabled(False)

        self.monitor_timer.stop()
        if self.btn_monitor.isChecked():
            self.btn_monitor.setChecked(False)
        self._prev_cpu = None
        self._prev_net = None
        self._monitor_pending = False
        self._monitor_pending_ts = 0.0

        old = self.worker
        # ★ 修复：精确断开旧信号，避免无差别 disconnect() 清掉其它订阅者
        if old is not None and old is not worker:
            try:
                old.stream_data.disconnect(self._on_stream_data)
            except (TypeError, RuntimeError):
                pass
            try:
                old.stream_closed.disconnect(self._on_stream_closed)
            except (TypeError, RuntimeError):
                pass

        self.worker = worker
        self._bind_exec_signal(worker)
        if worker is not None:
            try:
                worker.stream_data.connect(self._on_stream_data)
                worker.stream_closed.connect(self._on_stream_closed)
            except Exception:
                pass

    # ---------- 端口 ----------
    def _load_ports(self):
        if not self.worker:
            return
        self.btn_port.setEnabled(False)
        self._exec(
            "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null",
            self._on_ports, timeout=10, prefix="sys-port")

    def _on_ports(self, raw):
        self.btn_port.setEnabled(True)
        self.port_table.setRowCount(0)
        for line in raw.splitlines():
            if "LISTEN" not in line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            port = parts[3].rsplit(":", 1)[-1]
            proc = next((p for p in parts if "users:" in p), "")
            if not proc:
                # ★ 修复：netstat -tlnp 输出形如 "1234/sshd"（无 users: 前缀），
                #   旧逻辑拿不到 pid，kill 按钮点了也没反应。
                for p in reversed(parts):
                    left, sep, _ = p.partition("/")
                    if sep and left.isdigit():
                        proc = f"pid={left}"
                        break
            row = self.port_table.rowCount()
            self.port_table.insertRow(row)
            self.port_table.setItem(row, 0, QTableWidgetItem(port))
            self.port_table.setItem(row, 1, QTableWidgetItem(proc or "—"))
            btn = QPushButton("kill")
            btn.setObjectName("dangerBtn")
            btn.clicked.connect(
                lambda _=False, hint=proc: self._kill_proc(hint))
            self.port_table.setCellWidget(row, 2, btn)

    def _kill_proc(self, proc_hint):
        m = re.search(r"pid=(\d+)", proc_hint)
        if not m or not self.worker:
            return
        pid = m.group(1)
        reply = QMessageBox.question(
            self, "确认", f"kill -9 {pid}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._exec(f"kill -9 {pid}",
                       lambda _out: self._load_ports(),
                       timeout=8, prefix="sys-kill")

    # ---------- systemd ----------
    def _load_services(self, extra=""):
        if not self.worker:
            return
        self._exec(
            f"systemctl list-units --type=service --no-pager {extra}",
            self._on_services, timeout=15, prefix="sys-svc")

    def _on_services(self, raw):
        self.sys_table.setRowCount(0)
        for line in raw.splitlines():
            line = line.strip()
            if not line or "loaded" not in line:
                continue
            parts = line.split()
            unit_idx = next(
                (i for i, p in enumerate(parts) if p.endswith(".service")), -1)
            if unit_idx < 0:
                continue
            unit = parts[unit_idx]
            active = parts[unit_idx + 2] if unit_idx + 2 < len(parts) else ""
            row = self.sys_table.rowCount()
            self.sys_table.insertRow(row)
            self.sys_table.setItem(row, 0, QTableWidgetItem(unit))
            self.sys_table.setItem(row, 1, QTableWidgetItem(active))
            op_row = QWidget()
            op_layout = QHBoxLayout(op_row)
            op_layout.setContentsMargins(0, 0, 0, 0)
            op_layout.setSpacing(2)
            for label, sub in (("启", "start"), ("停", "stop"), ("重", "restart")):
                b = QPushButton(label)
                b.setFixedWidth(34)
                b.clicked.connect(
                    lambda _=False, u=unit, s=sub: self._svc_action(u, s))
                op_layout.addWidget(b)
            op_layout.addStretch()
            self.sys_table.setCellWidget(row, 2, op_row)

    def _svc_action(self, unit, action):
        if not self.worker:
            return
        self._exec(
            f"sudo systemctl {action} {shlex.quote(unit)}",
            lambda _out: self._load_services("--state=running"),
            timeout=20, prefix="sys-act")

    # ---------- docker ----------
    def _load_docker(self):
        if not self.worker:
            return
        self.btn_docker.setEnabled(False)
        self._exec(
            "docker ps --format '{{.Names}}\\t{{.Status}}\\t{{.Ports}}'",
            self._on_docker, timeout=12, prefix="sys-docker")

    def _on_docker(self, raw):
        self.btn_docker.setEnabled(True)
        self.docker_table.setRowCount(0)
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, status, ports = parts[0], parts[1], parts[2]
            row = self.docker_table.rowCount()
            self.docker_table.insertRow(row)
            self.docker_table.setItem(row, 0, QTableWidgetItem(name))
            self.docker_table.setItem(row, 1, QTableWidgetItem(status))
            self.docker_table.setItem(row, 2, QTableWidgetItem(ports or "—"))
            btn = QPushButton("logs")
            btn.clicked.connect(lambda _=False, n=name: self._docker_logs(n))
            self.docker_table.setCellWidget(row, 3, btn)

    def _docker_logs(self, name):
        if not self.worker:
            return
        self._exec(
            f"docker logs --tail 200 {shlex.quote(name)} 2>&1",
            lambda out, n=name: self._show_logs(n, out),
            timeout=15, prefix="sys-logs")

    def _show_logs(self, name, logs):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"docker logs - {name}")
        dlg.resize(780, 480)
        v = QVBoxLayout(dlg)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("Cascadia Code", 10))
        view.setPlainText(logs)
        v.addWidget(view)
        dlg.exec()

    # ---------- 实时资源监控 ----------
    def _toggle_monitor(self, checked):
        if checked:
            if not self.worker or not getattr(self.worker, "client", None):
                self.btn_monitor.setChecked(False)
                return
            self.monitor_status.setText("采集中…")
            self._monitor_pending = False
            self._monitor_pending_ts = 0.0
            self._prev_cpu = None
            self._prev_net = None
            self._poll_monitor()
            self.monitor_timer.start()
        else:
            self.monitor_timer.stop()
            self.monitor_status.setText("已停止")

    def _poll_monitor(self):
        if not self.worker or not getattr(self.worker, "client", None):
            self.monitor_timer.stop()
            self.btn_monitor.setChecked(False)
            self.monitor_status.setText("连接已断开")
            return
        # ★ 修复：请求丢包时 _monitor_pending 会永久 True，导致监控冻结；
        #   增加 15 秒兜底超时。
        if self._monitor_pending:
            if time.monotonic() - self._monitor_pending_ts < 15:
                return
        self._monitor_pending = True
        self._monitor_pending_ts = time.monotonic()
        self._monitor_seq += 1
        seq = self._monitor_seq
        cmd = (
            "cat /proc/loadavg 2>/dev/null; echo '---'; "
            "head -n 3 /proc/stat 2>/dev/null; echo '---'; "
            "grep -E '^(MemTotal|MemAvailable|MemFree|SwapTotal|SwapFree):' "
            "/proc/meminfo 2>/dev/null; echo '---'; "
            "cat /proc/net/dev 2>/dev/null"
        )
        self._exec(cmd,
                   lambda out, s=seq: self._on_monitor_data(s, out),
                   timeout=8, prefix="mon")

    def _on_monitor_data(self, seq, out):
        # ★ 修复：只有最新请求才清 pending，避免旧回调误清
        if seq == self._monitor_seq:
            self._monitor_pending = False
        if seq != self._monitor_seq:
            return
        if not out or out.startswith("[ERROR]"):
            self.monitor_status.setText("采集失败")
            return
        try:
            parts = out.split("---")
            if len(parts) < 4:
                self.monitor_status.setText("数据不完整")
                return
            loadavg = parts[0].strip().split()
            stat_lines = parts[1].strip().splitlines()
            meminfo = parts[2].strip().splitlines()
            netdev = parts[3].strip().splitlines()

            cpu_pct = None
            for line in stat_lines:
                if line.startswith("cpu "):
                    f = [int(x) for x in line.split()[1:]]
                    while len(f) < 8:
                        f.append(0)
                    user, nice, sys, idle, iowait, irq, softirq, steal = f[:8]
                    busy = user + nice + sys + irq + softirq + steal
                    total = busy + idle + iowait
                    if self._prev_cpu is not None:
                        db = busy - self._prev_cpu[0]
                        dt = total - self._prev_cpu[1]
                        if dt > 0:
                            cpu_pct = db / dt * 100
                    self._prev_cpu = (busy, total)
                    break

            mem = {}
            for line in meminfo:
                if ":" in line:
                    k, v = line.split(":", 1)
                    try:
                        mem[k.strip()] = int(v.split()[0])
                    except (ValueError, IndexError):
                        pass
            total_kb = mem.get("MemTotal", 1)
            avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
            used_kb = max(0, total_kb - avail_kb)
            mem_pct = used_kb / total_kb * 100 if total_kb else 0

            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            swap_used = max(0, swap_total - swap_free)
            swap_pct = swap_used / swap_total * 100 if swap_total else 0

            now = time.monotonic()
            net = {}
            for line in netdev:
                if ":" not in line:
                    continue
                iface, data = line.split(":", 1)
                iface = iface.strip()
                if not iface or iface == "lo":
                    continue
                fields = data.split()
                if len(fields) < 16:
                    continue
                try:
                    net[iface] = (int(fields[0]), int(fields[8]))
                except ValueError:
                    continue

            rx_rate = tx_rate = 0
            if self._prev_net is not None:
                dt = now - self._prev_net[0]
                if dt > 0:
                    for iface, (rx, tx) in net.items():
                        prx, ptx = self._prev_net[1].get(iface, (rx, tx))
                        rx_rate += (rx - prx) / dt
                        tx_rate += (tx - ptx) / dt
            self._prev_net = (now, net)

            if cpu_pct is not None:
                self.cpu_bar.setValue(int(cpu_pct))
                self.cpu_label.setText(f"{cpu_pct:.1f}%")
            self.mem_bar.setValue(int(mem_pct))
            self.mem_label.setText(
                f"{used_kb/1024:.0f}/{total_kb/1024:.0f} MB ({mem_pct:.0f}%)")
            if swap_total:
                self.swap_bar.setValue(int(swap_pct))
                self.swap_label.setText(
                    f"{swap_used/1024:.0f}/{swap_total/1024:.0f} MB "
                    f"({swap_pct:.0f}%)")
            else:
                self.swap_bar.setValue(0)
                self.swap_label.setText("未启用")
            if loadavg:
                self.load_label.setText(f"负载: {' '.join(loadavg[:3])}")
            self.net_label.setText(
                f"网络: ↓{self._fmt_rate(rx_rate)}  ↑{self._fmt_rate(tx_rate)}")
            self.monitor_status.setText("监控中")
        except Exception:
            self.monitor_status.setText("解析失败")

    @staticmethod
    def _fmt_rate(bps):
        if bps >= 1024 * 1024:
            return f"{bps/1024/1024:.1f} MB/s"
        if bps >= 1024:
            return f"{bps/1024:.1f} KB/s"
        return f"{bps:.0f} B/s"

    # ---------- 实时日志流 ----------
    def _start_log_stream(self):
        if not self.worker or not getattr(self.worker, "client", None):
            QMessageBox.warning(self, "提示", "SSH 连接未就绪")
            return
        if not hasattr(self.worker, "open_stream_command"):
            QMessageBox.warning(self, "提示", "当前连接不支持实时日志")
            return
        cmd = self.log_cmd_input.text().strip()
        if not cmd:
            return
        if self._log_stream_id:
            self._stop_log_stream()
        sid = f"log-{id(self)}-{int(time.time()*1000)}"
        try:
            ok = self.worker.open_stream_command(cmd, sid)
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(self, "提示", "启动日志流失败（权限或命令错误）")
            return
        self._log_stream_id = sid
        self.btn_log_start.setEnabled(False)
        self.btn_log_stop.setEnabled(True)
        self._append_log(f"\n[$ {cmd}]\n")

    def _stop_log_stream(self):
        sid = self._log_stream_id
        self._log_stream_id = ""
        if sid and self.worker is not None:
            try:
                self.worker.close_stream(sid)
            except Exception:
                pass
        self.btn_log_start.setEnabled(True)
        self.btn_log_stop.setEnabled(False)

    def _append_log(self, text):
        self._log_buf.append(text)
        if len(self._log_buf) > 200:
            self._flush_log_buffer()

    def _flush_log_buffer(self):
        if not self._log_buf:
            return
        text = "".join(self._log_buf)
        self._log_buf = []
        try:
            sb = self.log_view.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 5
            cur = self.log_view.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            self.log_view.setTextCursor(cur)
            self.log_view.insertPlainText(text)
            if at_bottom:
                sb.setValue(sb.maximum())
        except Exception:
            pass

    def _on_stream_data(self, sid, text):
        if sid != self._log_stream_id:
            return
        self._append_log(text)

    def _on_stream_closed(self, sid):
        if sid != self._log_stream_id:
            return
        self._log_stream_id = ""
        self.btn_log_start.setEnabled(True)
        self.btn_log_stop.setEnabled(False)
        self._append_log("\n[日志流已结束]\n")

    def log_view_clear(self):
        self.log_view.clear()
        self._log_buf = []


# ============================================================
# 开发面板聚合
# ============================================================
class DevPanel(_AsyncExecMixin, QWidget):
    def __init__(self, tab):
        QWidget.__init__(self)
        self.tab = tab
        self.launcher = None
        self._init_exec_state()
        self._tunnel_sig_connected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        launch_box = QGroupBox("远程开发环境")
        launch_layout = QVBoxLayout(launch_box)
        launch_layout.setSpacing(6)

        self.status_label = QLabel("连接后检测可用服务…")
        self.status_label.setObjectName("hintLabel")
        self.status_label.setWordWrap(True)
        launch_layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.btn_code = QPushButton("▶ 启动 VS Code Server")
        self.btn_code.clicked.connect(lambda: self._launch("code"))
        self.btn_code.setEnabled(False)
        self.btn_jupyter = QPushButton("▶ 启动 Jupyter Lab")
        self.btn_jupyter.clicked.connect(lambda: self._launch("jupyter"))
        self.btn_jupyter.setEnabled(False)
        btn_row.addWidget(self.btn_code)
        btn_row.addWidget(self.btn_jupyter)
        btn_row.addStretch()
        launch_layout.addLayout(btn_row)

        self.result_label = QLabel("")
        self.result_label.setObjectName("hintLabel")
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result_label.setOpenExternalLinks(True)
        launch_layout.addWidget(self.result_label)

        layout.addWidget(launch_box)

        self.inner_tabs = QTabWidget()
        self.git_panel = GitPanel(tab)
        self.system_panel = SystemPanel(tab)
        self.inner_tabs.addTab(self.git_panel, "Git")
        self.inner_tabs.addTab(self.system_panel, "系统")
        layout.addWidget(self.inner_tabs, 1)

    def bind_worker(self, worker):
        if not worker or not getattr(worker, "client", None):
            return
        # ★ 修复：重连会重建 TunnelManager，必须重置标志，否则新管理器
        #   的 tunnel_opened 永远不会被连接，启动服务后看不到 URL。
        self._tunnel_sig_connected = False
        self.git_panel.bind_worker(worker)
        self.system_panel.bind_worker(worker)
        self._bind_exec_signal(worker)
        if self.tab.tunnel_mgr:
            self.launcher = RemoteDevLauncher(self.tab, self.tab.tunnel_mgr)
            QTimer.singleShot(800, self._refresh_status)

    def _refresh_status(self):
        if not self.launcher:
            return
        self.status_label.setText("检测可用服务…")
        parts = []
        for name, spec in self.launcher.SERVICES.items():
            parts.append(
                f'if ( {spec["check"]} ) >/dev/null 2>&1; '
                f'then echo "Y:{name}"; else echo "N:{name}"; fi;')
        self._exec("; ".join(parts), self._on_detect_done,
                   timeout=15, prefix="dev-detect")

    def _on_detect_done(self, out):
        if not self.launcher:
            return
        found = {}
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Y:"):
                found[line[2:]] = True
            elif line.startswith("N:"):
                found[line[2:]] = False
        labels = []
        for k in self.launcher.SERVICES:
            label = self.launcher.SERVICES[k]["label"]
            labels.append(f"{'✅' if found.get(k) else '❌'} {label}")
        self.status_label.setText("检测结果：" + "  ".join(labels))
        self.btn_code.setEnabled(found.get("code", False))
        self.btn_jupyter.setEnabled(found.get("jupyter", False))

    def _launch(self, service):
        if not self.launcher:
            return
        spec = self.launcher.SERVICES.get(service)
        worker = self.tab.worker
        if not spec or not worker or not getattr(worker, "client", None):
            return
        remote_port = spec["default_port"]
        cmd = spec["start"].format(port=remote_port)
        self.result_label.setText(f"正在启动 {service}…")
        self._exec(cmd, lambda _out: None, timeout=8, prefix="dev-launch")
        QTimer.singleShot(
            2000, lambda s=service, p=remote_port: self._open_tunnel(s, p))
        if not self._tunnel_sig_connected and self.tab.tunnel_mgr:
            self.tab.tunnel_mgr.tunnel_opened.connect(self._on_tunnel)
            self._tunnel_sig_connected = True

    def _open_tunnel(self, service, remote_port):
        if not self.launcher or not self.launcher.tunnels:
            return
        try:
            self.launcher.tunnels.open(
                "127.0.0.1", remote_port,
                label=f"{service}:{remote_port}", service=service)
        except Exception:
            self.result_label.setText(f"❌ 打开 {service} 隧道失败")

    def _on_tunnel(self, rec):
        if rec.service not in RemoteDevLauncher.SERVICES:
            return
        path = RemoteDevLauncher.SERVICES[rec.service]["url_path"]
        url = f"http://127.0.0.1:{rec.local_port}{path}"
        self.result_label.setText(
            f'✅ {rec.service} 已启动 → <a href="{url}">{url}</a>')