"""Pygments 驱动的语法高亮 + 行号编辑器。"""
import os

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import (QSyntaxHighlighter, QTextCharFormat, QColor,
                         QFont, QPainter, QTextFormat)
from PyQt6.QtWidgets import QPlainTextEdit, QWidget

try:
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, TextLexer
    from pygments.token import Token
    from pygments.util import ClassNotFound
    _PYGMENTS_AVAILABLE = True
except ImportError:
    _PYGMENTS_AVAILABLE = False


_MAX_HIGHLIGHT_LINE = 1000
_MAX_HIGHLIGHT_FILE = 1024 * 1024


EXT_LEXER = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".jsx": "jsx", ".tsx": "tsx",
    ".go": "go", ".rs": "rust",
    ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".toml": "toml", ".ini": "ini", ".conf": "nginx",
    ".sql": "sql", ".html": "html", ".css": "css",
    ".md": "markdown", ".markdown": "markdown",
    ".xml": "xml", ".php": "php", ".rb": "ruby",
    ".lua": "lua", ".pl": "perl", ".r": "r",
}

TOKEN_COLOR_MAP = {
    Token.Keyword: "#C792EA",
    Token.Name.Function: "#82AAFF",
    Token.Name.Class: "#FFCB6B",
    Token.Name.Builtin: "#F78C6C",
    Token.String: "#C3E88D",
    Token.Number: "#F78C6C",
    Token.Comment: "#546E7A",
    Token.Operator: "#89DDFF",
    Token.Name.Decorator: "#FFCB6B",
}


def _build_formats():
    fmt_map = {}
    for token, color in TOKEN_COLOR_MAP.items():
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt_map[token] = fmt
    return fmt_map


class PygmentsHighlighter(QSyntaxHighlighter):
    def __init__(self, document, lexer_alias="text"):
        super().__init__(document)
        self._formats = _build_formats()
        self._lexer = TextLexer() if _PYGMENTS_AVAILABLE else None
        self._enabled = _PYGMENTS_AVAILABLE
        self._line_limit = _MAX_HIGHLIGHT_LINE
        self._alias = "text"          # ★ 新增：记录当前别名，避免重复 rehighlight
        if _PYGMENTS_AVAILABLE:
            self.set_lexer_alias(lexer_alias)

    def set_lexer_alias(self, alias):
        if not _PYGMENTS_AVAILABLE:
            return
        # ★ 优化：别名未变则不触发 rehighlight（大文件反复调用会拖慢 UI）
        if alias == self._alias:
            return
        try:
            self._lexer = get_lexer_by_name(alias, stripall=False)
        except Exception:
            self._lexer = TextLexer()
            alias = "text"
        self._alias = alias
        self.rehighlight()

    def set_enabled(self, enabled):
        enabled = bool(enabled) and _PYGMENTS_AVAILABLE
        if self._enabled != enabled:
            self._enabled = enabled
            self.rehighlight()

    def suspend(self):
        self._enabled = False

    def resume(self, enabled=True):
        enabled = bool(enabled) and _PYGMENTS_AVAILABLE
        if self._enabled != enabled:
            self._enabled = enabled
            self.rehighlight()

    def highlightBlock(self, text):
        if not self._enabled or self._lexer is None:
            return
        try:
            n = len(text)
            if n == 0 or n > self._line_limit:
                return
            offset = 0
            for token, value in lex(text, self._lexer):
                vlen = len(value)
                if vlen <= 0:
                    continue
                fmt = self._resolve_format(token)
                if fmt is not None:
                    if offset < 0:
                        offset = 0
                    if offset + vlen > n:
                        vlen = n - offset
                    if vlen > 0:
                        self.setFormat(offset, vlen, fmt)
                offset += vlen
                if offset >= n:
                    break
        except RecursionError:
            return
        except Exception:
            return

    def _resolve_format(self, token):
        fmt = self._formats.get(token)
        if fmt is not None:
            return fmt
        try:
            parent = token.parent
            while parent is not None:
                fmt = self._formats.get(parent)
                if fmt is not None:
                    return fmt
                parent = parent.parent
        except Exception:
            pass
        return None


class LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    def __init__(self, filename="", parent=None):
        super().__init__(parent)
        self._line_number_area = LineNumberArea(self)
        self._highlighter = None
        self._cached_line_width = -1
        self.setFont(QFont("Cascadia Code", 11))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setStyleSheet(
            "QPlainTextEdit { background:#1e1e2e; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:8px; padding:6px; "
            "selection-background-color:#45475a; }")

        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width()
        self._highlight_current_line()

        self._highlighter = PygmentsHighlighter(self.document(), "text")
        if filename:
            self.set_lexer_for_filename(filename)

    def set_lexer_for_filename(self, filename):
        alias = self._alias_for(filename)
        if self._highlighter is not None:
            self._highlighter.set_lexer_alias(alias)

    @staticmethod
    def _alias_for(filename):
        base = os.path.basename(filename).lower()
        if base in ("dockerfile", "containerfile"):
            return "docker"
        if base in ("makefile", "gnumakefile"):
            return "make"
        ext = os.path.splitext(filename)[1].lower()
        return EXT_LEXER.get(ext, "text")

    def setPlainText(self, text):
        text = text or ""
        if "\x00" in text:
            text = text.replace("\x00", "")
        if self._highlighter is not None:
            self._highlighter.suspend()
        super().setPlainText(text)
        if self._highlighter is not None:
            self._highlighter.resume(len(text) <= _MAX_HIGHLIGHT_FILE)

    def setReadOnly(self, ro):
        super().setReadOnly(ro)
        self._highlight_current_line()

    def line_number_width(self):
        digits = max(1, len(str(self.blockCount())))
        w = 14 + self.fontMetrics().horizontalAdvance("9") * digits
        if w == self._cached_line_width:
            return w
        self._cached_line_width = w
        return w

    def _update_line_number_width(self, _=None):
        try:
            self.setViewportMargins(self.line_number_width(), 0, 0, 0)
        except Exception:
            pass

    def _update_line_number_area(self, rect, dy):
        try:
            if dy:
                self._line_number_area.scroll(0, dy)
            else:
                self._line_number_area.update(
                    0, rect.y(),
                    self._line_number_area.width(), rect.height())
            if rect.contains(self.viewport().rect()):
                self._update_line_number_width()
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            cr = self.contentsRect()
            self._line_number_area.setGeometry(
                QRect(cr.left(), cr.top(),
                      self.line_number_width(), cr.height()))
        except Exception:
            pass

    def paint_line_numbers(self, event):
        # ★ 修复：用 try/finally 保证 painter.end()，任何异常都不会泄漏画家资源
        painter = QPainter(self._line_number_area)
        try:
            painter.fillRect(event.rect(), QColor("#181825"))
            painter.setPen(QColor("#6c7086"))
            block = self.firstVisibleBlock()
            num = block.blockNumber()
            top = round(self.blockBoundingGeometry(block)
                        .translated(self.contentOffset()).top())
            bottom = top + round(self.blockBoundingRect(block).height())
            line_height = self.fontMetrics().height()
            while block.isValid() and top <= event.rect().bottom():
                if block.isVisible() and bottom >= event.rect().top():
                    painter.drawText(
                        0, top,
                        self._line_number_area.width() - 6, line_height,
                        Qt.AlignmentFlag.AlignRight, str(num + 1))
                block = block.next()
                top = bottom
                bottom = top + round(self.blockBoundingRect(block).height())
                num += 1
        except Exception:
            pass
        finally:
            painter.end()

    def _highlight_current_line(self):
        try:
            extra = []
            if not self.isReadOnly():
                sel = QPlainTextEdit.ExtraSelection()
                sel.format.setBackground(QColor("#313244"))
                sel.format.setProperty(
                    QTextFormat.Property.FullWidthSelection, True)
                sel.cursor = self.textCursor()
                sel.cursor.clearSelection()
                extra.append(sel)
            self.setExtraSelections(extra)
        except Exception:
            pass