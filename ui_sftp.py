import os
import stat as _stat
import datetime
import string
from collections import OrderedDict

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QFileDialog,
                             QProgressBar, QLabel, QMessageBox, QLineEdit,
                             QDialog, QMenu, QCheckBox, QInputDialog,
                             QCompleter, QComboBox, QSplitter)
from PyQt6.QtCore import Qt, QUrl, QStringListModel, QThread, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QAction, QDesktopServices

from workers import SFTPWorker
from constants import MAX_EDIT_FILE_SIZE, SFTP_PATH_CACHE_MAX
from theme import TEXT_SECONDARY, TEXT_MUTED, PRIMARY, BG_SURFACE, BORDER
from code_highlighter import CodeEditor


_ORPHANED_DIALOG_WORKERS = []


def _safe_remove_orphan(w):
    try:
        _ORPHANED_DIALOG_WORKERS.remove(w)
    except ValueError:
        pass
    try:
        w.deleteLater()
    except RuntimeError:
        pass


class SFTPEditorDialog(QDialog):
    def __init__(self, client, remote_path, filename, parent=None):
        super().__init__(parent)
        self.client = client
        self.remote_path = remote_path
        self.is_modified = False
        self.read_worker = None
        self.write_worker = None
        self._closing = False

        self.setWindowTitle(f"在线编辑 - {filename}")
        self.resize(920, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.status_bar = QLabel("正在读取文件...")
        self.status_bar.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self.status_bar)

        self.editor = CodeEditor(filename)
        self.editor.setReadOnly(True)
        layout.addWidget(self.editor)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_save = QPushButton("保存 (Ctrl+S)")
        self.btn_save.setObjectName("primaryBtn")
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_file)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        self.editor.document().modificationChanged.connect(self._on_modified)

        self.read_worker = SFTPWorker(self.client, "read", remote=remote_path)
        self.read_worker.content_ready.connect(self.on_content_ready)
        self.read_worker.done.connect(self._on_read_done)
        self.read_worker.finished.connect(self.read_worker.deleteLater)
        self.read_worker.start()

    def _on_read_done(self, _msg):
        pass

    def on_content_ready(self, content):
        if self._closing:
            return
        if content.startswith("[读取失败]"):
            self.status_bar.setText(content)
            self.status_bar.setStyleSheet("color: #EF4444;")
            return
        try:
            self.editor.setPlainText(content)
        except Exception as e:
            self.status_bar.setText(f"渲染失败: {e}")
            self.status_bar.setStyleSheet("color: #EF4444;")
            return
        self.editor.document().setModified(False)
        self.editor.setReadOnly(False)
        self.btn_save.setEnabled(True)
        self.status_bar.setText(
            f"已加载: {self.remote_path} | 大小: {len(content)} 字符")
        self.status_bar.setStyleSheet(f"color: {TEXT_SECONDARY};")

    def save_file(self):
        if self._closing or not self.btn_save.isEnabled():
            return
        if self.write_worker is not None and self.write_worker.isRunning():
            return
        content = self.editor.toPlainText()
        self.status_bar.setText("正在保存到服务器...")
        self.status_bar.setStyleSheet("color: #F59E0B;")
        self.btn_save.setEnabled(False)
        self.write_worker = SFTPWorker(
            self.client, "write",
            remote=self.remote_path,
            content=content.encode('utf-8'))
        self.write_worker.write_finished.connect(self.on_save_finished)
        self.write_worker.done.connect(self._on_write_done)
        self.write_worker.finished.connect(self.write_worker.deleteLater)
        self.write_worker.start()

    def _on_write_done(self, _msg):
        pass

    def on_save_finished(self, success, msg):
        if self._closing:
            return
        self.btn_save.setEnabled(True)
        if success:
            self.is_modified = False
            self.editor.document().setModified(False)
            self.status_bar.setText(f"✅ 保存成功! ({msg})")
            self.status_bar.setStyleSheet("color: #10B981;")
        else:
            self.status_bar.setText(f"❌ {msg}")
            self.status_bar.setStyleSheet("color: #EF4444;")
            QMessageBox.critical(self, "保存失败", msg)

    def _on_modified(self, modified):
        self.is_modified = modified
        base = f"在线编辑 - {os.path.basename(self.remote_path)}"
        self.setWindowTitle(f"{base} *" if modified else base)

    def keyPressEvent(self, event):
        if (event.modifiers() == Qt.KeyboardModifier.ControlModifier
                and event.key() == Qt.Key.Key_S):
            if self.btn_save.isEnabled():
                self.save_file()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.is_modified:
            reply = QMessageBox.question(
                self, '未保存',
                '文件已修改但未保存，确定关闭吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True
        for attr in ('read_worker', 'write_worker'):
            w = getattr(self, attr, None)
            if w is None:
                continue
            try:
                if w.isRunning():
                    if not w.wait(1500):
                        _ORPHANED_DIALOG_WORKERS.append(w)
                        try:
                            w.finished.connect(
                                lambda ww=w: _safe_remove_orphan(ww))
                        except Exception:
                            pass
                        continue
            except Exception:
                pass
            setattr(self, attr, None)
        event.accept()


def _human_size(n):
    for u in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f"{n:.0f} {u}" if u == 'B' else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class LocalListWorker(QThread):
    file_list_ready = pyqtSignal(list)

    def __init__(self, path, include_hidden=True):
        super().__init__()
        self.path = path
        self.include_hidden = include_hidden

    def run(self):
        files = []
        try:
            with os.scandir(self.path) as it:
                for entry in it:
                    if not self.include_hidden and entry.name.startswith('.'):
                        continue
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    files.append({
                        "name": entry.name,
                        "size": st.st_size,
                        "is_dir": entry.is_dir(follow_symlinks=False),
                        "mode": st.st_mode,
                        "mtime": st.st_mtime,
                    })
        except OSError:
            pass
        self.file_list_ready.emit(files)


class SFTPPanel(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.current_path = "."
        self.current_worker = None
        self._list_worker = None
        self._list_seq = 0

        self._transfer_queue = []
        self._transfer_worker = None
        self._transfer_current = None
        self._bg_workers = []

        self._known_paths = OrderedDict()

        self._local_dir = os.path.expanduser("~")
        self._local_worker = None
        self._local_seq = 0

        self.init_ui()
        self.load_local_dir(self._local_dir)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(6)

        local_top = QHBoxLayout()
        local_top.setSpacing(6)
        self.drive_combo = QComboBox()
        self.drive_combo.setMinimumWidth(110)
        self._populate_drives()
        self.drive_combo.activated.connect(self._on_drive_selected)

        self.local_path_input = QLineEdit(self._local_dir)
        self.local_path_input.setPlaceholderText("本地路径")
        self.local_path_input.returnPressed.connect(self.on_local_path_entered)

        btn_local_refresh = QPushButton("刷新")
        btn_local_refresh.clicked.connect(lambda: self.load_local_dir(self._local_dir))
        btn_local_up = QPushButton("上级")
        btn_local_up.clicked.connect(self.local_go_up)

        local_top.addWidget(self.drive_combo)
        local_top.addWidget(self.local_path_input, 1)
        local_top.addWidget(btn_local_refresh)
        local_top.addWidget(btn_local_up)
        local_layout.addLayout(local_top)

        self.local_tree = QTreeWidget()
        self.local_tree.setHeaderLabels(["名称", "大小", "修改时间"])
        self.local_tree.setRootIsDecorated(False)
        self.local_tree.header().resizeSection(0, 240)
        self.local_tree.header().resizeSection(1, 80)
        self.local_tree.header().resizeSection(2, 130)
        self.local_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.local_tree.itemDoubleClicked.connect(self._on_local_item_double_click)
        local_layout.addWidget(self.local_tree)
        self.splitter.addWidget(local_widget)

        btn_widget = QWidget()
        btn_widget.setMaximumWidth(100)
        btn_layout = QVBoxLayout(btn_widget)
        btn_layout.setContentsMargins(4, 0, 4, 0)
        btn_layout.setSpacing(8)
        btn_layout.addStretch()

        self.btn_upload_sel = QPushButton("上传 →")
        self.btn_upload_sel.setMinimumHeight(36)
        self.btn_upload_sel.setToolTip("上传左侧选中项到远程当前目录")
        self.btn_upload_sel.clicked.connect(self.upload_selected)

        self.btn_download_sel = QPushButton("← 下载")
        self.btn_download_sel.setMinimumHeight(36)
        self.btn_download_sel.setToolTip("下载右侧选中项到本地当前目录")
        self.btn_download_sel.clicked.connect(self.download_selected_to_local)

        btn_layout.addWidget(self.btn_upload_sel)
        btn_layout.addWidget(self.btn_download_sel)
        btn_layout.addStretch()
        self.splitter.addWidget(btn_widget)

        remote_widget = QWidget()
        remote_layout = QVBoxLayout(remote_widget)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_layout.setSpacing(6)

        remote_top = QHBoxLayout()
        remote_top.setSpacing(6)

        self.path_input = QLineEdit(".")
        self.path_input.setPlaceholderText("远程路径，回车跳转（Tab 补全）")
        self.path_input.returnPressed.connect(self.on_path_entered)

        self._path_completer_model = QStringListModel([], self)
        self._path_completer = QCompleter(self._path_completer_model, self)
        self._path_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._path_completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._path_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.path_input.setCompleter(self._path_completer)

        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_up = QPushButton("上级")
        btn_up.clicked.connect(self.go_up)

        self.chk_hidden = QCheckBox("隐藏文件")
        self.chk_hidden.setChecked(False)
        self.chk_hidden.toggled.connect(lambda _: self.refresh())

        remote_top.addWidget(QLabel("远程:"))
        remote_top.addWidget(self.path_input, 1)
        remote_top.addWidget(btn_refresh)
        remote_top.addWidget(btn_up)
        remote_top.addWidget(self.chk_hidden)
        remote_layout.addLayout(remote_top)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["文件名", "大小", "权限", "修改时间", "类型"])
        self.file_tree.setRootIsDecorated(False)
        self.file_tree.header().setStretchLastSection(False)
        self.file_tree.header().resizeSection(0, 260)
        self.file_tree.header().resizeSection(1, 80)
        self.file_tree.header().resizeSection(2, 100)
        self.file_tree.header().resizeSection(3, 130)
        self.file_tree.header().resizeSection(4, 80)
        self.file_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.file_tree.itemDoubleClicked.connect(self.on_item_double_click)
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_tree.customContextMenuRequested.connect(self.show_context_menu)
        remote_layout.addWidget(self.file_tree)

        self.splitter.addWidget(remote_widget)
        self.splitter.setSizes([420, 100, 420])
        layout.addWidget(self.splitter, 1)

        progress_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.queue_label = QLabel("")
        self.queue_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.queue_label)
        layout.addLayout(progress_row)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)
        btn_upload = QPushButton("📁 上传文件…")
        btn_upload.clicked.connect(lambda: self.upload_file())
        btn_upload_dir = QPushButton("📂 上传文件夹…")
        btn_upload_dir.clicked.connect(self.upload_folder)

        self.btn_edit = QPushButton("📝 编辑选中文件")
        self.btn_edit.clicked.connect(self.edit_selected_file)

        btn_mkdir = QPushButton("📁 新建文件夹")
        btn_mkdir.clicked.connect(self.mkdir_remote)

        bottom_bar.addWidget(btn_upload)
        bottom_bar.addWidget(btn_upload_dir)
        bottom_bar.addWidget(self.btn_edit)
        bottom_bar.addWidget(btn_mkdir)
        bottom_bar.addStretch()
        layout.addLayout(bottom_bar)

    def _spawn(self, worker, connected_signals=None):
        self._bg_workers.append(worker)
        if connected_signals:
            for sig_name, slot in connected_signals.items():
                sig = getattr(worker, sig_name, None)
                if sig is not None:
                    sig.connect(slot)
        worker.finished.connect(lambda w=worker: self._release_worker(w))
        worker.start()
        return worker

    def _release_worker(self, worker):
        try:
            self._bg_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _populate_drives(self):
        self.drive_combo.clear()
        home = os.path.expanduser("~")
        self.drive_combo.addItem("🏠 主目录", home)
        for sub in ("Desktop", "桌面", "Downloads", "下载", "Documents", "文档"):
            p = os.path.join(home, sub)
            if os.path.isdir(p):
                self.drive_combo.addItem(f"📂 {sub}", p)
                break
        if os.name == 'nt':
            for letter in string.ascii_uppercase:
                d = f"{letter}:\\"
                if os.path.exists(d):
                    self.drive_combo.addItem(f"💾 {letter}:", d)
        else:
            self.drive_combo.addItem("🖥 根目录", "/")

    def _on_drive_selected(self, index):
        path = self.drive_combo.itemData(index)
        if path:
            self.load_local_dir(path)

    def on_local_path_entered(self):
        p = self.local_path_input.text().strip()
        if p:
            self.load_local_dir(p)

    def load_local_dir(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            # ★ 修复：无效路径不再静默返回，给用户明确反馈
            QMessageBox.warning(self, "提示", f"本地目录不存在:\n{path}")
            return
        self._local_dir = path
        self.local_path_input.setText(path)
        self.local_tree.clear()

        self._local_seq += 1
        seq = self._local_seq
        worker = LocalListWorker(path, include_hidden=True)
        self._local_worker = worker
        worker.file_list_ready.connect(
            lambda files, s=seq: self._on_local_list_ready(s, files))
        self._spawn(worker)

    def _on_local_list_ready(self, seq, files):
        if seq != self._local_seq:
            return
        self.populate_local_tree(files)

    def populate_local_tree(self, files):
        self.local_tree.clear()
        files = sorted(files, key=lambda f: (not f["is_dir"], f["name"].lower()))
        for f in files:
            size_str = _human_size(f["size"]) if not f["is_dir"] else "—"
            try:
                mt = (datetime.datetime.fromtimestamp(f.get("mtime", 0))
                      .strftime("%Y-%m-%d %H:%M")) if f.get("mtime") else "—"
            except Exception:
                mt = "—"
            name = f["name"]
            if f["is_dir"]:
                name = "📁 " + name
            item = QTreeWidgetItem([name, size_str, mt])
            item.setData(0, Qt.ItemDataRole.UserRole, f)
            if f["is_dir"]:
                item.setForeground(0, QBrush(QColor(PRIMARY)))
            self.local_tree.addTopLevelItem(item)

    def local_go_up(self):
        parent = os.path.dirname(self._local_dir.rstrip(os.sep))
        if parent and parent != self._local_dir:
            self.load_local_dir(parent)

    def _on_local_item_double_click(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data["is_dir"]:
            self.load_local_dir(os.path.join(self._local_dir, data["name"]))

    def _local_selected_items(self):
        return [it for it in self.local_tree.selectedItems()
                if it.data(0, Qt.ItemDataRole.UserRole)]

    def upload_selected(self):
        if not self.current_worker or not self.current_worker.client:
            QMessageBox.warning(self, "提示", "SSH 连接未就绪")
            return
        items = self._local_selected_items()
        if not items:
            QMessageBox.information(self, "提示", "请先在左侧选中要上传的文件或文件夹")
            return
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            local_abs = os.path.join(self._local_dir, data["name"])
            remote = self._join(data["name"])
            if data["is_dir"]:
                self._enqueue("put_recursive", local_abs, remote)
            else:
                self._enqueue("put", local_abs, remote)

    def download_selected_to_local(self):
        if not self.current_worker or not self.current_worker.client:
            QMessageBox.warning(self, "提示", "SSH 连接未就绪")
            return
        items = self._selected_items()
        if not items:
            QMessageBox.information(self, "提示", "请先在右侧选中要下载的文件或文件夹")
            return
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            remote = self._join(data["name"])
            local_abs = os.path.join(self._local_dir, data["name"])
            if data["is_dir"]:
                self._enqueue("get_recursive", local_abs, remote)
            else:
                self._enqueue("get", local_abs, remote)

    def _enqueue(self, action, local, remote, on_done=None):
        self._transfer_queue.append({
            'action': action, 'local': local, 'remote': remote,
            'on_done': on_done,
        })
        self._update_queue_label()
        self._pump_queue()

    def _pump_queue(self):
        if self._transfer_worker is not None:
            return
        if not self._transfer_queue:
            self._update_queue_label()
            return
        if not self.current_worker or not self.current_worker.client:
            self._transfer_queue.clear()
            self._update_queue_label()
            return

        item = self._transfer_queue.pop(0)
        self._transfer_current = item
        self.progress.setValue(0)
        w = SFTPWorker(self.current_worker.client, item['action'],
                       local=item['local'], remote=item['remote'])
        w.progress.connect(self.progress.setValue)
        w.progress_detail.connect(self._on_progress_detail)
        w.finished.connect(w.deleteLater)
        w.done.connect(self._on_transfer_done)
        self._transfer_worker = w
        w.start()
        self._update_queue_label()

    def _on_progress_detail(self, pct, detail):
        n = len(self._transfer_queue)
        if self._transfer_worker is not None:
            n += 1
        if detail:
            self.queue_label.setText(f"{detail} · 队列 {n}")

    def _on_transfer_done(self, msg):
        item = self._transfer_current
        if item:
            action = item['action']
            if action in ('put', 'get', 'put_recursive', 'get_recursive') \
                    and msg.startswith("✅"):
                self.progress.setValue(100)
            cb = item.get('on_done')
            if cb:
                try:
                    cb(msg)
                except Exception:
                    pass
        self._transfer_worker = None
        self._transfer_current = None
        if not self._transfer_queue:
            self.refresh()
            self.load_local_dir(self._local_dir)
        self._pump_queue()
        self._update_queue_label()

    def _update_queue_label(self):
        n = len(self._transfer_queue)
        if self._transfer_worker is not None:
            n += 1
        self.queue_label.setText(f"传输中/队列: {n} 项" if n > 0 else "")

    def has_active_transfer(self):
        return bool(self._transfer_queue) or (
            self._transfer_worker is not None
            and self._transfer_worker.isRunning())

    def on_path_entered(self):
        p = self.path_input.text().strip() or "."
        self.load_remote_dir(p)

    def bind_worker(self, worker):
        if self.current_worker == worker:
            return
        self.current_worker = worker
        if worker and worker.client:
            self.current_path = "."
            self.load_remote_dir(".")
        else:
            self.file_tree.clear()
            self.path_input.setText("未连接" if worker is None else "连接中...")

    def refresh(self):
        if not self.current_worker or not self.current_worker.client:
            return
        self.load_remote_dir(self.current_path)

    def load_remote_dir(self, path="."):
        if not self.current_worker or not self.current_worker.client:
            return
        self.current_path = path
        self.path_input.setText(path)

        self.file_tree.clear()
        ph = QTreeWidgetItem(["⏳ 正在加载...", "", "", "", ""])
        ph.setForeground(0, QBrush(QColor(TEXT_MUTED)))
        ph.setFlags(Qt.ItemFlag.NoItemFlags)
        self.file_tree.addTopLevelItem(ph)

        self._list_seq += 1
        seq = self._list_seq

        worker = SFTPWorker(
            self.current_worker.client, "list", path=path,
            include_hidden=self.chk_hidden.isChecked())
        self._list_worker = worker
        worker.file_list_ready.connect(
            lambda files, s=seq: self._on_list_ready(s, files))
        self._spawn(worker)

    def _on_list_ready(self, seq, files):
        if seq != self._list_seq:
            return
        self.populate_tree(files)

    def populate_tree(self, files):
        self.file_tree.clear()
        files = sorted(files, key=lambda f: (not f["is_dir"], f["name"].lower()))
        count = 0
        for f in files:
            if f["name"] in (".", ".."):
                continue
            size_str = _human_size(f["size"]) if not f["is_dir"] else "—"
            mode = f.get("mode", 0)
            perm_str = _stat.filemode(mode) if mode else "—"
            try:
                mt = datetime.datetime.fromtimestamp(f.get("mtime", 0)).strftime(
                    "%Y-%m-%d %H:%M") if f.get("mtime") else "—"
            except Exception:
                mt = "—"
            type_str = "📁 文件夹" if f["is_dir"] else "📄 文件"
            item = QTreeWidgetItem([f["name"], size_str, perm_str, mt, type_str])
            item.setData(0, Qt.ItemDataRole.UserRole, f)
            if f["is_dir"]:
                item.setForeground(0, QBrush(QColor(PRIMARY)))
            self.file_tree.addTopLevelItem(item)
            count += 1
        if count == 0:
            ph = QTreeWidgetItem(["(空目录)", "", "", "", ""])
            ph.setForeground(0, QBrush(QColor(TEXT_MUTED)))
            ph.setFlags(Qt.ItemFlag.NoItemFlags)
            self.file_tree.addTopLevelItem(ph)

        self._update_path_completer(files)

    def _update_path_completer(self, files):
        for f in files:
            if f["name"] in (".", ".."):
                continue
            p = self._join(f["name"])
            if f["is_dir"]:
                p = p.rstrip("/") + "/"
            self._known_paths[p] = None
            self._known_paths.move_to_end(p)
            while len(self._known_paths) > SFTP_PATH_CACHE_MAX:
                self._known_paths.popitem(last=False)
        self._path_completer_model.setStringList(sorted(self._known_paths.keys()))

    def go_up(self):
        if self.current_path in (".", "/", ""):
            return
        parent = os.path.dirname(self.current_path.rstrip('/'))
        self.load_remote_dir(parent if parent else "/")

    def _join(self, name):
        return os.path.join(self.current_path, name).replace("\\", "/")

    def _selected_items(self):
        return [it for it in self.file_tree.selectedItems()
                if it.data(0, Qt.ItemDataRole.UserRole)]

    def on_item_double_click(self, item):
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if data["is_dir"]:
            self.load_remote_dir(self._join(data["name"]))
        else:
            self.download_file(data["name"])

    def show_context_menu(self, pos):
        item = self.file_tree.itemAt(pos)
        menu = QMenu(self)

        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data:
                if not data["is_dir"]:
                    act_edit = QAction("📝 在线编辑", self)
                    act_edit.triggered.connect(self.edit_selected_file)
                    menu.addAction(act_edit)
                    act_dl = QAction("⬇️ 下载到…", self)
                    act_dl.triggered.connect(self.download_selected)
                    menu.addAction(act_dl)
                    act_dl2 = QAction("⬇️ 下载到左侧", self)
                    act_dl2.triggered.connect(self.download_selected_to_local)
                    menu.addAction(act_dl2)
                    menu.addSeparator()

                act_rename = QAction("✏️ 重命名", self)
                act_rename.triggered.connect(self.rename_selected)
                menu.addAction(act_rename)

                act_chmod = QAction("🔐 修改权限", self)
                act_chmod.triggered.connect(self.chmod_selected)
                menu.addAction(act_chmod)

                act_del = QAction("🗑️ 删除", self)
                act_del.triggered.connect(self.delete_selected)
                menu.addAction(act_del)

                menu.addSeparator()

        act_mkdir = QAction("📁 新建文件夹", self)
        act_mkdir.triggered.connect(self.mkdir_remote)
        menu.addAction(act_mkdir)

        act_upload = QAction("📁 上传文件", self)
        act_upload.triggered.connect(lambda: self.upload_file())
        menu.addAction(act_upload)

        act_upload_dir = QAction("📂 上传文件夹", self)
        act_upload_dir.triggered.connect(self.upload_folder)
        menu.addAction(act_upload_dir)

        menu.exec(self.file_tree.mapToGlobal(pos))

    def edit_selected_file(self):
        items = self._selected_items()
        if len(items) != 1:
            QMessageBox.information(self, "提示", "请先选中一个文件")
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data["is_dir"]:
            QMessageBox.information(self, "提示", "文件夹不支持在线编辑")
            return
        if not self.current_worker or not self.current_worker.client:
            return

        remote_path = self._join(data["name"])

        if data["size"] > MAX_EDIT_FILE_SIZE:
            reply = QMessageBox.question(
                self, "文件过大",
                f"文件大小 {_human_size(data['size'])} 超过在线编辑限制 "
                f"({MAX_EDIT_FILE_SIZE // (1024*1024)} MB)。\n"
                f"是否下载到本地并用系统默认程序打开？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self._download_and_open(remote_path, data["name"])
            return

        dialog = SFTPEditorDialog(
            self.current_worker.client, remote_path, data["name"], self)
        dialog.exec()

    def _download_and_open(self, remote_path, name):
        local_path, _ = QFileDialog.getSaveFileName(self, "保存文件", name)
        if not local_path:
            return

        def _after(msg, lp=local_path):
            if msg.startswith("✅") and os.path.exists(lp):
                QDesktopServices.openUrl(QUrl.fromLocalFile(lp))

        self._enqueue("get", local_path, remote_path, on_done=_after)

    def rename_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        old_path = self._join(data["name"])
        new_name, ok = QInputDialog.getText(
            self, "重命名", "新名称:", text=data["name"])
        if not ok or not new_name or new_name == data["name"]:
            return
        if new_name in (".", "..") or "/" in new_name or "\\" in new_name:
            QMessageBox.warning(self, "错误", "名称不能包含路径分隔符")
            return
        new_path = self._join(new_name)
        w = SFTPWorker(self.current_worker.client, "rename",
                       old=old_path, new=new_path)
        self._spawn(w, {'op_finished': self._on_op_finished})

    def chmod_selected(self):
        items = self._selected_items()
        if len(items) != 1:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        cur_mode = data.get("mode", 0)
        cur_oct = oct(cur_mode & 0o777)[2:] if cur_mode else "644"
        text, ok = QInputDialog.getText(
            self, "修改权限",
            f"权限（八进制）：{self._join(data['name'])}",
            text=cur_oct)
        if not ok or not text:
            return
        try:
            mode = int(text, 8)
        except ValueError:
            QMessageBox.warning(self, "错误", "请输入合法的八进制权限，例如 644")
            return
        w = SFTPWorker(self.current_worker.client, "chmod",
                       path=self._join(data["name"]), mode=mode)
        self._spawn(w, {'op_finished': self._on_op_finished})

    def delete_selected(self):
        items = self._selected_items()
        if not items:
            return
        names = [it.data(0, Qt.ItemDataRole.UserRole)["name"] for it in items]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除以下 {len(names)} 项？此操作不可撤销。\n\n"
            + "\n".join(names[:10]) + ("\n..." if len(names) > 10 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        paths = [self._join(n) for n in names]
        w = SFTPWorker(self.current_worker.client, "delete", paths=paths)
        self._spawn(w, {'op_finished': self._on_op_finished})

    def mkdir_remote(self):
        if not self.current_worker or not self.current_worker.client:
            return
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名:")
        if not ok or not name:
            return
        w = SFTPWorker(self.current_worker.client, "mkdir",
                       path=self._join(name))
        self._spawn(w, {'op_finished': self._on_op_finished})

    def _on_op_finished(self, success, msg):
        if not success:
            QMessageBox.warning(self, "操作失败", msg)
        self.refresh()

    def download_file(self, name):
        if not self.current_worker or not self.current_worker.client:
            return
        remote_path = self._join(name)
        local_path, _ = QFileDialog.getSaveFileName(self, "保存文件", name)
        if not local_path:
            return
        self._enqueue("get", local_path, remote_path)

    def download_selected(self):
        items = self._selected_items()
        if not items:
            return
        if len(items) == 1:
            data = items[0].data(0, Qt.ItemDataRole.UserRole)
            if not data["is_dir"]:
                self.download_file(data["name"])
                return
            target_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
            if not target_dir:
                return
            local = os.path.join(target_dir, data["name"])
            self._enqueue("get_recursive", local, self._join(data["name"]))
            return

        target_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not target_dir:
            return
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            local = os.path.join(target_dir, data["name"])
            if data["is_dir"]:
                self._enqueue("get_recursive", local, self._join(data["name"]))
            else:
                self._enqueue("get", local, self._join(data["name"]))

    def upload_file(self, file_path=None):
        if not self.current_worker or not self.current_worker.client:
            QMessageBox.warning(self, "提示", "SSH 连接未就绪")
            return
        if not file_path:
            paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传的文件")
            if not paths:
                return
        else:
            paths = [file_path]

        for p in paths:
            if not os.path.isfile(p):
                continue
            remote = self._join(os.path.basename(p))
            self._enqueue("put", p, remote)

    def upload_folder(self):
        if not self.current_worker or not self.current_worker.client:
            QMessageBox.warning(self, "提示", "SSH 连接未就绪")
            return
        local_dir = QFileDialog.getExistingDirectory(self, "选择本地文件夹")
        if not local_dir:
            return
        self.upload_folder_path(local_dir)

    def upload_folder_path(self, local_dir):
        if not self.current_worker or not self.current_worker.client:
            return
        name = os.path.basename(local_dir.rstrip("/\\")) or "upload"
        remote = self._join(name)
        self._enqueue("put_recursive", local_dir, remote)