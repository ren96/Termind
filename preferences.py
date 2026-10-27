"""首选项对话框。"""
from PyQt6.QtWidgets import (QDialog, QFormLayout, QSpinBox, QCheckBox,
                             QPushButton, QHBoxLayout, QLabel, QVBoxLayout,
                             QFrame)
from PyQt6.QtCore import QSettings

from constants import (ORG_NAME, APP_NAME, DEFAULT_FONT_SIZE, MIN_FONT_SIZE,
                       MAX_FONT_SIZE, DEFAULT_SCROLLBACK, DEFAULT_SSH_TIMEOUT,
                       DEFAULT_SSH_KEEPALIVE, DEFAULT_AUTO_RECONNECT,
                       DEFAULT_AUTO_RECONNECT_INTERVAL)


def _qs():
    return QSettings(ORG_NAME, APP_NAME)


class PreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("首选项")
        self.resize(500, 0)
        s = _qs()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 22, 22, 18)
        outer.setSpacing(14)

        # ---------- 终端 ----------
        terminal_frame, terminal_form = self._section(outer, "终端")
        self.spin_font = QSpinBox()
        self.spin_font.setRange(MIN_FONT_SIZE, MAX_FONT_SIZE)
        self.spin_font.setValue(s.value("terminal/font_size", DEFAULT_FONT_SIZE, type=int))

        self.spin_scrollback = QSpinBox()
        self.spin_scrollback.setRange(1000, 200000)
        self.spin_scrollback.setSingleStep(1000)
        self.spin_scrollback.setValue(s.value("terminal/scrollback", DEFAULT_SCROLLBACK, type=int))

        terminal_form.addRow("字体大小:", self.spin_font)
        terminal_form.addRow("Scrollback 行数:", self.spin_scrollback)

        # ---------- SSH ----------
        ssh_frame, ssh_form = self._section(outer, "SSH")
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(3, 120)
        self.spin_timeout.setSuffix(" 秒")
        self.spin_timeout.setValue(s.value("ssh/timeout", DEFAULT_SSH_TIMEOUT, type=int))

        self.spin_keepalive = QSpinBox()
        self.spin_keepalive.setRange(0, 300)
        self.spin_keepalive.setSuffix(" 秒")
        self.spin_keepalive.setSpecialValueText("禁用")
        self.spin_keepalive.setValue(s.value("ssh/keepalive", DEFAULT_SSH_KEEPALIVE, type=int))

        ssh_form.addRow("连接超时:", self.spin_timeout)
        ssh_form.addRow("Keepalive:", self.spin_keepalive)

        # ---------- 行为 ----------
        behavior_frame, behavior_form = self._section(outer, "行为")
        self.chk_auto_reconnect = QCheckBox("断开后自动重连")
        self.chk_auto_reconnect.setChecked(
            s.value("terminal/auto_reconnect", DEFAULT_AUTO_RECONNECT, type=bool))

        self.spin_reconnect_interval = QSpinBox()
        self.spin_reconnect_interval.setRange(1, 60)
        self.spin_reconnect_interval.setSuffix(" 秒")
        self.spin_reconnect_interval.setValue(
            s.value("terminal/auto_reconnect_interval",
                    DEFAULT_AUTO_RECONNECT_INTERVAL, type=int))

        self.chk_confirm_transfer = QCheckBox("关闭标签时若传输进行中则确认")
        self.chk_confirm_transfer.setChecked(
            s.value("ui/confirm_active_transfer", True, type=bool))

        # ★ 新增：多行粘贴确认
        self.chk_confirm_multiline = QCheckBox(
            "粘贴多行内容时确认（生产环境始终确认）")
        self.chk_confirm_multiline.setChecked(
            s.value("ui/confirm_multiline_paste", False, type=bool))

        behavior_form.addRow(self.chk_auto_reconnect)
        behavior_form.addRow("自动重连间隔:", self.spin_reconnect_interval)
        behavior_form.addRow(self.chk_confirm_transfer)
        behavior_form.addRow(self.chk_confirm_multiline)

        hint = QLabel("字体大小与 scrollback 修改后对新开的标签生效；"
                      "已有标签可 Ctrl+滚轮临时调整字体。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        # ---------- 按钮 ----------
        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setObjectName("primaryBtn")
        btn_save.clicked.connect(self.save)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        outer.addLayout(btn_box)

    def _section(self, parent_layout, title):
        label = QLabel(title)
        label.setObjectName("groupTitle")
        parent_layout.addWidget(label)

        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        parent_layout.addWidget(frame)
        return frame, form

    def save(self):
        s = _qs()
        s.setValue("terminal/font_size", self.spin_font.value())
        s.setValue("terminal/scrollback", self.spin_scrollback.value())
        s.setValue("ssh/timeout", self.spin_timeout.value())
        s.setValue("ssh/keepalive", self.spin_keepalive.value())
        s.setValue("terminal/auto_reconnect", self.chk_auto_reconnect.isChecked())
        s.setValue("terminal/auto_reconnect_interval", self.spin_reconnect_interval.value())
        s.setValue("ui/confirm_active_transfer", self.chk_confirm_transfer.isChecked())
        s.setValue("ui/confirm_multiline_paste", self.chk_confirm_multiline.isChecked())   # ★ 新增
        s.sync()
        self.accept()