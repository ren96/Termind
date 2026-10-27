import os
import re
import json
import weakref

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QMessageBox, QLabel, QTabWidget, QCheckBox, QMenu)
from PyQt6.QtCore import Qt, QObject, pyqtSlot, pyqtSignal, QUrl, QEvent, QSettings, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtGui import (QGuiApplication, QAction, QCursor, QShortcut,
                         QKeySequence, QDesktopServices)

from workers import SSHWorker, LocalShellWorker
from ui_sftp import SFTPPanel
from tunnel_manager import TunnelManager
from remote_panels import PortForwardPanel, DevPanel
from constants import (TERMINAL_HISTORY_MAX, CMD_HISTORY_MAX,
                       ORG_NAME, APP_NAME, DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE,
                       DEFAULT_SCROLLBACK, DEFAULT_AUTO_RECONNECT,
                       DEFAULT_AUTO_RECONNECT_INTERVAL, DANGEROUS_PATTERNS,
                       TERMINAL_FONT_FAMILY, TERM_BG, TERM_FG,
                       ENV_PRODUCTION, ENV_STAGING, ENV_META,
                       get_resource_path)
from theme import SUCCESS, WARNING, DANGER

_DANGER_RES = [re.compile(p) for p in DANGEROUS_PATTERNS]

_PENDING_WORKERS = []

_MAX_WRITE_BUFFER_BYTES = 2 * 1024 * 1024
_MAX_FLUSH_BYTES = 128 * 1024


def _make_pending_cleanup(worker):
    wref = weakref.ref(worker)

    def _cleanup(*_args, **_kwargs):
        w = wref()
        if w is None:
            return
        try:
            _PENDING_WORKERS.remove(w)
        except ValueError:
            pass
        try:
            w.deleteLater()
        except RuntimeError:
            pass

    return _cleanup


def is_dangerous_command(cmd):
    for r in _DANGER_RES:
        if r.search(cmd):
            return True
    return False


def _qs():
    return QSettings(ORG_NAME, APP_NAME)


class Bridge(QObject):
    def __init__(self, tab):
        super().__init__()
        self.tab = tab

    @pyqtSlot(str)
    def send_input(self, data):
        if self.tab.worker:
            self.tab.worker.send_data(data)

    @pyqtSlot(int, int)
    def resize_terminal(self, cols, rows):
        if self.tab.worker:
            self.tab.worker.resize_pty(cols, rows)

    @pyqtSlot(str)
    def copy_to_clipboard(self, text):
        QGuiApplication.clipboard().setText(text)

    @pyqtSlot()
    def get_clipboard_content(self):
        if not self.tab.worker:
            return
        raw = QGuiApplication.clipboard().text()
        if not raw:
            return
        if not self.tab._confirm_multiline_paste(raw):
            return
        text = raw.replace("\r\n", "\r").replace("\n", "\r")
        self.tab.worker.send_data(text)

    @pyqtSlot(str)
    def show_context_menu(self, selection):
        self.tab.show_terminal_context_menu(selection)

    @pyqtSlot(int)
    def save_font_size(self, size):
        _qs().setValue("terminal/font_size", int(size))

    @pyqtSlot(str)
    def open_url(self, url):
        if not url:
            return
        if not (url.startswith("http://") or
                url.startswith("https://") or
                url.startswith("ftp://")):
            return
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception:
            pass


class TerminalTab(QWidget):
    status_changed = pyqtSignal(str)

    def __init__(self, title, conn_type="ssh", server_info=None,
                 local_shell="auto"):
        super().__init__()
        self.title = title
        self.conn_type = conn_type
        self.server_info = server_info
        self.local_shell = local_shell

        self.pinned = False
        self.color = ""
        self.icon_emoji = ""

        self.env = ""
        if server_info:
            self.env = server_info.get("env", "") or ""

        self._write_buffer = []
        self._write_buffer_bytes = 0
        self._write_timer = QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(16)
        self._write_timer.timeout.connect(self._flush_terminal)

        self.worker = None
        self.tunnel_mgr = None
        self._connected = False
        self._closing = False
        self._user_disconnected = False
        self._danger_whitelist = set()
        self._disconnect_reason = ""

        self._pending_old_worker = None
        self._pending_wait_tries = 0

        self.cmd_history = []
        self.history_index = -1

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._do_auto_reconnect)

        self._delayed_start_timer = QTimer(self)
        self._delayed_start_timer.setSingleShot(True)
        self._delayed_start_timer.timeout.connect(self._start_new_worker)

        self.setAcceptDrops(True)
        self.init_ui()
        self._install_shortcuts()
        self.start_connection()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        self.inner_tabs = QTabWidget()
        self.inner_tabs.currentChanged.connect(self.on_inner_tab_changed)

        cmd_widget = QWidget()
        cmd_layout = QVBoxLayout(cmd_widget)
        cmd_layout.setContentsMargins(0, 6, 0, 0)
        cmd_layout.setSpacing(8)

        cmd_title_bar = QHBoxLayout()
        cmd_title_bar.setSpacing(8)

        self.shell_status = QLabel()
        self._set_status("正在连接...", WARNING)
        cmd_title_bar.addWidget(self.shell_status)

        btn_find = QPushButton("查找")
        btn_find.setObjectName("ghostBtn")
        btn_find.clicked.connect(self.open_search)

        btn_ctrl_c = QPushButton("Ctrl+C")
        btn_ctrl_c.setObjectName("ghostBtn")
        btn_ctrl_c.clicked.connect(self.send_ctrl_c)

        btn_clear = QPushButton("清屏")
        btn_clear.setObjectName("ghostBtn")
        btn_clear.clicked.connect(self.clear_terminal)

        self.btn_reconnect = QPushButton("重新连接")
        self.btn_reconnect.clicked.connect(self.reconnect)
        self.btn_reconnect.hide()

        self.btn_disconnect = QPushButton("断开连接")
        self.btn_disconnect.setObjectName("dangerBtn")
        self.btn_disconnect.clicked.connect(self.disconnect_all)

        cmd_title_bar.addStretch()
        cmd_title_bar.addWidget(btn_find)
        cmd_title_bar.addWidget(btn_ctrl_c)
        cmd_title_bar.addWidget(btn_clear)
        cmd_title_bar.addWidget(self.btn_reconnect)
        cmd_title_bar.addWidget(self.btn_disconnect)
        cmd_layout.addLayout(cmd_title_bar)

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject("bridge", self.bridge)

        self.web_view = QWebEngineView()
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.web_view.page().setWebChannel(self.channel)

        html_path = get_resource_path('terminal.html')
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except OSError as e:
            QMessageBox.critical(self, "错误", f"无法读取终端页面: {e}")
            html_content = "<html><body>terminal.html missing</body></html>"

        s = _qs()
        font_size = s.value("terminal/font_size", DEFAULT_FONT_SIZE, type=int)
        font_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, font_size))
        scrollback = s.value("terminal/scrollback", DEFAULT_SCROLLBACK, type=int)

        html_content = html_content.replace('const _initialFontSize = 14;', f'const _initialFontSize = {font_size};')
        html_content = html_content.replace('const _initialScrollback = 5000;',
                                            f'const _initialScrollback = {scrollback};')
        html_content = html_content.replace("const _termBg = '#1e1e2e';", f"const _termBg = '{TERM_BG}';")
        html_content = html_content.replace("const _termFg = '#cdd6f4';", f"const _termFg = '{TERM_FG}';")
        html_content = html_content.replace("const _termFont = 'Cascadia Code, Consolas, \"Courier New\", monospace';",
                                            f"const _termFont = '{TERMINAL_FONT_FAMILY}';")
        html_content = html_content.replace('background-color: #1e1e2e;', f'background-color: {TERM_BG};')

        env_meta = ENV_META.get(self.env, {})
        watermark = env_meta.get("watermark", "")
        if watermark:
            html_content = html_content.replace(
                "</style>",
                "#env-watermark { position:fixed; top:50%; left:50%; "
                "transform:translate(-50%,-50%) rotate(-30deg); "
                "font-size:120px; font-weight:900; "
                "color:rgba(239,68,68,0.06); pointer-events:none; "
                "z-index:9999; font-family:monospace; user-select:none; }\n"
                "</style>")
            html_content = html_content.replace(
                '<div id="terminal"></div>',
                '<div id="terminal"></div>'
                f'<div id="env-watermark">{watermark}</div>')

        self.web_view.setHtml(html_content, QUrl.fromLocalFile(os.path.abspath(html_path)))
        cmd_layout.addWidget(self.web_view)

        input_bar = QHBoxLayout()
        input_bar.setSpacing(8)
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("点击终端直接操作；Ctrl+Shift+C/V 复制/粘贴；↑/↓ 历史")
        self.cmd_input.setMinimumHeight(38)
        self.cmd_input.returnPressed.connect(self.send_cmd_from_input)
        self.cmd_input.installEventFilter(self)

        btn_send = QPushButton("发送")
        btn_send.setObjectName("primaryBtn")
        btn_send.setMinimumHeight(38)
        btn_send.setMinimumWidth(84)
        btn_send.clicked.connect(self.send_cmd_from_input)

        self.chk_hide_pwd = QCheckBox("隐藏输入")
        self.chk_hide_pwd.toggled.connect(self.toggle_pwd_echo)

        input_bar.addWidget(self.cmd_input)
        input_bar.addWidget(btn_send)
        input_bar.addWidget(self.chk_hide_pwd)
        cmd_layout.addLayout(input_bar)

        self.sftp_panel = SFTPPanel(self)
        self.port_panel = PortForwardPanel(self)
        self.dev_panel = DevPanel(self)

        self.inner_tabs.addTab(cmd_widget, "命令行")
        self.inner_tabs.addTab(self.sftp_panel, "上传文件")
        if self.conn_type == "ssh":
            self.inner_tabs.addTab(self.port_panel, "端口转发")
            self.inner_tabs.addTab(self.dev_panel, "开发面板")
        main_layout.addWidget(self.inner_tabs)

    def _install_shortcuts(self):
        sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        sc_find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_find.activated.connect(self.open_search)

        sc_next = QShortcut(QKeySequence("Ctrl+G"), self)
        sc_next.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_next.activated.connect(
            lambda: self.web_view.page().runJavaScript("window.searchNext();"))

    def open_search(self):
        self.web_view.page().runJavaScript("window.openSearch && window.openSearch();")

    def _set_status(self, text, color):
        self.shell_status.setText(f"{self.title} {text}")
        self.shell_status.setStyleSheet(
            f"color: {color}; background-color: {color}1A; border-radius: 10px; "
            f"padding: 2px 10px; font-size: 11px; font-weight: 600;")
        self.status_changed.emit(text)

    def on_inner_tab_changed(self, index):
        if index == 0:
            self.web_view.page().runJavaScript(
                "window.fitTerminal && window.fitTerminal();")

    def _is_active_worker_sender(self, sender):
        if sender is None:
            return True
        if sender is self.worker:
            return True
        w = self.worker
        if w is not None and getattr(w, '_bridge', None) is sender:
            return True
        return False

    def show_terminal_context_menu(self, selection):
        menu = QMenu(self)
        if selection:
            act_copy = QAction("复制", self)
            act_copy.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(selection))
            menu.addAction(act_copy)

        act_paste = QAction("粘贴", self)
        act_paste.triggered.connect(self._paste_from_clipboard)
        menu.addAction(act_paste)

        act_select_all = QAction("全选", self)
        act_select_all.triggered.connect(
            lambda: self.web_view.page().runJavaScript(
                "if (window.term) term.selectAll();"))
        menu.addAction(act_select_all)

        menu.addSeparator()
        act_find = QAction("查找 (Ctrl+F)", self)
        act_find.triggered.connect(self.open_search)
        menu.addAction(act_find)

        act_clear = QAction("清屏", self)
        act_clear.triggered.connect(self.clear_terminal)
        menu.addAction(act_clear)

        act_reconnect = QAction("重新连接", self)
        act_reconnect.triggered.connect(self.reconnect)
        menu.addAction(act_reconnect)

        menu.exec(QCursor.pos())

    def _paste_from_clipboard(self):
        if not self.worker:
            return
        raw = QGuiApplication.clipboard().text()
        if not raw:
            return
        if not self._confirm_multiline_paste(raw):
            return
        text = raw.replace("\r\n", "\r").replace("\n", "\r")
        self.worker.send_data(text)

    def _confirm_multiline_paste(self, raw_text):
        if not raw_text:
            return True
        normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [ln for ln in normalized.split("\n") if ln.strip()]
        if len(lines) <= 1:
            return True
        is_prod = (self.env == ENV_PRODUCTION)
        if not is_prod:
            try:
                if not _qs().value("ui/confirm_multiline_paste", False, type=bool):
                    return True
            except Exception:
                pass
        preview_lines = lines[:10]
        preview = "\n".join(preview_lines)
        if len(lines) > 10:
            preview += f"\n... (共 {len(lines)} 行)"
        prefix = "【生产环境】" if is_prod else ""
        reply = QMessageBox.question(
            self, "粘贴多行内容",
            f"{prefix}即将粘贴 {len(lines)} 行内容到终端：\n\n{preview}\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    def send_ctrl_c(self):
        if self.worker and self._connected:
            self.worker.send_data('\x03')
        self.cmd_input.setFocus()

    def clear_terminal(self):
        self.web_view.page().runJavaScript("window.clearTerminal();")
        self.cmd_input.setFocus()

    def toggle_pwd_echo(self, checked):
        self.cmd_input.setEchoMode(
            QLineEdit.EchoMode.Password if checked else QLineEdit.EchoMode.Normal)

    def start_connection(self):
        self._set_status("正在连接...", WARNING)
        self._disconnect_reason = ""

        if self.conn_type == "ssh":
            info = self.server_info
            self.worker = SSHWorker(
                info['host'], info['port'], info['user'],
                pwd=info.get('pwd'), key_path=info.get('key_path'),
                jump=info.get('jump'))
            self.worker.host_key_verify.connect(self.on_host_key_verify)
        else:
            self.worker = LocalShellWorker(self.local_shell)

        self.worker.data_received.connect(self.on_data)
        self.worker.connection_lost.connect(self.on_disconnect)
        if hasattr(self.worker, 'disconnected_with_reason'):
            self.worker.disconnected_with_reason.connect(self._on_disconnect_reason)
        if hasattr(self.worker, 'connected'):
            self.worker.connected.connect(self.on_worker_connected)

        _PENDING_WORKERS.append(self.worker)
        self.worker.finished.connect(_make_pending_cleanup(self.worker))

        self.worker.start()
        self.btn_reconnect.hide()

    def _on_disconnect_reason(self, reason):
        if not self._is_active_worker_sender(self.sender()):
            return
        self._disconnect_reason = reason or ""

    def on_host_key_verify(self, hostname, key_type, fp):
        worker = self.sender()
        if not isinstance(worker, SSHWorker):
            worker = self.worker
        if not isinstance(worker, SSHWorker):
            return

        if worker is not self.worker:
            try:
                worker.respond_host_key(False)
            except Exception:
                pass
            return

        parent = self.window() or self
        try:
            reply = QMessageBox.question(
                parent, "未知主机密钥",
                f"首次连接 {hostname}\n"
                f"密钥类型: {key_type}\n"
                f"指纹: {fp}\n\n"
                f"是否信任该主机并继续连接？\n"
                f"（信任后将写入 ~/.ssh/known_hosts）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
        except Exception:
            reply = QMessageBox.StandardButton.No

        if worker is not self.worker:
            try:
                worker.respond_host_key(False)
            except Exception:
                pass
            return

        try:
            worker.respond_host_key(reply == QMessageBox.StandardButton.Yes)
        except Exception:
            pass

    def on_worker_connected(self):
        if not self._is_active_worker_sender(self.sender()):
            return
        if self.conn_type == "ssh":
            self.sftp_panel.bind_worker(self.worker)
            if self.tunnel_mgr is None:
                self.tunnel_mgr = TunnelManager(self.worker, self)
                self.port_panel.set_tunnel_manager(self.tunnel_mgr)
            self.dev_panel.bind_worker(self.worker)
        else:
            self.sftp_panel.bind_worker(None)
        if not self._connected:
            self._connected = True
            self._user_disconnected = False
            self._set_status("已连接", SUCCESS)

    def _detach_worker(self, worker):
        if worker is None:
            return
        for sig_name, slot in (
                ('data_received', self.on_data),
                ('connection_lost', self.on_disconnect),
                ('connected', self.on_worker_connected),
                ('host_key_verify', self.on_host_key_verify),
                ('disconnected_with_reason', self._on_disconnect_reason),
        ):
            try:
                sig = getattr(worker, sig_name, None)
                if sig is None:
                    continue
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def reconnect(self):
        if self._delayed_start_timer.isActive() or self._pending_old_worker is not None:
            return

        self._reconnect_timer.stop()
        self._delayed_start_timer.stop()
        self._user_disconnected = False
        self._disconnect_reason = ""
        self._pending_wait_tries = 0

        self.btn_reconnect.hide()
        self._set_status("正在连接...", WARNING)

        if self.tunnel_mgr:
            self.tunnel_mgr.close_all()
            self.tunnel_mgr = None

        old = self.worker
        self.worker = None
        self._connected = False
        self._closing = False

        if old is not None:
            self._detach_worker(old)
            try:
                old.cancel_pending_host_key()
            except Exception:
                pass
            try:
                old.disconnect()
            except Exception:
                pass
            self._pending_old_worker = old
        else:
            self._pending_old_worker = None

        self._delayed_start_timer.start(50)

    def _start_new_worker(self):
        if self._closing or self._user_disconnected:
            return
        if self.worker is not None:
            return

        old = self._pending_old_worker
        if old is not None:
            still_running = False
            try:
                still_running = old.isRunning()
            except RuntimeError:
                still_running = False
            if still_running:
                self._pending_wait_tries += 1
                if self._pending_wait_tries < 75:
                    self._delayed_start_timer.start(200)
                    return
                # ★ 修复：旧 worker 在 start_connection 时已加入 _PENDING_WORKERS，
                #   此处不能再 append，否则强引用残留一份导致清理不彻底。
                self._pending_old_worker = None
                self._pending_wait_tries = 0
            else:
                self._pending_old_worker = None
                self._pending_wait_tries = 0

        self.start_connection()
        self._write_terminal("\r\n\x1b[33m[正在尝试重新连接...]\x1b[0m\r\n")

    def disconnect_all(self):
        self._user_disconnected = True
        self._disconnect_reason = "用户主动断开"
        self._reconnect_timer.stop()
        self._delayed_start_timer.stop()

        # ★ 修复：旧 worker 已在 _PENDING_WORKERS 中，仅清引用
        self._pending_old_worker = None

        self._pending_wait_tries = 0
        if self.tunnel_mgr:
            self.tunnel_mgr.close_all()
        if self.worker:
            try:
                self.worker.cancel_pending_host_key()
            except Exception:
                pass
            try:
                self.worker.disconnect()
            except Exception:
                pass
        if not self._connected:
            self._set_status("正在断开...", WARNING)

    def _do_auto_reconnect(self):
        if self._closing or self._connected or self._user_disconnected:
            return
        self._write_terminal("\r\n\x1b[33m[自动重连...]\x1b[0m\r\n")
        self.reconnect()

    def eventFilter(self, obj, event):
        if obj == self.cmd_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                if self.cmd_history:
                    if self.history_index == -1:
                        self.history_index = len(self.cmd_history) - 1
                    elif self.history_index > 0:
                        self.history_index -= 1
                    self.cmd_input.setText(self.cmd_history[self.history_index])
                return True
            elif key == Qt.Key.Key_Down:
                if self.history_index >= 0 and self.cmd_history:
                    if self.history_index < len(self.cmd_history) - 1:
                        self.history_index += 1
                        self.cmd_input.setText(self.cmd_history[self.history_index])
                    else:
                        self.history_index = -1
                        self.cmd_input.clear()
                return True
        return super().eventFilter(obj, event)

    def _write_terminal(self, text):
        if not text or self._closing:
            return
        if self._write_buffer_bytes >= _MAX_WRITE_BUFFER_BYTES:
            # ★ 优化：溢出不再静默丢弃全部，改为直接投递警告并清空积压，
            #   避免 UI 长时间无响应且用户看不到任何提示。
            self._write_buffer.clear()
            self._write_buffer_bytes = 0
            self._write_terminal_direct(
                "\r\n\x1b[33m[输出过快，已丢弃部分内容]\x1b[0m\r\n")
            return
        self._write_buffer.append(text)
        self._write_buffer_bytes += len(text)
        if not self._write_timer.isActive():
            self._write_timer.start()

    def _write_terminal_direct(self, text):
        if not text or not self.web_view or not self.web_view.page():
            return
        try:
            self.web_view.page().runJavaScript(
                f"window.writeToTerminal({json.dumps(text)});")
        except Exception:
            pass

    def _flush_terminal(self):
        if not self._write_buffer:
            return

        chunk = []
        size = 0
        idx = 0
        n = len(self._write_buffer)
        while idx < n and size < _MAX_FLUSH_BYTES:
            item = self._write_buffer[idx]
            chunk.append(item)
            size += len(item)
            idx += 1
        self._write_buffer = self._write_buffer[idx:]

        text = "".join(chunk)
        self._write_buffer_bytes -= len(text)
        if self._write_buffer_bytes < 0:
            self._write_buffer_bytes = 0

        self._write_terminal_direct(text)

        if self._write_buffer and not self._write_timer.isActive():
            self._write_timer.start(8)

    def on_data(self, text):
        if self._closing:
            return
        if not self._is_active_worker_sender(self.sender()):
            return
        self._write_terminal(text)

    def on_disconnect(self):
        if self._closing:
            return
        if not self._is_active_worker_sender(self.sender()):
            return
        if self._connected:
            reason = self._disconnect_reason or "未知原因"
            self._write_terminal(
                f"\r\n\x1b[31m[连接已断开: {reason}]\x1b[0m\r\n")
        self._connected = False
        self._set_status("已断开", DANGER)
        self.btn_reconnect.show()

        s = _qs()
        if (self.conn_type == "ssh"
                and not self._user_disconnected
                and s.value("terminal/auto_reconnect", DEFAULT_AUTO_RECONNECT, type=bool)):
            interval = s.value(
                "terminal/auto_reconnect_interval",
                DEFAULT_AUTO_RECONNECT_INTERVAL, type=int)
            interval = max(1, int(interval))
            self._reconnect_timer.start(interval * 1000)

    def _confirm_dangerous(self, cmd):
        is_prod = (self.env == ENV_PRODUCTION)
        if cmd.strip() in self._danger_whitelist and not is_prod:
            return True
        box = QMessageBox(self)
        title = "⚠️ 危险命令"
        if is_prod:
            title += " · 生产环境"
        box.setWindowTitle(title)
        prefix = "【生产环境】此命令可能影响线上服务！\n\n" if is_prod else ""
        box.setText(f"{prefix}检测到潜在危险命令，是否继续执行？\n\n{cmd}")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        cb = QCheckBox("本次会话不再提醒此命令")
        if is_prod:
            cb.setEnabled(False)
        box.setCheckBox(cb)
        result = box.exec()
        if result != QMessageBox.StandardButton.Yes:
            return False
        if cb.isChecked() and not is_prod:
            self._danger_whitelist.add(cmd.strip())
        return True

    def send_cmd_from_input(self):
        if self._closing or not self._connected or not self.worker:
            self.cmd_input.setFocus()
            return

        cmd = self.cmd_input.text()

        if cmd.strip() and is_dangerous_command(cmd):
            if not self._confirm_dangerous(cmd):
                self.cmd_input.setFocus()
                return

        if cmd.strip():
            if not self.cmd_history or self.cmd_history[-1] != cmd:
                self.cmd_history.append(cmd)
                if len(self.cmd_history) > CMD_HISTORY_MAX:
                    self.cmd_history = self.cmd_history[-CMD_HISTORY_MAX:]
            self.history_index = -1

        self.worker.send_data(cmd + "\r")
        self.cmd_input.clear()
        self.cmd_input.setFocus()

    def send_data_raw(self, data):
        if self.worker and self._connected and not self._closing:
            self.worker.send_data(data)
            return True
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        page = self.web_view.page()
        if page:
            page.runJavaScript("window.debouncedFit && window.debouncedFit();")

    def close_connection(self):
        self._closing = True
        self._write_timer.stop()
        self._write_buffer.clear()
        self._write_buffer_bytes = 0
        self._reconnect_timer.stop()
        self._delayed_start_timer.stop()

        # ★ 修复：旧 worker 已在 _PENDING_WORKERS 中，仅清引用
        self._pending_old_worker = None
        self._pending_wait_tries = 0

        if self.tunnel_mgr:
            self.tunnel_mgr.close_all()
            self.tunnel_mgr = None
        if self.worker:
            try:
                self.worker.cancel_pending_host_key()
            except Exception:
                pass
            try:
                self.worker.disconnect()
            except Exception:
                pass

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if self.conn_type != "ssh":
            return
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                self.inner_tabs.setCurrentWidget(self.sftp_panel)
                self.sftp_panel.upload_file(file_path)
            elif os.path.isdir(file_path):
                self.inner_tabs.setCurrentWidget(self.sftp_panel)
                self.sftp_panel.upload_folder_path(file_path)