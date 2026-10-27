"""集中式主题与样式表。"""

PRIMARY = "#6366F1"
PRIMARY_HOVER = "#4F46E5"
PRIMARY_ACTIVE = "#4338CA"
PRIMARY_LIGHT = "#EEF2FF"
PRIMARY_SOFT = "#E0E7FF"

SUCCESS = "#10B981"
WARNING = "#F59E0B"
DANGER = "#EF4444"

BG_APP = "#F4F5F9"
BG_SURFACE = "#FFFFFF"
BG_SUBTLE = "#FAFBFC"
BG_HOVER = "#F1F2F7"

BORDER = "#E4E6EE"
BORDER_STRONG = "#CBD0DC"

TEXT = "#111827"
TEXT_SECONDARY = "#6B7280"
TEXT_MUTED = "#9CA3AF"

FONT_FAMILY = ('"Segoe UI", "Microsoft YaHei", "PingFang SC", '
               '"Noto Sans CJK SC", sans-serif')
MONO_FONT = '"Cascadia Code", "Consolas", "Courier New", monospace'


def get_stylesheet():
    return f"""
    QMainWindow, QWidget {{
        background-color: {BG_APP};
        color: {TEXT};
        font-family: {FONT_FAMILY};
        font-size: 10pt;
    }}
    QLabel {{ background: transparent; }}

    /* ---------- 顶栏 ---------- */
    QFrame#topBar {{
        background-color: {BG_SURFACE};
        border: none;
        border-bottom: 1px solid {BORDER};
    }}
    QLabel#brandTitle {{
        font-size: 13pt; font-weight: 700; color: {TEXT};
    }}
    QLabel#brandSub {{
        font-size: 8pt; color: {TEXT_MUTED};
    }}

    /* ---------- 卡片 ---------- */
    QFrame#card {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}

    /* ---------- 分割器 ---------- */
    QSplitter::handle {{ background-color: transparent; }}
    QSplitter::handle:horizontal {{ width: 1px; margin: 0 4px; }}

    /* ---------- 树形列表 ---------- */
    QTreeWidget {{
        background-color: transparent;
        border: none;
        outline: none;
        padding: 2px;
    }}
    QTreeWidget::item {{
        height: 36px; border-radius: 8px; padding: 0 6px;
        color: {TEXT};
    }}
    QTreeWidget::item:hover {{ background-color: {BG_HOVER}; }}
    QTreeWidget::item:selected {{
        background-color: {PRIMARY_LIGHT}; color: {PRIMARY}; font-weight: 600;
    }}
    QHeaderView::section {{
        background-color: transparent;
        border: none; border-bottom: 1px solid {BORDER};
        padding: 6px 8px; font-weight: 600;
        color: {TEXT_MUTED}; font-size: 8pt;
        text-transform: uppercase;
    }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 7px 14px;
        color: {TEXT};
        font-size: 10pt;
    }}
    QPushButton:hover {{
        border-color: {PRIMARY_SOFT}; color: {PRIMARY};
        background-color: {PRIMARY_LIGHT};
    }}
    QPushButton:pressed {{ background-color: {PRIMARY_SOFT}; }}
    QPushButton:disabled {{
        color: {TEXT_MUTED}; border-color: {BORDER}; background: {BG_SUBTLE};
    }}

    /* 主色调按钮 —— hover 时文字上移（抬升感），pressed 时文字下移（按下感）
       ★ 修复：原代码 hover 下移、pressed 上移，视觉反馈反了。 */
    QPushButton#primaryBtn {{
        background-color: {PRIMARY};
        color: #FFFFFF;
        border: 1px solid {PRIMARY};
        font-weight: 600;
        padding: 8px 16px;
        min-width: 68px;
    }}
    QPushButton#primaryBtn:hover {{
        background-color: {PRIMARY_HOVER};
        border-color: {PRIMARY_HOVER};
        color: #FFFFFF;
        padding-top: 9px; padding-bottom: 7px;
    }}
    QPushButton#primaryBtn:pressed {{
        background-color: {PRIMARY_ACTIVE};
        padding-top: 7px; padding-bottom: 9px;
    }}
    QPushButton#primaryBtn:disabled {{
        background-color: {BORDER_STRONG};
        border-color: {BORDER_STRONG};
        color: #FFFFFF;
    }}

    /* 透明幽灵按钮 */
    QPushButton#ghostBtn {{
        background-color: transparent; border: 1px solid transparent;
        color: {TEXT_SECONDARY};
    }}
    QPushButton#ghostBtn:hover {{
        background-color: {BG_HOVER}; color: {TEXT}; border-color: transparent;
    }}

    /* 危险按钮 */
    QPushButton#dangerBtn {{
        background-color: transparent; color: {DANGER};
        border: 1px solid {DANGER};
    }}
    QPushButton#dangerBtn:hover {{ background-color: rgba(239,68,68,0.08); }}

    /* ---------- 输入框 ---------- */
    QLineEdit, QTextEdit, QTextBrowser, QComboBox, QPlainTextEdit {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 7px 11px;
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {PRIMARY};
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {PRIMARY};
        background-color: #FFFFFF;
        padding: 6px 10px;       /* 抵消边框变粗，避免布局抖动 */
    }}
    QLineEdit:disabled {{ background-color: {BG_SUBTLE}; color: {TEXT_MUTED}; }}

    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
        selection-background-color: {PRIMARY_LIGHT}; selection-color: {PRIMARY};
        outline: none;
    }}

    /* ---------- 标签页 ---------- */
    QTabWidget::pane {{
        border: 1px solid {BORDER}; border-radius: 12px;
        background: {BG_SURFACE}; top: -1px;
    }}
    QTabBar::tab {{
        background: transparent; color: {TEXT_SECONDARY};
        padding: 9px 20px; margin-right: 4px;
        border: 1px solid transparent;
        border-top-left-radius: 9px; border-top-right-radius: 9px;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; background: {BG_HOVER}; }}
    QTabBar::tab:selected {{
        background: {BG_SURFACE}; color: {PRIMARY};
        border: 1px solid {BORDER}; border-bottom-color: {BG_SURFACE};
        font-weight: 600;
    }}
    QTabBar::close-button {{
        subcontrol-position: right;
    }}

    /* ---------- 菜单 ---------- */
    QMenu {{
        background-color: {BG_SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px; padding: 6px;
        color: {TEXT};
    }}
    QMenu::item {{
        padding: 7px 22px; border-radius: 6px;
        color: {TEXT};
    }}
    QMenu::item:selected {{ background-color: {PRIMARY_LIGHT}; color: {PRIMARY}; }}
    QMenu::item:disabled {{ color: {TEXT_MUTED}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

    /* ---------- 进度条 ---------- */
    QProgressBar {{
        background-color: {BG_HOVER};
        border: none;
        border-radius: 5px;
        min-height: 10px; max-height: 10px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        border-radius: 5px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 {PRIMARY}, stop:1 #818CF8);
    }}

    /* ---------- 复选框 ---------- */
    QCheckBox {{ spacing: 8px; color: {TEXT_SECONDARY}; background: transparent; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1.5px solid {BORDER_STRONG};
        border-radius: 4px; background: {BG_SURFACE};
    }}
    QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}
    QCheckBox::indicator:checked {{
        background: {PRIMARY};
        border: 1.5px solid {PRIMARY};
        image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'></polyline></svg>");
    }}
    QCheckBox::indicator:checked:hover {{ background: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}

    QTreeWidget::indicator, QListWidget::indicator {{
        width: 16px; height: 16px;
        border: 1.5px solid {BORDER_STRONG};
        border-radius: 4px; background: {BG_SURFACE};
    }}
    QTreeWidget::indicator:checked, QListWidget::indicator:checked {{
        background: {PRIMARY}; border: 1.5px solid {PRIMARY};
        image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='white' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'><polyline points='20 6 9 17 4 12'></polyline></svg>");
    }}

    /* ---------- 滚动条 ---------- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {BORDER_STRONG}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {BORDER_STRONG}; border-radius: 4px; min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {TEXT_MUTED}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    /* ---------- 工具提示 ---------- */
    QToolTip {{
        background-color: {TEXT}; color: #FFFFFF;
        border: none; padding: 5px 9px; border-radius: 6px;
    }}

    /* ---------- 列表 ---------- */
    QListWidget {{
        background: transparent; border: none; outline: none;
    }}
    QListWidget::item {{
        height: 30px; border-radius: 6px; padding: 0 4px;
    }}
    QListWidget::item:hover {{ background: {BG_HOVER}; }}
    QListWidget::item:selected {{ background: {PRIMARY_LIGHT}; color: {PRIMARY}; }}

    /* ---------- 标签样式 ---------- */
    QLabel#titleLabel {{ font-size: 15pt; font-weight: 700; color: {TEXT}; }}
    QLabel#sectionLabel {{ font-size: 10pt; font-weight: 600; color: {TEXT}; }}
    QLabel#groupTitle {{
        font-size: 8pt; font-weight: 700; color: {TEXT_MUTED};
        text-transform: uppercase; letter-spacing: 0.5px;
    }}
    QLabel#hintLabel {{ font-size: 9pt; color: {TEXT_MUTED}; }}
    QLabel#emptyLabel {{ font-size: 10pt; color: {TEXT_MUTED}; padding: 24px; }}
    QLabel#statLabel {{
        font-size: 8pt; color: {TEXT_MUTED};
        padding: 2px 0;
    }}
    """