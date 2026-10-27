"""Shared parts of the conversation window: constants, style sheet, icons and small buttons."""
from __future__ import annotations


import difflib
import html
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from agentdock.dispatch.adapter import EFFORT_LABELS
from agentdock.dispatch.projects import UNSORTED
from agentdock.dispatch.store import owner_for, work_id
from agentdock.dispatch.transcript import Transcript, tool_summary
from agentdock.ui import theme
from agentdock.ui.dispatch_view import FormBox, status_text, text_box
from agentdock.ui.qa_view import button, label
from agentdock.ui import web_transcript
from agentdock.ui.question_text import QuestionText
from agentdock.ui.web_transcript import tool_name
from agentdock.ui.widgets import ElidedLabel, icon_button


ROLE = Qt.ItemDataRole.UserRole


CHAT_MODES = {'claude-code': 'plan', 'codex': 'read-only', 'opencode': 'plan'}   # 只聊天: propose, do not act


AGENTS = (('opencode', 'OpenCode'), ('claude-code', 'Claude'), ('codex', 'Codex'), ('grok-cli', 'Grok'))


RAIL = 56


SIDEBAR = (220, 270, 460)   # narrowest, default and widest conversation list


FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')


def split_code(text: str) -> list[tuple]:
    """Markdown split into ('text', body) and ('code', language, body); an unclosed fence runs to the end."""
    out: list[tuple] = []
    lines: list[str] = []
    code: list | None = None   # [fence char, fence length, language, lines]
    for line in text.split('\n'):
        marker = FENCE.match(line)
        if code is None:
            if marker:
                if '\n'.join(lines).strip():
                    out.append(('text', '\n'.join(lines)))
                lines = []
                info = marker.group(2).strip()
                code = [marker.group(1)[0], len(marker.group(1)), info.split()[0] if info else '', []]
            else:
                lines.append(line)
        elif marker and marker.group(1)[0] == code[0] and len(marker.group(1)) >= code[1] and not marker.group(2).strip():
            out.append(('code', code[2], '\n'.join(code[3])))
            code = None
        else:
            code[3].append(line)
    if code is not None:
        out.append(('code', code[2], '\n'.join(code[3])))
    elif '\n'.join(lines).strip():
        out.append(('text', '\n'.join(lines)))
    return out


def when(stamp: str, now: datetime | None = None) -> str:
    """A short local time for the list: 剛剛, 5 分, 14:03, 昨天, 10/20, 2025/12/31."""
    try:
        moment = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone()
    except (ValueError, AttributeError):
        return ''
    now = (now or datetime.now(timezone.utc)).astimezone()
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return '剛剛'
    if seconds < 3600:
        return f'{int(seconds // 60)} 分'
    days = (now.date() - moment.date()).days
    if days == 0:
        return moment.strftime('%H:%M')
    if days == 1:
        return '昨天'
    if moment.year == now.year:
        return f'{moment.month}/{moment.day}'
    return f'{moment.year}/{moment.month}/{moment.day}'


def resume_command(row: dict) -> list[str] | None:
    """The client's own command that continues this session in a terminal."""
    client, sid = row['client'], row['session_id']
    if client == 'claude-code':
        from agentdock.dispatch.claude import find_executable
        args = ['--resume', sid]
    elif client == 'codex':
        from agentdock.dispatch.codex import find_executable
        args = ['resume', sid]
    elif client == 'grok-cli':
        from agentdock.dispatch.grok import find_executable
        args = ['--resume', sid]
    else:
        from agentdock.dispatch.opencode import find_executable
        args = ['-s', sid]
    exe = find_executable()
    return [exe, *args] if exe else None


def tokens(count: int) -> str:
    if count >= 1_000_000:
        return f'{count / 1_000_000:.1f}M'
    if count >= 1000:
        return f'{count / 1000:.0f}k' if count >= 10_000 else f'{count / 1000:.1f}k'
    return str(count)


def context_text(usage: dict | None) -> str:
    """How full the model's context is, as the client last reported it."""
    if not usage or not usage.get('used'):
        return ''
    used, limit = int(usage['used']), int(usage.get('limit') or 0)
    if limit:
        return f'上下文 {min(100, round(used * 100 / limit))}%'
    return f'上下文 {tokens(used)}'


def _style() -> str:
    t = theme
    # The sweet look rounds everything more; parts the local look paints itself drop their plain box.
    big, mid, small = (26, 20, 14) if t.CUTE else (18, 14, 8)
    # Qt drops the rounding when a radius exceeds half the height: single-line parts get their own.
    bubble, pill = (17, 14) if t.CUTE else (14, 8)
    line = f'1px solid {t.LINE}'
    # The rail and the list read as one panel: no line between them, one line where the content starts.
    rail_bg, rail_line = ('transparent', 'none') if t.decorated('canvas') else (t.CARD, 'none')
    side_bg, side_line = ('transparent', 'none') if t.decorated('panel') else (t.CARD, line)
    box_bg, box_line = ('transparent', 'none') if t.decorated('box') else (t.CARD, line)
    # The accent is kept for what needs the eye; the user's own words sit on a quiet grey.
    bubble_bg, bubble_line = (('transparent', 'none') if t.decorated('bubble') else
                              (t.ACCENT_SOFT, line) if t.CUTE else (t.LAVENDER, 'none'))
    code_bg, code_line = ('transparent', 'none') if t.decorated('code') else (t.ACCENT_SOFT, line)
    request_line = f'1px dashed {t.LINE}' if t.CUTE else line
    return t.sized(f"""
#ChatWindow {{ background: {t.CANVAS}; }}
#ChatRail {{ background: {rail_bg}; border-right: {rail_line}; }}
#ChatRail QPushButton {{ border: none; border-radius: {small}px; padding: 10px; background: transparent; }}
#ChatRail QPushButton:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatRail QPushButton[current="true"] {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DEEP}; font-weight: 700; }}
#ChatSidebar {{ background: {side_bg}; border-right: {side_line}; }}
#ChatSplit::handle {{ background: transparent; }}
QPushButton#RowMore {{ border: none; background: transparent; border-radius: 6px; padding: 0; }}
QPushButton#RowMore:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatSplit::handle:hover {{ background: {t.ACCENT_SOFT}; }}
#ChatSidebar QTreeWidget {{ background: transparent; border: none; outline: none; }}
#ChatSidebar QTreeWidget::item {{ min-height: 30px; padding: 2px 4px; background: transparent; }}
#ChatSidebar QTreeWidget::item:selected {{ background: transparent; color: {t.INK}; }}
#ChatSidebar QTreeWidget::branch {{ background: transparent; border: none; }}
#SectionLabel {{ color: {t.MUTED}; font-size: 12px; }}
#UserBubble {{ background: {bubble_bg}; border: {bubble_line}; border-radius: {bubble}px; }}
#EditBox {{ border: none; background: transparent; }}
#ToolRow {{ text-align: left; border: none; border-radius: {pill}px; min-height: 20px; padding: 3px 8px; background: transparent; color: {t.MUTED}; font-size: 12px; }}
#ToolRow:hover {{ background: {t.HOVER}; color: {t.INK}; }}
#ToolDetail {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {small}px; padding: 8px; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 12px; }}
#Request {{ background: {t.WARN_BG}; border: {request_line}; border-radius: {mid}px; }}
#Composer {{ background: {box_bg}; border: {box_line}; border-radius: {big}px; }}
#Composer QPlainTextEdit {{ border: none; background: transparent; }}
#ConvTitle {{ font-size: 14px; font-weight: 600; }}
#PageTitle {{ font-size: 22px; font-weight: 700; }}
#ChatChip {{ border: none; border-radius: 8px; padding: 3px 8px; color: {t.MUTED}; font-size: 12px; background: transparent; }}
QPushButton#ChatChip:hover {{ background: {t.HOVER}; color: {t.INK}; }}
QPushButton#NewChat {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 10px; padding: 7px 14px; color: {t.INK}; font-weight: 600; }}
QPushButton#NewChat:hover {{ border-color: {t.ACCENT}; color: {t.ACCENT_DEEP}; }}
#EndLine {{ color: {t.MUTED}; font-size: 12px; }}
#EndLine[failed="true"] {{ color: {t.DANGER}; }}
#CodeBlock {{ background: {code_bg}; border: {code_line}; border-radius: {small}px; }}
#CodeBlock QScrollArea, #CodeBlock QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
#CodeLang {{ color: {t.MUTED}; font-size: 11px; }}
QPushButton#MiniButton {{ border: none; background: transparent; color: {t.MUTED}; padding: 2px 8px; font-size: 12px; border-radius: 6px; }}
QPushButton#MiniButton:hover {{ background: {t.HOVER}; color: {t.INK}; }}
QPushButton#FileChip {{ border: 1px solid {t.LINE}; border-radius: 11px; padding: 2px 10px; background: {t.CANVAS}; color: {t.INK}; font-size: 12px; }}
QPushButton#ImageChip {{ border: 1px solid {t.LINE}; border-radius: 10px; padding: 3px 8px 3px 3px; background: {t.CANVAS}; color: {t.MUTED}; font-size: 12px; }}
#FileNote {{ color: {t.MUTED}; font-size: 12px; }}
#ErrorText {{ color: {t.DANGER}; }}
QDialog {{ background: {t.CANVAS}; }}
QDialog QTreeWidget, QDialog QTextBrowser {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {small}px; outline: none; }}
QDialog QTreeWidget::item {{ min-height: 26px; }}
QDialog QTreeWidget::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.INK}; }}
QDialog QHeaderView::section {{ background: {t.CARD}; color: {t.MUTED}; border: none; border-bottom: 1px solid {t.LINE}; padding: 4px 6px; }}
#ChatChip[high="true"] {{ color: {t.DANGER}; border-color: {t.DANGER}; }}
QPushButton#Chip {{ border: none; background: transparent; color: {t.MUTED}; padding: 4px 20px 4px 8px; border-radius: 12px; font-size: 13px; text-align: left; }}
QPushButton#Chip:hover {{ background: {t.HOVER}; color: {t.INK}; }}
QPushButton#Chip:disabled {{ color: {t.DIM}; }}
QPushButton#IconButton {{ border: none; background: transparent; border-radius: 15px; padding: 0; }}
QPushButton#IconButton:hover {{ background: {t.HOVER}; }}
QPushButton#RoundButton, QPushButton#StopButton {{ border: none; border-radius: 15px; padding: 0; background: {t.ACCENT}; }}
QPushButton#RoundButton:hover, QPushButton#StopButton:hover {{ background: {t.ACCENT_DEEP}; }}
QPushButton#RoundButton:disabled {{ background: {t.DIM}; }}
QPushButton#RecentRow {{ border: none; background: transparent; border-radius: {small}px; padding: 0; text-align: left; }}
QPushButton#RecentRow:hover {{ background: {t.HOVER}; }}
#RecentTitle {{ font-size: 14px; }}
#RecentMeta {{ color: {t.MUTED}; font-size: 12px; }}
#RecentAlert {{ color: {t.ALERT}; font-size: 12px; }}
#DiffView {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {small}px; padding: 8px; }}
#Reasoning {{ color: {t.MUTED}; }}
#ProcessRule {{ background: {t.LINE}; border: none; margin: 4px 0 8px; }}
#FindBar {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {small}px; }}
QPushButton#LatestButton {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: 16px; padding: 0; }}
QPushButton#LatestButton:hover {{ border-color: {t.ACCENT}; }}
#PaletteBackdrop {{ background: rgba(0, 0, 0, 40); }}
#Palette {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {big}px; }}
#Palette QListWidget {{ border: none; }}
#Palette QListWidget::item {{ padding: 6px 10px; }}
#Suggestions {{ background: transparent; border: none; border-bottom: 1px solid {t.LINE}; outline: none; padding: 0 0 4px 0; }}
#Suggestions::item {{ padding: 4px 8px; border-radius: 10px; color: {t.INK}; }}
#Suggestions::item:selected {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT_DEEP}; }}
QPushButton[seg="true"] {{ border-radius: 10px; padding: 5px 14px; }}
#SettingsPage[wide="true"] QWidget#SectionHead {{ background: transparent; }}
#SettingsPage[wide="true"] QWidget#WideList {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {mid}px; }}
#QuotaCard {{ background: {t.CARD}; border: 1px solid {t.LINE}; border-radius: {mid}px; }}
""" + t.extra_style('chat'))


class Bridge(QObject):
    """Worker-thread callbacks into the UI thread."""
    history = Signal(str, list, object)
    history_failed = Signal(str, str)
    live = Signal(str, dict)
    started = Signal(str)
    deleted = Signal(str)
    replaced = Signal(str, str)        # a conversation that started over in a new session: old key, new key
    reload = Signal(str)               # show again what the client holds for this conversation
    options = Signal(str, str, dict)   # client, conversation key ('' for a new one), choices
    git = Signal(str, str, str)        # conversation key, repository root ('' when none), status summary
    sessions = Signal(str, list)       # client, its saved sessions


ICONS = {
    'chat': 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z',
    'tools': 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z',
    'settings': 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3 1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8 1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
    'folder': 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
    'clip': 'M21 11.5l-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l7.9-7.9',
    'more': 'M5 12h.01M12 12h.01M19 12h.01',
    'plus': 'M12 5v14M5 12h14',
    'up': 'M12 19V5M5 12l7-7 7 7',
    'stop': 'M8 8h8v8H8z',
    'down': 'M12 5v14M19 12l-7 7-7-7',
    'chevron': 'M6 9l6 6 6-6',
    'shield': 'M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z',
    'gauge': 'M4.2 17a8.5 8.5 0 1 1 15.6 0M12 14l3.5-4.5',
    'agent': 'M12 8V4M8 4h8M6 8h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM9 13h.01M15 13h.01',
}


GAP = 14   # between messages of the conversation


COLUMN = 720   # the reading column: header, messages and the message box share its edges


def heart_icon(filled: bool, color: str, size: int = 14):
    """A drawn heart for fold rows and buttons: Windows fonts may show ♡ as another glyph."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
    from agentdock.ui.settings_page import heart_path
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = heart_path(QPointF(size, size * 1.15), size * 0.78)
    painter.setPen(QPen(QColor(color), 2.2))
    painter.setBrush(QColor(color) if filled else Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def fold_mark(button, shown: bool) -> str:
    """The fold sign of a row: a drawn heart in the sweet look (as the icon), ▸ / ▾ otherwise (in the text)."""
    if theme.CUTE:
        button.setIcon(heart_icon(shown, theme.ACCENT or '#e0529a'))
        return ''
    button.setIcon(QIcon())
    return theme.t('fold_open' if shown else 'fold_closed') + ' '


def paint_decor(widget, name: str, event, *args) -> None:
    """After the widget's own painting, let the local look draw `name` over its whole rect."""
    if theme.decorated(name):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter
        painter = QPainter(widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme.decorate(name, painter, QRectF(widget.rect()), *args)
        painter.end()


def on_accent() -> str:
    """Text and icon colour on an accent-filled button."""
    return 'white' if theme.CUTE else (theme.CANVAS or '#000000')


def icon(name: str, color: str, size: int = 18):
    """A stroke icon in the theme's colour; fonts of private themes may lack emoji."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{ICONS[name]}"/></svg>')
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class ChipButton(QPushButton):
    """A flat pick-one button as in desktop chat apps: optional icon, the current value and a small chevron.
    Clicking opens its menu under it."""

    def __init__(self, icon_name: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('Chip')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_name = icon_name
        self.menu_ = QMenu(self)
        self.clicked.connect(self.open_menu)
        self.restyle()

    def restyle(self) -> None:
        if self.icon_name:
            self.setIcon(icon(self.icon_name, theme.MUTED or '#888888', 14))

    def open_menu(self) -> None:
        if self.menu_.actions():
            self.menu_.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        from PySide6.QtGui import QPainter, QPen
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(theme_color('MUTED' if self.isEnabled() else 'DIM'))
        pen.setWidthF(1.4)
        painter.setPen(pen)
        x, y = self.width() - 14, self.height() / 2 - 1.5
        painter.drawPolyline([QPointF(x, y), QPointF(x + 3.5, y + 3.5), QPointF(x + 7, y)])
        painter.end()


class RoundButton(QPushButton):
    """The circular send / stop button at the end of the message box."""

    def __init__(self, icon_name: str, tip: str, parent=None):
        super().__init__(parent)
        self.setObjectName('RoundButton')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(tip)
        self.setFixedSize(32, 32)
        self.setIconSize(QSize(16, 16))
        self.icon_name = icon_name
        self.restyle()

    def restyle(self) -> None:
        self.setIcon(icon(self.icon_name, on_accent(), 16))

    def paintEvent(self, event):  # noqa: N802
        if theme.decorated('round'):
            from PySide6.QtCore import QRectF
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            theme.decorate('round', painter, QRectF(self.rect()), self.icon_name, self.isEnabled(), self.underMouse())
            painter.end()
            return
        super().paintEvent(event)

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self.update()


def hidden(widget: QWidget) -> bool:
    """Hidden on purpose (not just because its window is not shown yet)."""
    return widget.isHidden() and widget.testAttribute(Qt.WidgetAttribute.WA_WState_ExplicitShowHide)


def theme_color(name: str):
    from PySide6.QtGui import QColor
    return QColor(getattr(theme, name) or '#888888')


def mini_button(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName('MiniButton')
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def copy_to_clipboard(text: str, b: QPushButton | None = None) -> None:
    QApplication.clipboard().setText(text)
    if b is not None:
        old = b.text()
        b.setText('已複製')
        QTimer.singleShot(1200, b, lambda: b.setText(old) if b.text() == '已複製' else None)
