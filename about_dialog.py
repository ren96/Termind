"""关于对话框。"""
import os
import sys
import platform

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                             QPushButton, QFrame, QFormLayout)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QGuiApplication

try:
    from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
except ImportError:
    PYQT_VERSION_STR = "?"
    QT_VERSION_STR = "?"

from constants import APP_NAME, APP_VERSION, ORG_NAME, get_resource_path


def _pkg_version(name):
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "未知"


def _app_icon_pixmap(size):
    path = get_resource_path(os.path.join("tubiao", "tubiao.ico"))
    if not os.path.exists(path):
        return None
    pix = QPixmap(path)
    if pix.isNull():
        return None
    return pix.scaled(size, size,
                      Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)


def _build_info_text():
    lines = [
        f"{APP_NAME} {APP_VERSION}",
        f"组织: {ORG_NAME}",
        "",
        f"Python:   {sys.version.split()[0]}",
        f"PyQt6:    {PYQT_VERSION_STR}",
        f"Qt:       {QT_VERSION_STR}",
        f"paramiko: {_pkg_version('paramiko')}",
        f"keyring:  {_pkg_version('keyring')}",
        f"pygments: {_pkg_version('pygments')}",
        f"平台:     {platform.platform()}",
        f"可执行:   {sys.executable}",
    ]
    return "\n".join(lines)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setFixedWidth(620)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 18)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)

        icon_label = QLabel()
        icon_label.setFixedSize(72, 72)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = _app_icon_pixmap(72)
        if pix is not None:
            icon_label.setPixmap(pix)
        else:
            icon_label.setText("🖥️")
            icon_label.setStyleSheet("font-size: 48px;")
        header.addWidget(icon_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        name = QLabel(APP_NAME)
        name.setObjectName("titleLabel")
        version = QLabel(f"版本 {APP_VERSION}")
        version.setObjectName("hintLabel")
        desc = QLabel("远程开发工作站 · SSH / SFTP / 端口转发 / Git / 系统面板")
        desc.setObjectName("hintLabel")
        desc.setWordWrap(True)
        title_box.addWidget(name)
        title_box.addWidget(version)
        title_box.addWidget(desc)
        header.addLayout(title_box, 1)
        outer.addLayout(header)

        # ---------- 功能概览 ----------
        overview_card = QFrame()
        overview_card.setObjectName("card")
        overview_layout = QVBoxLayout(overview_card)
        overview_layout.setContentsMargins(16, 12, 16, 12)
        overview_layout.setSpacing(6)

        overview_title = QLabel("功能概览")
        overview_title.setObjectName("sectionLabel")
        overview_layout.addWidget(overview_title)

        overview_text = QLabel(
            "🖥️ 连接：密码 / 密钥 / 跳板机 / ssh config 导入 / 主机密钥确认\n"
            "⌨️ 终端：xterm.js 渲染、搜索、URL 点击、Ctrl+滚轮缩放、广播输入\n"
            "📁 传输：双栏 SFTP、递归上传下载、30+ 语言在线编辑\n"
            "🔌 转发：自动扫描远程监听端口，一键映射到本地\n"
            "🚀 开发：VS Code Server / Jupyter Lab 一键启动\n"
            "🛠 系统：端口 / systemd / Docker / 实时监控 / 实时日志\n"
            "📊 Git：status / log / diff 可视化，pull / stash\n"
            "🛡 安全：危险命令拦截、粘贴确认、生产环境水印"
        )
        overview_text.setObjectName("hintLabel")
        overview_text.setWordWrap(True)
        overview_layout.addWidget(overview_text)
        outer.addWidget(overview_card)

        # ---------- 环境信息 ----------
        env_card = QFrame()
        env_card.setObjectName("card")
        env_layout = QVBoxLayout(env_card)
        env_layout.setContentsMargins(16, 12, 16, 12)
        env_form = QFormLayout()
        env_form.setSpacing(6)
        env_form.setContentsMargins(0, 0, 0, 0)

        env_form.addRow("Python", QLabel(sys.version.split()[0]))
        env_form.addRow("PyQt6", QLabel(PYQT_VERSION_STR))
        env_form.addRow("Qt", QLabel(QT_VERSION_STR))
        env_form.addRow("paramiko", QLabel(_pkg_version("paramiko")))
        env_form.addRow("keyring", QLabel(_pkg_version("keyring")))
        env_form.addRow("pygments", QLabel(_pkg_version("pygments")))
        env_form.addRow("平台", QLabel(platform.platform()))
        env_layout.addLayout(env_form)
        outer.addWidget(env_card)

        stack_label = QLabel(
            "本程序基于 PyQt6 / Qt WebEngine / xterm.js / Paramiko / Pygments 构建。"
            "终端渲染由 xterm.js 提供，SSH 通信由 Paramiko 完成，"
            "密码通过系统 keyring 保管。"
        )
        stack_label.setObjectName("hintLabel")
        stack_label.setWordWrap(True)
        outer.addWidget(stack_label)

        btn_row = QHBoxLayout()
        btn_copy = QPushButton("复制环境信息")
        btn_copy.clicked.connect(self._copy_info)
        btn_row.addWidget(btn_copy)
        btn_row.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("primaryBtn")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        outer.addLayout(btn_row)

    def _copy_info(self):
        QGuiApplication.clipboard().setText(_build_info_text())
        btn = self.sender()
        if isinstance(btn, QPushButton):
            btn.setText("✓ 已复制")
            QTimer.singleShot(1500, lambda b=btn: self._restore_copy_btn(b))

    @staticmethod
    def _restore_copy_btn(btn):
        # 对话框可能在 1.5s 内已关闭，btn 已被 deleteLater；忽略 RuntimeError
        try:
            btn.setText("复制环境信息")
        except RuntimeError:
            pass