import sys
import io
import os
import time

import faulthandler

# ---- faulthandler：捕获原生崩溃（0xC0000409 会写入 crash.log）----
_crash_log = None
try:
    _crash_log_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "crash.log")
    _crash_log = open(_crash_log_path, "w", encoding="utf-8", errors="replace")
    faulthandler.enable(file=_crash_log)
except Exception:
    _crash_log = None

# ---- Qt WebEngine / OpenGL：彻底关闭 GPU 与相关日志噪音 ----
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    "--no-sandbox "
    "--disable-gpu "
    "--disable-gpu-compositing "
    "--disable-software-rasterizer "
    "--disable-features=Vulkan,UseSkiaRenderer "
    "--disable-logging "
    "--log-level=3"
)
os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_OPENGL"] = "software"
os.environ["QSG_RHI_BACKEND"] = "software"

# ---- stdout/stderr 统一 UTF-8（Windows 中文不崩）----
if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---- Windows 任务栏图标分组 ----
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Termind.SSHClient.1.3.0"
        )
    except Exception:
        pass

from PyQt6.QtCore import QCoreApplication, Qt, QSettings

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QIcon
from ui_main import MainWindow, _LINGERING_WORKERS
from constants import APP_NAME, ORG_NAME, get_resource_path

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setFont(QFont("Microsoft YaHei", 10))

    icon_path = get_resource_path(os.path.join("tubiao", "tubiao.ico"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()
    exit_code = app.exec()

    # 退出前刷新所有 QSettings
    try:
        QSettings(ORG_NAME, APP_NAME).sync()
    except Exception:
        pass

    # ★ 修复：绝不能在 Python 里 remove 掉最后一个引用，
    # 否则仍在 isRunning() 的 QThread 会被 Python GC 析构 → abort (0xC0000409)。
    # 只监控是否都结束，引用全部保留到 os._exit()，由 OS 一次性回收。
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            all_done = True
            for wk in _LINGERING_WORKERS:
                try:
                    if not wk.isFinished():
                        all_done = False
                        break
                except Exception:
                    # C++ 对象已删（实际上我们不会让它删），视为结束
                    pass
            if all_done:
                break
            time.sleep(0.05)
    except Exception:
        pass

    # flush 崩溃日志，确保 0xC0000409 的栈能落盘
    try:
        if _crash_log is not None:
            _crash_log.flush()
    except Exception:
        pass

    # ★ 强制退出：跳过 Python 解释器关闭 / GC，避免残留 QThread 在析构时崩溃
    os._exit(exit_code if isinstance(exit_code, int) else 0)