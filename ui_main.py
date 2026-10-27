import os

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QPushButton,
    QTabWidget, QLineEdit, QLabel, QDialog, QFormLayout, QMessageBox,
    QMenu, QHeaderView, QGridLayout, QComboBox, QFileDialog,
    QInputDialog, QListWidget, QListWidgetItem, QFrame, QTabBar, QSizePolicy,
    QColorDialog, QToolButton,
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import (QBrush, QColor, QShortcut, QKeySequence, QAction, QIcon,
                         QPainter, QPixmap, QFont)

import config_manager
import terminal_tab as _terminal_tab
from theme import (TEXT_MUTED, SUCCESS, DANGER, WARNING, PRIMARY, get_stylesheet)
from terminal_tab import TerminalTab, is_dangerous_command
from constants import (ORG_NAME, APP_NAME, APP_VERSION, get_resource_path,
                       ENV_PRODUCTION, ENV_STAGING, ENV_DEVELOPMENT, ENV_META,
                       DEFAULT_LOCAL_SHELL)
from preferences import PreferencesDialog
from ssh_config_import import parse_ssh_config, default_ssh_config_path
from about_dialog import AboutDialog


_LINGERING_WORKERS = []


def _qs():
    return QSettings(ORG_NAME, APP_NAME)


def _emoji_pixmap(emoji, size=16):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont()
    font.setPixelSize(size - 2)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, emoji)
    p.end()
    return pix


class ServerDialog(QDialog):
    def __init__(self, parent=None, server_data=None):
        super().__init__(parent)
        self.setWindowTitle("服务器配置")
        self.resize(500, 0)
        self.server_data = server_data or {}
        layout = QFormLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        self.input_name = QLineEdit(self.server_data.get("name", ""))
        self.input_host = QLineEdit(self.server_data.get("host", ""))
        self.input_port = QLineEdit(str(self.server_data.get("port", "22")))
        self.input_user = QLineEdit(self.server_data.get("user", "root"))
        self.input_group = QLineEdit(self.server_data.get("group", ""))
        self.input_group.setPlaceholderText("留空表示不分组")

        self.auth_combo = QComboBox()
        self.auth_combo.addItems(["密码认证", "密钥认证"])
        auth_type = self.server_data.get("auth_type", "password")
        self.auth_combo.setCurrentIndex(0 if auth_type == "password" else 1)
        self.auth_combo.currentIndexChanged.connect(self.on_auth_changed)

        self.key_widget = QWidget()
        key_layout = QHBoxLayout(self.key_widget)
        key_layout.setContentsMargins(0, 0, 0, 0)
        self.input_key = QLineEdit(self.server_data.get("key_path", ""))
        self.input_key.setPlaceholderText("请选择 .pem 或 id_rsa 文件")
        btn_browse = QPushButton("浏览...")
        btn_browse.clicked.connect(self.browse_key)
        key_layout.addWidget(self.input_key, 1)
        key_layout.addWidget(btn_browse)
        self.key_widget.hide()

        pwd = config_manager.get_password(
            self.server_data.get("host", ""), self.server_data.get("user", "")) or ""
        self._original_pwd = pwd
        self._pwd_touched = False
        self.input_pwd = QLineEdit(pwd)
        self.input_pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_pwd.textEdited.connect(self._on_pwd_edited)

        self.btn_eye = QToolButton()
        self.btn_eye.setText("👁")
        self.btn_eye.setCheckable(True)
        self.btn_eye.setToolTip("显示/隐藏密码")
        self.btn_eye.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_eye.setStyleSheet(
            "QToolButton { border: none; background: transparent; font-size: 15px; }"
            "QToolButton:hover { background: #F1F2F7; border-radius: 4px; }")
        self.btn_eye.toggled.connect(self._toggle_pwd_visible)

        pwd_row = QWidget()
        pwd_layout = QHBoxLayout(pwd_row)
        pwd_layout.setContentsMargins(0, 0, 0, 0)
        pwd_layout.setSpacing(4)
        pwd_layout.addWidget(self.input_pwd, 1)
        pwd_layout.addWidget(self.btn_eye)

        self.color_value = self.server_data.get("color", "") or ""
        self.icon_value = self.server_data.get("icon", "") or ""

        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(100, 28)
        self._update_color_button()
        self.btn_color.clicked.connect(self._pick_color)

        self.input_icon = QLineEdit(self.icon_value)
        self.input_icon.setPlaceholderText("输入 emoji，例如 🐧")
        self.input_icon.setMaxLength(8)

        self.env_combo = QComboBox()
        for key in (ENV_DEVELOPMENT, ENV_STAGING, ENV_PRODUCTION):
            meta = ENV_META[key]
            self.env_combo.addItem(meta["label"], key)
        cur_env = self.server_data.get("env", ENV_DEVELOPMENT)
        idx = self.env_combo.findData(cur_env)
        self.env_combo.setCurrentIndex(max(0, idx))

        self._pwd_label = QLabel("密码:")

        layout.addRow("别名:", self.input_name)
        layout.addRow("分组:", self.input_group)
        layout.addRow("IP:", self.input_host)
        layout.addRow("端口:", self.input_port)
        layout.addRow("用户:", self.input_user)
        layout.addRow("认证方式:", self.auth_combo)
        layout.addRow("私钥路径:", self.key_widget)
        layout.addRow(self._pwd_label, pwd_row)
        layout.addRow("环境:", self.env_combo)
        layout.addRow("标签颜色:", self.btn_color)
        layout.addRow("标签图标:", self.input_icon)

        # ★ 新增：跳板机选择
        self.jump_combo = QComboBox()
        self.jump_combo.addItem("（不使用跳板机）", "")
        try:
            for s in config_manager.load_servers():
                h = s.get('host', '')
                u = s.get('user', '')
                if h and u:
                    label = f"{s.get('name', h)} ({u}@{h})"
                    self.jump_combo.addItem(label, f"{u}@{h}")
        except Exception:
            pass
        cur_jh = self.server_data.get('jump_host', '') or ''
        cur_ju = self.server_data.get('jump_user', '') or ''
        if cur_jh and cur_ju:
            idx = self.jump_combo.findData(f"{cur_ju}@{cur_jh}")
            if idx >= 0:
                self.jump_combo.setCurrentIndex(idx)

        layout.addRow("跳板机:", self.jump_combo)

        btn = QPushButton("保存")
        btn.setObjectName("primaryBtn")
        btn.clicked.connect(self.save)
        layout.addRow(btn)

        self.on_auth_changed(self.auth_combo.currentIndex())

    def _toggle_pwd_visible(self, checked):
        self.input_pwd.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        self.btn_eye.setText("🙈" if checked else "👁")

    def _pick_color(self):
        initial = QColor(self.color_value) if self.color_value else QColor()
        c = QColorDialog.getColor(initial, self, "选择标签颜色")
        if c.isValid():
            self.color_value = c.name()
            self._update_color_button()

    def _update_color_button(self):
        if self.color_value:
            self.btn_color.setStyleSheet(
                f"background-color: {self.color_value}; color: #fff; "
                f"border: none; border-radius: 6px;")
            self.btn_color.setText(self.color_value)
        else:
            self.btn_color.setStyleSheet("")
            self.btn_color.setText("默认")

    def _on_pwd_edited(self, _text):
        self._pwd_touched = True

    def on_auth_changed(self, index):
        if index == 0:
            self.key_widget.hide()
            self._pwd_label.setText("密码:")
            self.input_pwd.setPlaceholderText("请输入登录密码")
        else:
            self.key_widget.show()
            self._pwd_label.setText("私钥密码:")
            self.input_pwd.setPlaceholderText("可选，私钥无密码则留空")

    def browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择私钥文件", "", "All Files (*)")
        if path:
            self.input_key.setText(path)

    def save(self):
        host = self.input_host.text().strip()
        port_str = self.input_port.text().strip()
        user = self.input_user.text().strip()

        if not host or not user:
            QMessageBox.warning(self, "提示", "IP和用户名不能为空！")
            return
        try:
            port = int(port_str)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "提示", "端口号必须是 1-65535 之间的整数！")
            return

        auth_type = "password" if self.auth_combo.currentIndex() == 0 else "key"
        key_path = self.input_key.text().strip()

        if auth_type == "key":
            if not key_path or not os.path.exists(key_path):
                QMessageBox.warning(self, "提示", "请选择有效的私钥文件！")
                return

        old_host = self.server_data.get("host")
        old_user = self.server_data.get("user")

        self.server_data = {
            "name": self.input_name.text().strip() or host,
            "group": self.input_group.text().strip(),
            "host": host, "port": port, "user": user,
            "auth_type": auth_type,
            "key_path": key_path if auth_type == "key" else "",
            "color": self.color_value,
            "icon": self.input_icon.text().strip(),
            "pinned": bool(self.server_data.get("pinned", False)),
            "env": self.env_combo.currentData() or ENV_DEVELOPMENT,
        }

        # ★ 新增：保存跳板机
        jump_val = self.jump_combo.currentData() or ""
        if jump_val and "@" in jump_val:
            jh, ju = jump_val.split("@", 1)
            jump_port = 22
            jump_auth = "password"
            jump_key = ""
            for s in config_manager.load_servers():
                if s.get('host') == jh and s.get('user') == ju:
                    jump_port = int(s.get('port', 22))
                    jump_auth = s.get('auth_type', 'password')
                    jump_key = s.get('key_path', '')
                    break
            self.server_data["jump_host"] = jh
            self.server_data["jump_user"] = ju
            self.server_data["jump_port"] = jump_port
            self.server_data["jump_auth_type"] = jump_auth
            self.server_data["jump_key_path"] = jump_key
        else:
            self.server_data["jump_host"] = ""
            self.server_data["jump_user"] = ""
            self.server_data["jump_port"] = 22
            self.server_data["jump_auth_type"] = "password"
            self.server_data["jump_key_path"] = ""

        pwd = self.input_pwd.text()
        if self._pwd_touched:
            if pwd:
                if not config_manager.save_password(host, user, pwd):
                    QMessageBox.warning(
                        self, "警告",
                        "密码保存到系统凭据失败！重启后需要重新输入。\n"
                        "请检查系统是否安装并启动了 keyring 后端。")
                if old_host and old_user and (old_host != host or old_user != user):
                    config_manager.delete_password(old_host, old_user)
            else:
                config_manager.delete_password(host, user)
                if old_host and old_user and (old_host != host or old_user != user):
                    config_manager.delete_password(old_host, old_user)
        elif old_host and old_user and (old_host != host or old_user != user):
            if self._original_pwd:
                config_manager.save_password(host, user, self._original_pwd)
            config_manager.delete_password(old_host, old_user)

        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 860)

        icon_path = get_resource_path(os.path.join("tubiao", "tubiao.ico"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.setStyleSheet(get_stylesheet())
        self.init_ui()
        self._install_shortcuts()
        self.load_server_list()
        self._restore_state()
        self._refresh_bcast_targets()

    def init_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top_bar.setFixedHeight(58)
        tb = QHBoxLayout(top_bar)
        tb.setContentsMargins(20, 0, 20, 0)
        tb.setSpacing(12)

        logo = QLabel("🖥️")
        logo.setStyleSheet("font-size: 22px;")
        brand_box = QVBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brandTitle")
        sub = QLabel(f"v{APP_VERSION} · 远程开发工作站")
        sub.setObjectName("brandSub")
        brand_box.addWidget(brand)
        brand_box.addWidget(sub)

        tb.addWidget(logo)
        tb.addLayout(brand_box)
        tb.addStretch()

        btn_add = QPushButton("+ 新建连接")
        btn_add.setObjectName("primaryBtn")
        btn_add.clicked.connect(self.add_server)

        btn_ops = QPushButton("操作 ▾")
        ops_menu = QMenu(self)

        local_menu = ops_menu.addMenu("💻 本地终端")
        for label, key in (("自动", "auto"), ("PowerShell", "powershell"),
                           ("CMD", "cmd"), ("WSL", "wsl"),
                           ("Git Bash", "gitbash")):
            local_menu.addAction(label).triggered.connect(
                lambda _=False, k=key: self.open_local_terminal(k))

        ops_menu.addAction("📥 导入 ~/.ssh/config").triggered.connect(self.import_ssh_config)
        ops_menu.addSeparator()
        ops_menu.addAction("⚙️ 首选项").triggered.connect(self.open_preferences)
        ops_menu.addAction("ℹ️ 关于").triggered.connect(self.open_about)
        btn_ops.setMenu(ops_menu)

        tb.addWidget(btn_add)
        tb.addWidget(btn_ops)
        root.addWidget(top_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(14, 14, 14, 14)
        body_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        left_card = QFrame()
        left_card.setObjectName("card")
        ll = QVBoxLayout(left_card)
        ll.setContentsMargins(12, 14, 12, 12)
        ll.setSpacing(10)

        left_title = QLabel("服务器")
        left_title.setObjectName("sectionLabel")
        ll.addWidget(left_title)

        self.server_search = QLineEdit()
        self.server_search.setPlaceholderText("搜索名称 / IP / 用户…")
        self.server_search.setClearButtonEnabled(True)
        self.server_search.textChanged.connect(self._filter_server_tree)
        ll.addWidget(self.server_search)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "状态"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(14)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setFixedHeight(28)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_menu)
        self.tree.itemDoubleClicked.connect(self.connect_selected)
        ll.addWidget(self.tree, 1)

        self.stat_label = QLabel("")
        self.stat_label.setObjectName("statLabel")
        ll.addWidget(self.stat_label)

        self.splitter.addWidget(left_card)

        mid_card = QFrame()
        mid_card.setObjectName("card")
        ml = QVBoxLayout(mid_card)
        ml.setContentsMargins(6, 6, 6, 6)
        ml.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)
        ml.addWidget(self.tabs)
        self.splitter.addWidget(mid_card)

        right_card = QFrame()
        right_card.setObjectName("card")
        rl = QVBoxLayout(right_card)
        rl.setContentsMargins(14, 14, 14, 14)
        rl.setSpacing(12)

        quick_title = QLabel("快捷指令")
        quick_title.setObjectName("sectionLabel")
        rl.addWidget(quick_title)

        hint_label = QLabel("点击按钮向当前激活终端发送命令")
        hint_label.setObjectName("hintLabel")
        hint_label.setWordWrap(True)
        rl.addWidget(hint_label)

        grid = QGridLayout()
        grid.setSpacing(8)
        quick_cmds = [
            ("📊 系统信息", "uname -a && cat /etc/os-release"),
            ("📁 当前目录", "ls -al"),
            ("🧠 内存信息", "free -h"),
            ("💾 磁盘空间", "df -h"),
            ("⚡ 进程排行", "ps aux --sort=-%cpu | head -15"),
            ("🌐 网络连接", "ss -tunap 2>/dev/null | head -20"),
        ]
        for i, (text, cmd) in enumerate(quick_cmds):
            btn = QPushButton(text)
            btn.setMinimumHeight(40)
            btn.clicked.connect(lambda _, c=cmd: self.exec_quick_cmd(c))
            grid.addWidget(btn, i // 2, i % 2)
        rl.addLayout(grid)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #E4E6EE; max-height: 1px;")
        rl.addWidget(sep)

        bcast_title = QLabel("广播输入")
        bcast_title.setObjectName("sectionLabel")
        rl.addWidget(bcast_title)

        bcast_hint = QLabel("勾选目标终端后发送")
        bcast_hint.setObjectName("hintLabel")
        rl.addWidget(bcast_hint)

        self.bcast_targets = QListWidget()
        self.bcast_targets.setMaximumHeight(150)
        rl.addWidget(self.bcast_targets)

        self.bcast_input = QLineEdit()
        self.bcast_input.setPlaceholderText("输入命令，回车广播")
        self.bcast_input.returnPressed.connect(self.broadcast_command)
        rl.addWidget(self.bcast_input)

        btn_bcast = QPushButton("📡 广播执行")
        btn_bcast.setObjectName("primaryBtn")
        btn_bcast.setMinimumHeight(38)
        btn_bcast.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_bcast.clicked.connect(self.broadcast_command)
        rl.addWidget(btn_bcast)

        rl.addStretch()
        self.splitter.addWidget(right_card)

        self.splitter.setSizes([280, 820, 300])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        body_layout.addWidget(self.splitter)
        root.addWidget(body, 1)
        self.setCentralWidget(central)

        self.show_welcome_tab()

    def _install_shortcuts(self):
        def add(seq, func):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(func)
            return sc

        add("Ctrl+T", self.add_server)
        add("Ctrl+W", self._close_current_tab)
        add("Ctrl+Tab", lambda: self._cycle_tab(1))
        add("Ctrl+Shift+Tab", lambda: self._cycle_tab(-1))
        add("Ctrl+,", self.open_preferences)
        for i in range(1, 10):
            add(f"Ctrl+{i}", lambda idx=i - 1: self._goto_tab(idx))

    def _close_current_tab(self):
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.close_tab(idx)

    def _cycle_tab(self, delta):
        n = self.tabs.count()
        if n <= 1:
            return
        cur = self.tabs.currentIndex()
        self.tabs.setCurrentIndex((cur + delta) % n)

    def _goto_tab(self, idx):
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    def open_preferences(self):
        dlg = PreferencesDialog(self)
        dlg.exec()

    def open_about(self):
        dlg = AboutDialog(self)
        dlg.exec()

    def _filter_server_tree(self, text):
        text = (text or "").strip().lower()
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if d is not None:
                name = (d.get('name') or '').lower()
                host = (d.get('host') or '').lower()
                user = (d.get('user') or '').lower()
                match = (not text) or (text in name or text in host or text in user)
                item.setHidden(not match)
            it += 1
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.data(0, Qt.ItemDataRole.UserRole) is None and top.childCount() > 0:
                any_visible = any(
                    not top.child(j).isHidden() for j in range(top.childCount()))
                top.setHidden(not any_visible)
                if text and any_visible:
                    top.setExpanded(True)

    def _rename_tab(self, index):
        if index < 0:
            return
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return
        new_name, ok = QInputDialog.getText(
            self, "重命名标签", "新名称:", text=w.title)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            return
        w.title = new_name
        self._apply_tab_style(index)

    def _apply_tab_style(self, index):
        tabbar = self.tabs.tabBar()
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return
        base = w.title or ""
        text = f"📌 {base}" if w.pinned else base
        tabbar.setTabText(index, text)

        if w.color:
            tabbar.setTabTextColor(index, QColor(w.color))
        else:
            env_meta = ENV_META.get(getattr(w, "env", ""), {})
            if env_meta.get("color"):
                tabbar.setTabTextColor(index, QColor(env_meta["color"]))
            else:
                tabbar.setTabTextColor(
                    index,
                    self.palette().color(self.foregroundRole()))

        if w.icon_emoji:
            tabbar.setTabIcon(index, QIcon(_emoji_pixmap(w.icon_emoji, 16)))
        else:
            tabbar.setTabIcon(index, QIcon())

    def _set_tab_color(self, index, color):
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return
        w.color = color or ""
        self._apply_tab_style(index)
        self._persist_tab_meta(w)

    def _set_tab_icon(self, index, emoji):
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return
        w.icon_emoji = emoji or ""
        self._apply_tab_style(index)
        self._persist_tab_meta(w)

    def _toggle_pin(self, index, pinned):
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return
        w.pinned = bool(pinned)
        self._apply_tab_style(index)
        self._persist_tab_meta(w)

    def _persist_tab_meta(self, w):
        if w.conn_type != "ssh" or not w.server_info:
            return
        host = w.server_info.get("host")
        user = w.server_info.get("user")
        if not host or not user:
            return
        servers = config_manager.load_servers()
        changed = False
        for s in servers:
            if s.get("host") == host and s.get("user") == user:
                s["color"] = w.color or ""
                s["icon"] = w.icon_emoji or ""
                s["pinned"] = bool(w.pinned)
                changed = True
                break
        if changed:
            config_manager.save_servers(servers)

    def _on_tab_context_menu(self, pos):
        tabbar = self.tabs.tabBar()
        index = tabbar.tabAt(pos)
        if index < 0:
            return
        w = self.tabs.widget(index)
        if not isinstance(w, TerminalTab):
            return

        menu = QMenu(self)
        act_rename = menu.addAction("✏️ 重命名")
        act_rename.triggered.connect(lambda: self._rename_tab(index))

        color_menu = menu.addMenu("🎨 标签颜色")
        presets = [("默认", ""), ("红", "#EF4444"), ("橙", "#F59E0B"),
                   ("绿", "#10B981"), ("蓝", "#3B82F6"), ("紫", "#8B5CF6"),
                   ("粉", "#EC4899"), ("青", "#06B6D4")]
        for name, color in presets:
            a = color_menu.addAction(name)
            a.triggered.connect(
                lambda _=False, c=color, i=index: self._set_tab_color(i, c))

        icon_menu = menu.addMenu("😀 标签图标")
        for ic in ["🖥", "🐧", "🪟", "🍎", "⚡", "🔥", "🧪", "🔒", "🌐", "☁", "🗄"]:
            a = icon_menu.addAction(ic)
            a.triggered.connect(
                lambda _=False, e=ic, i=index: self._set_tab_icon(i, e))
        a = icon_menu.addAction("清除图标")
        a.triggered.connect(
            lambda _=False, i=index: self._set_tab_icon(i, ""))

        if w.pinned:
            act_pin = menu.addAction("📌 取消置顶")
            act_pin.triggered.connect(lambda: self._toggle_pin(index, False))
        else:
            act_pin = menu.addAction("📌 置顶（关闭时需确认）")
            act_pin.triggered.connect(lambda: self._toggle_pin(index, True))

        if not w.pinned:
            menu.addSeparator()
            act_close = menu.addAction("关闭标签")
            act_close.triggered.connect(lambda: self.close_tab(index))

        menu.exec(tabbar.mapToGlobal(pos))

    def _refresh_bcast_targets(self, *_):
        if getattr(self, 'bcast_targets', None) is None:
            return
        checked = {}
        for i in range(self.bcast_targets.count()):
            item = self.bcast_targets.item(i)
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                checked[key] = item.checkState()

        self.bcast_targets.clear()
        n = 0
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TerminalTab) and w.conn_type == "ssh" and w.server_info:
                key = f"{w.server_info.get('user')}@{w.server_info.get('host')}"
                label = key
                if not w._connected:
                    label += "（未连接）"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(checked.get(key, Qt.CheckState.Checked))
                self.bcast_targets.addItem(item)
                n += 1
        if n == 0:
            item = QListWidgetItem("(无 SSH 终端)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.bcast_targets.addItem(item)

    def broadcast_command(self):
        cmd = self.bcast_input.text().strip()
        if not cmd:
            return

        if is_dangerous_command(cmd):
            reply = QMessageBox.warning(
                self, "⚠️ 危险命令",
                f"检测到潜在危险命令，将广播到多个终端：\n\n{cmd}\n\n确定继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return

        wanted = set()
        for i in range(self.bcast_targets.count()):
            item = self.bcast_targets.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            key = item.data(Qt.ItemDataRole.UserRole)
            if key:
                wanted.add(key)
        if not wanted:
            QMessageBox.information(self, "提示", "请先勾选目标终端")
            return

        targets = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if not isinstance(w, TerminalTab) or w.conn_type != "ssh" or not w.server_info:
                continue
            key = f"{w.server_info.get('user')}@{w.server_info.get('host')}"
            if key in wanted and w.send_data_raw(cmd + "\r"):
                targets.append(w.title)

        if not targets:
            QMessageBox.information(self, "提示", "勾选的终端中没有已连接的可发送目标")
            return
        self.bcast_input.clear()

    def _restore_state(self):
        s = _qs()
        geo = s.value("window/geometry")
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        sp = s.value("window/splitter")
        if sp is not None:
            try:
                self.splitter.restoreState(sp)
            except Exception:
                pass

    def _save_state(self):
        s = _qs()
        s.setValue("window/geometry", self.saveGeometry())
        if getattr(self, "splitter", None) is not None:
            s.setValue("window/splitter", self.splitter.saveState())

    def exec_quick_cmd(self, cmd):
        current_tab = self.tabs.currentWidget()
        if not isinstance(current_tab, TerminalTab):
            QMessageBox.information(self, "提示", "请先打开并连接一个终端")
            return
        if not current_tab.worker:
            QMessageBox.warning(self, "提示", "当前终端未连接")
            return
        if not getattr(current_tab, "_connected", False):
            QMessageBox.warning(self, "提示", "终端尚未就绪，请稍候片刻再试")
            return
        current_tab.cmd_input.setText(cmd)
        current_tab.send_cmd_from_input()

    def show_welcome_tab(self):
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.setSpacing(14)
        icon = QLabel("🖥️")
        icon.setStyleSheet("font-size: 72px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel(APP_NAME)
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("请在左侧双击服务器开始连接\n或点击顶部【操作 → 本地终端】")
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wl.addWidget(icon)
        wl.addWidget(title)
        wl.addWidget(hint)

        idx = self.tabs.addTab(w, "欢迎")
        self.tabs.tabBar().setTabButton(
            idx, QTabBar.ButtonPosition.RightSide, None)
        self._refresh_bcast_targets()

    def remove_welcome_tab_if_exists(self):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == "欢迎":
                self.tabs.removeTab(i)
                break

    def _find_open_tab(self, host, user):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TerminalTab) and w.conn_type == "ssh" and w.server_info:
                if (w.server_info.get('host') == host
                        and w.server_info.get('user') == user):
                    return i
        return -1

    def load_server_list(self):
        self.tree.clear()
        servers = config_manager.load_servers()
        self.stat_label.setText(f"共 {len(servers)} 台服务器")

        if not servers:
            item = QTreeWidgetItem(["(暂无服务器，点击右上角新建)", ""])
            item.setForeground(0, QBrush(QColor(TEXT_MUTED)))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree.addTopLevelItem(item)
            if self.server_search.text():
                self._filter_server_tree(self.server_search.text())
            return

        groups = {}
        for srv in servers:
            g = (srv.get('group') or '').strip()
            groups.setdefault(g, []).append(srv)

        for g in sorted(groups.keys(), key=lambda x: (x != '', x.lower())):
            items = sorted(groups[g], key=lambda s: s.get('name', '').lower())
            if not g:
                for srv in items:
                    self._add_server_item(self.tree, srv)
            else:
                group_item = QTreeWidgetItem([f"📁 {g}", ""])
                group_item.setForeground(0, QBrush(QColor(TEXT_MUTED)))
                group_item.setData(0, Qt.ItemDataRole.UserRole, None)
                for srv in items:
                    self._add_server_item(group_item, srv)
                group_item.setExpanded(True)
                self.tree.addTopLevelItem(group_item)

        if self.server_search.text():
            self._filter_server_tree(self.server_search.text())

    def _add_server_item(self, parent, srv):
        display_name = srv['name']
        if srv.get('icon'):
            display_name = f"{srv['icon']} {display_name}"
        item = QTreeWidgetItem([display_name, "未连接"])
        item.setData(0, Qt.ItemDataRole.UserRole, srv)
        item.setForeground(1, QBrush(QColor(TEXT_MUTED)))
        if srv.get('color'):
            item.setForeground(0, QBrush(QColor(srv['color'])))
        elif srv.get('env') in ENV_META:
            env_meta = ENV_META[srv['env']]
            if env_meta.get("color") and srv['env'] in (ENV_PRODUCTION, ENV_STAGING):
                item.setForeground(0, QBrush(QColor(env_meta["color"])))
        if srv.get('env') in (ENV_PRODUCTION, ENV_STAGING):
            item.setText(1, ENV_META[srv['env']]["label"])
        item.setToolTip(0, f"{srv.get('user')}@{srv.get('host')}:{srv.get('port', 22)}")
        if isinstance(parent, QTreeWidget):
            parent.addTopLevelItem(item)
        else:
            parent.addChild(item)

    def _update_server_status(self, srv, status):
        if not srv:
            return
        color_map = {
            "已连接": SUCCESS, "正在连接...": WARNING,
            "未连接": TEXT_MUTED, "已断开": DANGER,
        }
        color = color_map.get(status, TEXT_MUTED)
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            d = item.data(0, Qt.ItemDataRole.UserRole)
            if (d and d.get('host') == srv.get('host')
                    and d.get('user') == srv.get('user')):
                item.setText(1, status)
                item.setForeground(1, QBrush(QColor(color)))
                return
            it += 1

    def add_server(self):
        dlg = ServerDialog(self)
        if not dlg.exec():
            return
        servers = config_manager.load_servers()
        replaced = False
        for i, s in enumerate(servers):
            if (s['host'] == dlg.server_data['host']
                    and s['user'] == dlg.server_data['user']):
                servers[i] = dlg.server_data
                replaced = True
                break
        if not replaced:
            servers.append(dlg.server_data)
        if not config_manager.save_servers(servers):
            QMessageBox.warning(self, "错误", "配置保存失败，请检查磁盘权限。")
            return
        self.load_server_list()

    def show_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        if item.data(0, Qt.ItemDataRole.UserRole) is None:
            return
        menu = QMenu()
        menu.addAction("编辑").triggered.connect(lambda: self.edit_server(item))
        menu.addAction("删除").triggered.connect(lambda: self.delete_server(item))
        menu.exec(self.tree.mapToGlobal(pos))

    def edit_server(self, item):
        srv = item.data(0, Qt.ItemDataRole.UserRole)
        dlg = ServerDialog(self, srv)
        if not dlg.exec():
            return
        servers = config_manager.load_servers()
        new_data = dlg.server_data

        if new_data['host'] != srv['host'] or new_data['user'] != srv['user']:
            for s in servers:
                if (s['host'] == new_data['host']
                        and s['user'] == new_data['user']
                        and not (s['host'] == srv['host']
                                 and s['user'] == srv['user'])):
                    QMessageBox.warning(self, "冲突",
                        f"已存在相同 {new_data['user']}@{new_data['host']} 的配置")
                    return

        for i, s in enumerate(servers):
            if s['host'] == srv['host'] and s['user'] == srv['user']:
                servers[i] = new_data
                break
        if not config_manager.save_servers(servers):
            QMessageBox.warning(self, "错误", "配置保存失败。")
            return
        self.load_server_list()

    def delete_server(self, item):
        srv = item.data(0, Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self, '确认删除',
            f"确定要删除服务器 [{srv['name']}] 吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            servers = [s for s in config_manager.load_servers()
                       if not (s['host'] == srv['host'] and s['user'] == srv['user'])]
            config_manager.save_servers(servers)
            self.load_server_list()

    def import_ssh_config(self):
        path = default_ssh_config_path()
        if not os.path.exists(path):
            QMessageBox.information(self, "提示", f"未找到 {path}")
            return
        try:
            new_servers = parse_ssh_config(path)
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return
        if not new_servers:
            QMessageBox.information(self, "提示", "未在 ssh config 中解析到任何主机")
            return

        existing = config_manager.load_servers()
        existing_keys = {(s['host'], s['user']) for s in existing}
        added = skipped = 0
        for srv in new_servers:
            key = (srv['host'], srv['user'])
            if key in existing_keys:
                skipped += 1
                continue
            existing.append(srv)
            existing_keys.add(key)
            added += 1

        if added == 0:
            QMessageBox.information(
                self, "提示", f"所有 {skipped} 个主机已存在，无新增。")
            return
        if not config_manager.save_servers(existing):
            QMessageBox.warning(self, "错误", "配置保存失败。")
            return
        self.load_server_list()
        QMessageBox.information(
            self, "完成", f"已导入 {added} 个，跳过 {skipped} 个已存在的。")

    def connect_selected(self, item):
        srv = item.data(0, Qt.ItemDataRole.UserRole)
        if not srv:
            return
        idx = self._find_open_tab(srv['host'], srv['user'])
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)
            return

        auth_type = srv.get("auth_type", "password")
        conn_info = dict(srv)
        pwd = config_manager.get_password(srv['host'], srv['user'])

        if auth_type == "password":
            if not pwd:
                text, ok = QInputDialog.getText(
                    self, "输入密码",
                    f"请输入 {srv['user']}@{srv['host']} 的登录密码：",
                    QLineEdit.EchoMode.Password)
                if not ok or not text:
                    return
                pwd = text
                if not config_manager.save_password(srv['host'], srv['user'], pwd):
                    QMessageBox.warning(
                        self, "警告",
                        "密码未能保存到系统凭据，本次会话有效。\n"
                        "下次连接需要重新输入。")
            conn_info['pwd'] = pwd
        else:
            key_path = srv.get("key_path", "")
            if not key_path or not os.path.exists(key_path):
                QMessageBox.warning(self, "错误", f"私钥文件不存在:\n{key_path}")
                return
            conn_info['key_path'] = key_path
            conn_info['pwd'] = pwd

        # ★ 新增：组装跳板机配置
        jump_host = srv.get('jump_host', '') or ''
        jump_user = srv.get('jump_user', '') or ''
        if jump_host and jump_user:
            jump_port = int(srv.get('jump_port', 22) or 22)
            jump_auth = srv.get('jump_auth_type', 'password') or 'password'
            jump_key = srv.get('jump_key_path', '') or ''
            jump_pwd = config_manager.get_password(jump_host, jump_user)
            if jump_auth == 'password' and not jump_pwd:
                text, ok = QInputDialog.getText(
                    self, "跳板机密码",
                    f"请输入跳板机 {jump_user}@{jump_host} 的密码：",
                    QLineEdit.EchoMode.Password)
                if not ok or not text:
                    return
                jump_pwd = text
                config_manager.save_password(jump_host, jump_user, jump_pwd)
            conn_info['jump'] = {
                'host': jump_host, 'port': jump_port, 'user': jump_user,
                'auth_type': jump_auth, 'key_path': jump_key, 'pwd': jump_pwd,
            }

        term_tab = TerminalTab(srv['name'], conn_type="ssh", server_info=conn_info)
        term_tab.color = srv.get('color', '') or ''
        term_tab.icon_emoji = srv.get('icon', '') or ''
        term_tab.pinned = bool(srv.get('pinned', False))
        term_tab.env = srv.get('env', '') or ''
        term_tab.status_changed.connect(
            lambda s, sv=srv: self._update_server_status(sv, s))
        term_tab.status_changed.connect(lambda _s: self._refresh_bcast_targets())
        self.remove_welcome_tab_if_exists()
        idx = self.tabs.addTab(term_tab, srv['name'])
        self._apply_tab_style(idx)
        self.tabs.setTabToolTip(
            idx, f"{srv.get('user')}@{srv.get('host')}:{srv.get('port', 22)}")
        self.tabs.setCurrentIndex(idx)
        self._update_server_status(srv, "正在连接...")
        self._refresh_bcast_targets()

    def open_local_terminal(self, shell="auto"):
        base_label = {"powershell": "💻 PowerShell", "wsl": "💻 WSL",
                      "gitbash": "💻 Git Bash", "cmd": "💻 CMD"}.get(
            shell, "💻 本地终端")

        existing = {self.tabs.tabText(i) for i in range(self.tabs.count())}
        if base_label not in existing:
            label = base_label
        else:
            n = 2
            while f"{base_label} ({n})" in existing:
                n += 1
            label = f"{base_label} ({n})"

        term_tab = TerminalTab("本地终端", conn_type="local", local_shell=shell)
        self.remove_welcome_tab_if_exists()
        idx = self.tabs.addTab(term_tab, label)
        self.tabs.setCurrentIndex(idx)
        self._refresh_bcast_targets()

    def close_tab(self, index):
        widget = self.tabs.widget(index)
        if isinstance(widget, TerminalTab):
            if widget.pinned:
                reply = QMessageBox.question(
                    self, "已置顶",
                    f"标签 [{widget.title}] 已置顶，确定关闭吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
            s = _qs()
            if (widget.conn_type == "ssh"
                    and hasattr(widget, 'sftp_panel')
                    and widget.sftp_panel.has_active_transfer()
                    and s.value("ui/confirm_active_transfer", True, type=bool)):
                reply = QMessageBox.question(
                    self, "传输进行中",
                    "文件传输尚未完成，确定要关闭吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if reply != QMessageBox.StandardButton.Yes:
                    return
            widget.close_connection()
            if widget.conn_type == "ssh" and widget.server_info:
                self._update_server_status(widget.server_info, "未连接")
        self.tabs.removeTab(index)
        if widget:
            widget.deleteLater()
        if self.tabs.count() == 0:
            self.show_welcome_tab()
        else:
            self._refresh_bcast_targets()

    def closeEvent(self, event):
        active = 0
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TerminalTab) and w.worker:
                active += 1
        if active > 0:
            reply = QMessageBox.question(
                self, "确认退出",
                f"还有 {active} 个活动连接，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._save_state()
        try:
            _qs().sync()
        except Exception:
            pass

        workers = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, TerminalTab):
                w.close_connection()
                if w.worker is not None:
                    workers.append(w.worker)

        try:
            for w in list(_terminal_tab._PENDING_WORKERS):
                if w not in workers:
                    workers.append(w)
        except Exception:
            pass

        for wk in workers:
            try:
                if wk.isRunning():
                    if not wk.wait(300):
                        _LINGERING_WORKERS.append(wk)
            except Exception:
                pass
        event.accept()