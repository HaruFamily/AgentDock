"""AgentDock's one and only window, in two sizes.

- 浮動視窗 (card): one page at a time — 額度 / 問答 / 設定 (Agent, MCP, extensions). Resizable from any edge.
  A new question switches to 問答 and goes back to where you were once it is answered.
- 小圖示 (heart): resting; no quota requests. Glows with a number when questions wait; a simple single-choice
  question pops up as a bubble next to it (answered with one click). Used automatically while a full-screen
  app (game, video) is in front — then nothing pops up.

Appearance is configured by theme.py and an optional local theme.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from agentdock.ui import theme

HEART, CARD = "heart", "card"
PILL = "pill"   # old saved state; treated as card
MARGIN = 12          # room for glow / shadow around the window
CARD_W = 400
CARD_H = 560
MIN_W, MIN_H = 340, 300
HEART_SIZE = 44
SNAP = 18


# ---------------------------------------------------------------------------- shapes
def level_color(used: float) -> QColor:
    return QColor(theme.DANGER if used >= 90 else theme.ALERT if used >= 70 else theme.OK)


def draw_round_icon(p, c, size):
    if theme.PRIVATE and theme.CUTE:
        theme.PRIVATE.draw_icon(p, c, size)
        return
    p.setBrush(QColor(theme.CARD))
    p.setPen(QPen(QColor(theme.ACCENT), max(1.0, size / 40)))
    p.drawEllipse(c, size / 2, size / 2)


def heart_icon(size: int):
    from PySide6.QtGui import QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_round_icon(p, QPointF(size / 2, size / 2), size * 0.9)
    p.end()
    return pm


# ---------------------------------------------------------------------------- small widgets
class Bar(QWidget):
    """A rounded bar. The ribbon look adds a soft gradient and a dot at the end."""

    def __init__(self, value: float, used: float) -> None:
        super().__init__()
        self.value = max(0.0, min(100.0, value))
        self.used = used
        self.setFixedHeight(14 if theme.CUTE else 10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = level_color(self.used)
        h = 8.0 if theme.CUTE else 6.0
        y = (self.height() - h) / 2
        pad = 5.0 if theme.CUTE else 0.0
        track = QRectF(pad, y, self.width() - pad * 2, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT_SOFT if theme.CUTE else theme.LINE))
        p.drawRoundedRect(track, h / 2, h / 2)
        w = track.width() * self.value / 100
        if w <= 0.5:
            return
        fill = QRectF(track.left(), y, max(w, h), h)
        if theme.CUTE:
            grad = QLinearGradient(fill.topLeft(), fill.topRight())
            grad.setColorAt(0, color.lighter(130))
            grad.setColorAt(1, color)
            p.setBrush(grad)
            p.drawRoundedRect(fill, h / 2, h / 2)
            p.fillRect(QRectF(fill.left() + 3, y + 1.5, max(fill.width() - 6, 0), 1.5), QColor(255, 255, 255, 120))
            cx = min(max(track.left() + w, track.left() + 5), track.right() - 5)
            p.setPen(QPen(QColor("white"), 1.2))
            p.setBrush(color)
            p.drawEllipse(QPointF(cx, self.height() / 2), 4.5, 4.5)
        else:
            p.setBrush(color)
            p.drawRoundedRect(fill, h / 2, h / 2)


class Clickable(QWidget):
    clicked = Signal()

    def __init__(self, tip: str = "", name: str = "CardLink") -> None:
        super().__init__()
        self.setObjectName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tip:
            self.setToolTip(tip)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


def card_style() -> str:
    t = theme
    accent_ink = "white" if t.CUTE else t.CANVAS
    return f"""
QLabel#CardTitle {{ color: {t.ACCENT_DEEP}; font-size: 16px; font-weight: 800; }}
QPushButton#CardTab {{ border: 1px solid {t.LINE}; background: {t.CARD}; color: {t.MUTED}; border-radius: {t.PILL}px;
                       padding: 5px 10px; font-size: 13px; font-weight: 700; min-height: 22px; }}
QPushButton#CardTab:checked {{ background: {t.ACCENT}; border-color: {t.ACCENT}; color: {accent_ink}; }}
QPushButton#CardTab:hover:!checked {{ background: {t.HOVER}; color: {t.ACCENT_DEEP}; }}
QPushButton#CardIcon {{ border: none; background: transparent; color: {t.ACCENT_DEEP}; font-size: 15px; font-weight: 800;
                        border-radius: 13px; min-width: 26px; min-height: 26px; padding: 0 4px; }}
QPushButton#CardIcon:hover {{ background: {t.HOVER}; }}
QLabel#CardMood {{ color: {t.ACCENT_DEEP}; font-size: 13px; font-weight: 700; }}
QLabel#CardMeta {{ color: {t.MUTED}; font-size: 11px; }}
QLabel#CardName {{ color: {t.INK}; font-size: 13px; font-weight: 700; }}
QLabel#CardPlan {{ color: {accent_ink}; background: {t.ACCENT}; border-radius: 7px; padding: 0 6px; font-size: 10px; font-weight: 700; }}
QLabel#CardKind {{ color: {t.MUTED}; font-size: 11px; font-weight: 700; }}
QLabel#CardPct {{ color: {t.INK}; font-size: 12px; font-weight: 700; }}
QLabel#CardErr {{ color: {t.DANGER}; font-size: 11px; }}
QWidget#CardLink {{ border-radius: 10px; }}
QWidget#CardLink:hover {{ background: {t.HOVER}; }}
QWidget#CardPage {{ background: transparent; }}
"""


# ---------------------------------------------------------------------------- effects
class HeartBurst(QWidget):
    """Small dots floating up and fading out after an answer is sent."""

    def __init__(self, at: QPoint, count: int = 14) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(180, 220)
        self.move(at.x() - 90, at.y() - 200)
        self.parts = [(random.uniform(30, 150), random.uniform(-25, 25), random.uniform(10, 20), random.uniform(0, 0.35),
                       random.choice([theme.ACCENT, theme.ACCENT_SOFT, theme.INK])) for _ in range(count)]
        self.t0 = time.monotonic()
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._step)
        self.timer.start()
        self.show()

    def _step(self) -> None:
        if time.monotonic() - self.t0 > 1.6:
            self.timer.stop()
            self.close()
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = time.monotonic() - self.t0
        for x, drift, size, delay, color in self.parts:
            k = (now - delay) / 1.2
            if not 0 <= k <= 1:
                continue
            c = QColor(color)
            c.setAlphaF(max(0.0, 1 - k))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(x + drift * math.sin(k * 6), 200 - k * 180), size * 0.15, size * 0.15)


class Bubble(QWidget):
    """A speech bubble with big option buttons for a simple single-choice question."""
    answered = Signal(str, str)   # question id, option id
    expand = Signal(str)
    closed = Signal()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(e)

    def __init__(self, q: dict[str, Any]) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.qid = q["id"]
        self.setStyleSheet(card_style() + f"""
QLabel#BubbleQ {{ color: {theme.INK}; font-size: 14px; font-weight: 700; }}
QPushButton#BubbleOpt {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_DEEP}; border: 1px solid {theme.LINE};
                         border-radius: {theme.PILL}px; padding: 7px 12px; font-size: 13px; font-weight: 700; text-align: left; }}
QPushButton#BubbleOpt:hover {{ background: {theme.ACCENT}; color: {'white' if theme.CUTE else theme.CANVAS}; border-color: {theme.ACCENT}; }}
""")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(MARGIN + 14, MARGIN + 12, MARGIN + 14, MARGIN + 24)
        lay.setSpacing(7)
        top = QHBoxLayout()
        mood = QLabel(theme.t("qa_waiting", source=q.get("source") or "Agent"))
        mood.setObjectName("CardMood")
        mood.setWordWrap(True)
        top.addWidget(mood, 1)
        close = QPushButton("×")
        close.setObjectName("CardIcon")
        close.setToolTip("稍後再回答")
        close.clicked.connect(self.close)
        top.addWidget(close)
        lay.addLayout(top)
        question = QLabel(q.get("question") or "")
        question.setObjectName("BubbleQ")
        question.setWordWrap(True)
        lay.addWidget(question)
        for opt in q.get("options", []):
            b = QPushButton(opt.get("label") or opt["id"])
            b.setObjectName("BubbleOpt")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if opt.get("description"):
                b.setToolTip(opt["description"])
            b.clicked.connect(lambda _=False, oid=opt["id"]: self.answered.emit(self.qid, oid))
            lay.addWidget(b)
        more = QPushButton(theme.t("bubble_more"))
        more.setObjectName("CardIcon")
        more.setStyleSheet("font-size: 12px; font-weight: 600;")
        more.clicked.connect(lambda: self.expand.emit(self.qid))
        lay.addWidget(more, 0, Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(330)
        self.adjustSize()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(MARGIN, MARGIN, self.width() - MARGIN * 2, self.height() - MARGIN * 2 - 10)
        path = QPainterPath()
        path.addRoundedRect(r, theme.RADIUS, theme.RADIUS)
        tail = QPainterPath()
        tail.moveTo(r.right() - 46, r.bottom() - 2)
        tail.lineTo(r.right() - 22, r.bottom() + 12)
        tail.lineTo(r.right() - 28, r.bottom() - 2)
        path = path.united(tail)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 25))
        p.drawPath(path.translated(0, 3))
        p.setBrush(QColor(theme.CARD))
        p.setPen(QPen(QColor(theme.LINE), 2))
        p.drawPath(path)
        if theme.CUTE:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT_SOFT))
            for i in range(3):
                p.drawEllipse(QPointF(r.right() - 12 - i * 8, r.bottom() - 8), 1.5, 1.5)


# ---------------------------------------------------------------------------- resizing
class EdgeGrip(QWidget):
    """Invisible strip on the window border; dragging it resizes the window natively (startSystemResize)."""

    CURSORS = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    }

    def __init__(self, win: QWidget, edges: Qt.Edge) -> None:
        super().__init__(win)
        self.win = win
        self.edges = edges
        diag = {Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.Edge.BottomEdge | Qt.Edge.RightEdge}
        if edges in self.CURSORS:
            self.setCursor(self.CURSORS[edges])
        else:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if edges in diag else Qt.CursorShape.SizeBDiagCursor)
        self._start = None

    def paintEvent(self, _e) -> None:  # noqa: N802
        # Nearly transparent but not fully: fully transparent pixels are click-through on Windows.
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        handle = self.win.windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            return
        self._start = (e.globalPosition().toPoint(), self.win.geometry())  # fallback: manual resize

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if not self._start:
            return
        origin, geo = self._start
        d = e.globalPosition().toPoint() - origin
        g = QRect(geo)
        if self.edges & Qt.Edge.LeftEdge:
            g.setLeft(min(geo.left() + d.x(), geo.right() - self.win.minimumWidth()))
        if self.edges & Qt.Edge.RightEdge:
            g.setRight(geo.right() + d.x())
        if self.edges & Qt.Edge.TopEdge:
            g.setTop(min(geo.top() + d.y(), geo.bottom() - self.win.minimumHeight()))
        if self.edges & Qt.Edge.BottomEdge:
            g.setBottom(geo.bottom() + d.y())
        self.win.setGeometry(g)

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._start = None
        self.win.geometry_changed.emit()


# ---------------------------------------------------------------------------- the window
class FloatingCard(QWidget):
    moved = Signal(QPoint)
    quit_requested = Signal()
    mode_changed = Signal(str)
    page_changed = Signal(str)
    theme_requested = Signal(str)
    bubble_answer = Signal(str, str)     # question id, option id
    open_question = Signal(str)          # show this question on the 問答 page ("" = first pending)
    geometry_changed = Signal()          # resized by the user (EdgeGrip)
    bg_opacity_changed = Signal(float)   # background opacity picked in the menu
    autostart_requested = Signal(bool)   # 開機自動啟動 on/off
    toast_requested = Signal(bool)       # 新問題通知 on/off

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.mode = CARD
        self.page = "quota"
        self.return_page: str | None = None      # page to go back to after answering
        self.rest_from: str | None = None        # mode before an automatic rest (full-screen app)
        self.pending = 0
        self.questions: list[dict[str, Any]] = []
        self.summary = ("", False)
        self.quota = None
        self.bubble: Bubble | None = None
        self.bg_opacity = 1.0                    # card background only; text and bars stay solid
        self.autostart_state: Callable[[], bool] | None = None   # set by app.py on Windows
        self.toast_state: Callable[[], bool] | None = None       # set by app.py
        self._glow = 0.0
        self._phase = 0
        self._twinkle = 0
        self._hover = False
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False
        self._pulse = QTimer(self, interval=60)
        self._pulse.timeout.connect(self._on_pulse)
        self._twinkle_timer = QTimer(self, interval=700)
        self._twinkle_timer.timeout.connect(self._on_twinkle)

        self.root = QVBoxLayout(self)
        self.root.setSpacing(8)
        self.header = QWidget()
        hv = QVBoxLayout(self.header)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(8)
        self.tabs_row = QHBoxLayout()
        self.tabs_row.setSpacing(6)
        self.tab_buttons: dict[str, QPushButton] = {}
        for key in ("quota", "qa", "settings"):
            b = QPushButton()
            b.setObjectName("CardTab")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_page(k, by_user=True))
            self.tabs_row.addWidget(b, 1)
            self.tab_buttons[key] = b
        hv.addLayout(self.tabs_row)
        self.root.addWidget(self.header)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self._add_page("quota", self._quota_page())
        self.root.addWidget(self.stack, 1)
        self.card_size = (CARD_W, CARD_H)
        E = Qt.Edge
        self.grips = [EdgeGrip(self, edges) for edges in (
            E.LeftEdge, E.RightEdge, E.TopEdge, E.BottomEdge, E.TopEdge | E.LeftEdge, E.TopEdge | E.RightEdge,
            E.BottomEdge | E.LeftEdge, E.BottomEdge | E.RightEdge)]
        self.geometry_changed.connect(self._remember_size)
        self._size_timer = QTimer(self, singleShot=True, interval=500)
        self._size_timer.timeout.connect(self._remember_size)
        self.restyle()

    # ------------------------------------------------------------------ building
    def _add_page(self, key: str, widget: QWidget) -> None:
        self.pages[key] = widget
        self.stack.addWidget(widget)
        if key == self.page:
            self.stack.setCurrentWidget(widget)

    def add_tool_page(self, key: str, title: str, widget: QWidget) -> None:
        b = QPushButton(title)
        b.setObjectName("CardTab")
        b.setCheckable(True)
        b.clicked.connect(lambda _=False, k=key: self.set_page(k, by_user=True))
        self.tabs_row.addWidget(b, 1)
        self.tab_buttons[key] = b
        self._add_page(key, widget)

    def set_qa(self, view: QWidget) -> None:
        self.qa_view = view
        holder = QWidget()
        holder.setObjectName("CardPage")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        self._add_page("qa", holder)
        self._paint_tabs()

    def set_settings(self, view: QWidget) -> None:
        holder = QWidget()
        holder.setObjectName("CardPage")
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(view, 1)
        self._add_page("settings", holder)
        self._paint_tabs()

    def _quota_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("CardPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(6, 0, 2, 0)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        self.mood = QLabel("")
        self.mood.setObjectName("CardMood")
        self.updated = QLabel("")
        self.updated.setObjectName("CardMeta")
        texts.addWidget(self.mood)
        texts.addWidget(self.updated)
        head.addLayout(texts, 1)
        refresh = QPushButton("⟳")
        refresh.setObjectName("CardIcon")
        refresh.setToolTip("立即更新額度")
        refresh.clicked.connect(lambda: self.quota and self.quota.collect())
        head.addWidget(refresh)
        lay.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quota_body = QWidget()
        self.quota_lay = QVBoxLayout(self.quota_body)
        self.quota_lay.setContentsMargins(2, 2, 6, 2)
        self.quota_lay.setSpacing(2)
        scroll.setWidget(self.quota_body)
        lay.addWidget(scroll, 1)
        return page

    def restyle(self) -> None:
        """After a theme switch (also first build)."""
        self.setStyleSheet(card_style())
        self._paint_tabs()
        self._twinkle_timer.stop()
        self.render_quota()
        self._apply_mode()

    def _paint_tabs(self) -> None:
        names = {"quota": theme.t("tab_quota"), "qa": theme.t("tab_qa"), "settings": theme.t("tab_settings")}
        for key, b in self.tab_buttons.items():
            if key in names:
                text = names[key]
                if key == "qa" and self.pending:
                    text += f" {self.pending}"
                if key == "settings" and self.summary[1]:
                    text += " •"
                b.setText(text)
            b.setChecked(key == self.page)
            b.setVisible(key in self.pages)
            if key == "settings":
                b.setToolTip("Agent、MCP 與擴充" + (f"\n{self.summary[0]}" if self.summary[0] else ""))

    # ------------------------------------------------------------------ state from app.py
    def set_pending(self, count: int) -> None:
        if count > self.pending:
            self._phase = 0
            self._pulse.start()
        elif not count:
            self._pulse.stop()
            self._glow = 0.0
        self.pending = count
        self.setToolTip(f"AgentDock：{count} 題待回答" if count else "")
        self._paint_tabs()
        self.update()

    def set_questions(self, questions: list[dict[str, Any]]) -> None:
        self.questions = questions
        if not questions:
            if self.bubble is not None:
                self.bubble.close()
            if self.mode == CARD and self.page == "qa" and self.return_page:
                back, self.return_page = self.return_page, None
                QTimer.singleShot(900, lambda: self.page == "qa" and not self.questions and self.set_page(back))

    def set_agent_summary(self, text: str, alert: bool = False) -> None:
        self.summary = (text, alert)
        self._paint_tabs()

    def set_quota(self, model) -> None:
        self.quota = model
        model.changed.connect(self.render_quota)
        model.set_active(self.mode != HEART)
        self.render_quota()

    @staticmethod
    def is_simple(q: dict[str, Any]) -> bool:
        return (q.get("mode") == "single" and not q.get("group") and not q.get("images")
                and 1 <= len(q.get("options") or []) <= 6)

    def question_arrived(self, q: dict[str, Any]) -> None:
        """Never steals focus. Icon: glows; a simple choice also pops up as a bubble (not during a full-screen
        app). Card: switch to 問答 and come back afterwards."""
        if self.mode == HEART:
            if self.is_simple(q) and not self.rest_from:
                self.show_bubble(q)
            return
        if self.page != "qa":
            self.return_page = self.page
            self.set_page("qa")
        self.open_question.emit("")

    def show_bubble(self, q: dict[str, Any]) -> None:
        if self.bubble is not None:
            if self.bubble.qid == q["id"]:
                return
            self.bubble.close()
        bubble = Bubble(q)
        self.bubble = bubble
        bubble.answered.connect(self._bubble_answered)
        bubble.expand.connect(self._bubble_expand)
        bubble.closed.connect(lambda b=bubble: self.bubble is b and setattr(self, "bubble", None))
        g = self.frameGeometry()   # above the icon, right-aligned; below it when there is no room
        screen = (QApplication.screenAt(g.center()) or QApplication.primaryScreen()).availableGeometry()
        x = min(max(g.right() - self.bubble.width() + MARGIN, screen.left()), screen.right() - self.bubble.width())
        y = g.top() - self.bubble.height() + MARGIN + 4
        if y < screen.top():
            y = g.bottom() - MARGIN
        self.bubble.move(x, y)
        self.bubble.show()

    def _bubble_answered(self, qid: str, oid: str) -> None:
        self.bubble_answer.emit(qid, oid)
        if self.bubble is not None:
            self.bubble.close()

    def _bubble_expand(self, qid: str) -> None:
        if self.bubble is not None:
            self.bubble.close()
        self.set_mode(CARD)
        self.return_page = self.page if self.page != "qa" else None
        self.set_page("qa")
        self.open_question.emit(qid)

    def celebrate(self) -> None:
        if theme.CUTE and self.isVisible():
            g = self.frameGeometry()
            HeartBurst(QPoint(g.center().x(), g.top() + 60))

    # ------------------------------------------------------------------ modes & pages
    def set_page(self, key: str, by_user: bool = False) -> None:
        if key not in self.pages:
            return
        if by_user:
            self.return_page = None
        self.page = key
        self.stack.setCurrentWidget(self.pages[key])
        self._paint_tabs()
        self.page_changed.emit(key)

    def set_mode(self, mode: str, auto: bool = False) -> None:
        if mode == PILL:
            mode = CARD
        if mode == self.mode:
            return
        if not auto:
            self.rest_from = None
        anchor = self.frameGeometry()
        self.mode = mode
        self._apply_mode()
        self.move(anchor.right() + 1 - self.width(), anchor.top())   # keep the right edge and the top
        self.keep_on_screen()
        if self.quota:
            self.quota.set_active(mode != HEART)
        self._twinkle_timer.stop()
        if mode == CARD and self.bubble is not None:
            self.bubble.close()
        self.mode_changed.emit(mode)

    def rest(self, on: bool) -> None:
        """Automatic rest while a full-screen app is in front; restores the previous size afterwards."""
        if on and self.mode != HEART:
            prev = self.mode
            self.set_mode(HEART, auto=True)
            self.rest_from = prev
        elif not on and self.rest_from:
            prev, self.rest_from = self.rest_from, None
            self.set_mode(prev, auto=True)

    def _apply_mode(self) -> None:
        card = self.mode == CARD
        self.header.setVisible(card)
        self.stack.setVisible(card)
        for g in self.grips:
            g.setVisible(card)
        if card:
            if theme.CUTE:
                self.root.setContentsMargins(MARGIN + 25, MARGIN + 25, MARGIN + 25, MARGIN + 32)
            else:
                self.root.setContentsMargins(MARGIN + 16, MARGIN + 20, MARGIN + 14, MARGIN + 18)
            screen = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
            w = max(MIN_W, min(self.card_size[0], screen.width() - 20))
            h = max(MIN_H, min(self.card_size[1], screen.height() - 20))
            self.setMinimumSize(MIN_W + MARGIN * 2, MIN_H + MARGIN * 2)
            self.setMaximumSize(16777215, 16777215)
            self.resize(w + MARGIN * 2, h + MARGIN * 2)
        else:
            self.root.setContentsMargins(0, 0, 0, 0)
            self.setMinimumSize(0, 0)
            self.setFixedSize(HEART_SIZE + MARGIN * 2, HEART_SIZE + MARGIN * 2)
        self.update()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        if hasattr(self, "grips"):
            self._place_grips()
        if self.mode == CARD and hasattr(self, "_size_timer"):
            self._size_timer.start()   # remember the size once the user stops dragging

    def _place_grips(self) -> None:
        from PySide6.QtCore import QRect
        E, g = Qt.Edge, 6
        x0, y0 = MARGIN - g // 2, MARGIN - g // 2
        x1, y1 = self.width() - MARGIN - g // 2, self.height() - MARGIN - g // 2
        w, h, c = x1 - x0, y1 - y0, g * 3
        rects = {
            E.LeftEdge: QRect(x0, y0 + c, g, h - 2 * c), E.RightEdge: QRect(x1, y0 + c, g, h - 2 * c),
            E.TopEdge: QRect(x0 + c, y0, w - 2 * c, g), E.BottomEdge: QRect(x0 + c, y1, w - 2 * c, g),
            E.TopEdge | E.LeftEdge: QRect(x0, y0, c, c), E.TopEdge | E.RightEdge: QRect(x1 + g - c, y0, c, c),
            E.BottomEdge | E.LeftEdge: QRect(x0, y1 + g - c, c, c), E.BottomEdge | E.RightEdge: QRect(x1 + g - c, y1 + g - c, c, c),
        }
        for grip in self.grips:
            grip.setGeometry(rects[grip.edges])
            grip.raise_()

    def _remember_size(self) -> None:
        if self.mode == CARD:
            self.card_size = (self.width() - MARGIN * 2, self.height() - MARGIN * 2)
            self.keep_on_screen()
            self.moved.emit(self.pos())

    # ------------------------------------------------------------------ quota
    def _tightest(self) -> list[tuple[str, float, float]]:
        """[(short name, remaining %, used %)] per provider, using its most-used window."""
        out = []
        if not self.quota:
            return out
        for r in self.quota.visible_results():
            windows = [w for a in r.accounts for w in a.windows]
            if windows:
                used = max(0.0, min(100.0, max(float(w.used) for w in windows)))
                out.append((r.name.split()[0], 100 - used, used))
        return out

    def render_quota(self) -> None:
        if not hasattr(self, "quota_lay"):
            return
        lay = self.quota_lay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        q = self.quota
        if q is None:
            return
        from agentdock.tools.tokengauge import gauge
        worst = max((u for _n, _l, u in self._tightest()), default=0.0)
        mood_key = "quota_bad" if worst >= 90 else "quota_warn" if worst >= 70 else "quota_ok"
        self.mood.setText(theme.t(mood_key))
        self.mood.setVisible(bool(self.mood.text()))
        when = time.strftime("%H:%M", time.localtime(q.last)) if q.last else ""
        self.updated.setText(theme.t("updating") if q.busy else (theme.t("updated", t=when) if when else theme.t("no_quota")))
        for r in q.visible_results():
            lay.addWidget(self._provider_row(r, False, gauge))
        lay.addStretch()

    def _provider_row(self, r, show_used: bool, gauge) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(1)
        head = QHBoxLayout()
        head.setContentsMargins(6, 5, 6, 2)
        head.setSpacing(6)
        name = QLabel(r.name)
        name.setObjectName("CardName")
        head.addWidget(name)
        for plan in sorted({a.plan for a in r.accounts if a.plan})[:2]:
            chip = QLabel(plan)
            chip.setObjectName("CardPlan")
            head.addWidget(chip)
        head.addStretch()
        v.addLayout(head)
        if r.error:
            err = QLabel(("（上次的數字）" if r.stale else "") + r.error)
            err.setObjectName("CardErr")
            err.setWordWrap(True)
            err.setContentsMargins(8, 0, 6, 2)
            v.addWidget(err)
        for a in r.accounts:
            if len(r.accounts) > 1 and a.label:
                sub = QLabel(a.label)
                sub.setObjectName("CardMeta")
                sub.setContentsMargins(8, 2, 0, 0)
                v.addWidget(sub)
            if a.note and not a.windows:
                note = QLabel(a.note)
                note.setObjectName("CardMeta")
                note.setContentsMargins(8, 0, 0, 0)
                v.addWidget(note)
            for w in a.windows:
                used = max(0.0, min(100.0, float(w.used)))
                value = used if show_used else 100 - used
                row = QHBoxLayout()
                row.setContentsMargins(8, 0, 6, 0)
                row.setSpacing(8)
                kind = QLabel(gauge.KIND_LABEL.get(w.kind, w.kind))
                kind.setObjectName("CardKind")
                kind.setFixedWidth(22)
                pct = QLabel(f"{value:.0f}%")
                pct.setObjectName("CardPct")
                pct.setFixedWidth(38)
                pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                left = QLabel(gauge.fmt_countdown(w.resets_at))
                left.setObjectName("CardMeta")
                left.setFixedWidth(50)
                left.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                left.setToolTip(theme.t("reset_in", t=gauge.fmt_countdown(w.resets_at)))
                row.addWidget(kind)
                row.addWidget(Bar(value, used), 1)
                row.addWidget(pct)
                row.addWidget(left)
                holder = QWidget()
                holder.setLayout(row)
                v.addWidget(holder)
        return box

    # ------------------------------------------------------------------ painting
    def _on_pulse(self) -> None:
        self._phase += 1
        cycle = (self._phase % 20) / 20
        self._glow = max(0.0, math.sin(cycle * math.pi * 4)) if cycle < 0.5 else 0.0  # ba-dum, ba-dum
        if self._phase >= 20 * 5:
            self._pulse.stop()
            self._glow = 0.5
        self.update()

    def _on_twinkle(self) -> None:
        self._twinkle += 1
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.mode == HEART:
            self._paint_heart(p)
        else:
            self._paint_card(p)

    def _glow_color(self) -> QColor:
        c = QColor(theme.ACCENT)
        c.setAlphaF(min(1.0, 0.15 + 0.3 * self._glow))
        return c

    def set_bg_opacity(self, value: float) -> None:
        self.bg_opacity = min(1.0, max(0.3, float(value)))
        self.update()

    def _paint_card(self, p: QPainter) -> None:
        p.setOpacity(self.bg_opacity)   # everything painted here is background; child widgets draw on top
        r = QRectF(MARGIN, MARGIN, self.width() - MARGIN * 2, self.height() - MARGIN * 2)
        radius = float(theme.RADIUS)
        if self.pending:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._glow_color())
            p.drawRoundedRect(r.adjusted(-5, -5, 5, 5), radius + 5, radius + 5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 22))
        p.drawRoundedRect(r.translated(0, 3), radius, radius)
        p.setBrush(QColor(theme.CANVAS))
        p.drawRoundedRect(r, radius, radius)
        if not theme.CUTE:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(theme.LINE), 1))
            p.drawRoundedRect(r, radius, radius)
            return
        theme.PRIVATE.draw_frame(p, r, radius)

    def _paint_heart(self, p: QPainter) -> None:
        c = QPointF(self.width() / 2, self.height() / 2 + 2)
        size = HEART_SIZE - 6
        beat = 1 + 0.08 * self._glow
        if self.pending:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._glow_color())
            p.drawEllipse(c, (size + 10) * beat / 2, (size + 10) * beat / 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 35))
        p.drawEllipse(c + QPointF(0, 2), size / 2, size / 2)
        draw_round_icon(p, c, size * beat)
        if self.pending:
            badge = QRectF(self.width() - MARGIN - 20, MARGIN - 4, 24, 20)
            p.setBrush(QColor(theme.DANGER))
            p.setPen(QPen(QColor("white"), 1.5))
            p.drawRoundedRect(badge, 10, 10)
            f = QFont(p.font())
            f.setBold(True)
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self.pending) if self.pending < 100 else "99+")

    # ------------------------------------------------------------------ position
    def center(self) -> QPoint:
        return self.frameGeometry().center()

    def keep_on_screen(self) -> None:
        screen = QApplication.screenAt(self.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left() - MARGIN), area.right() + 1 - self.width() + MARGIN)
        y = min(max(self.y(), area.top() - MARGIN), area.bottom() + 1 - self.height() + MARGIN)
        self.move(x, y)

    def snap(self) -> None:
        """Dropped near a screen edge: stick to it."""
        screen = QApplication.screenAt(self.center()) or QApplication.primaryScreen()
        a = screen.availableGeometry()
        x, y = self.x(), self.y()
        if abs(x + MARGIN - a.left()) < SNAP:
            x = a.left() - MARGIN
        if abs(x + self.width() - MARGIN - a.right() - 1) < SNAP:
            x = a.right() + 1 - self.width() + MARGIN
        if abs(y + MARGIN - a.top()) < SNAP:
            y = a.top() - MARGIN
        if abs(y + self.height() - MARGIN - a.bottom() - 1) < SNAP:
            y = a.bottom() + 1 - self.height() + MARGIN
        self.move(x, y)

    # ------------------------------------------------------------------ input
    def enterEvent(self, _e) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _e) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        if e.key() == Qt.Key.Key_Escape and self.mode == CARD:
            self.set_mode(HEART)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._origin = self.pos()
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None:
            return
        delta = e.globalPosition().toPoint() - self._press
        if self._dragging or delta.manhattanLength() > 4:
            self._dragging = True
            self.move(self._origin + delta)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self.snap()
            self.keep_on_screen()
            self.moved.emit(self.pos())
        elif self.mode == HEART:
            self.set_mode(CARD)
            if self.pending:
                self.set_page("qa")
                self.open_question.emit("")

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("隱藏浮動視窗" if self.mode == CARD else "顯示浮動視窗",
                       lambda: self.set_mode(HEART if self.mode == CARD else CARD))
        if self.quota is not None:
            q = self.quota
            cfg = q.config()
            poll = menu.addMenu("額度更新頻率")
            for mins in (5, 10, 15, 30, 60):
                act = poll.addAction(f"每 {mins} 分鐘", lambda v=mins: q.set_config(poll_minutes=v))
                act.setCheckable(True)
                act.setChecked(cfg.get("poll_minutes", 5) == mins)
        looks = menu.addMenu("外觀")
        for key, cfg in theme.THEMES.items():
            act = looks.addAction(cfg["label"], lambda k=key: self.theme_requested.emit(k))
            act.setCheckable(True)
            act.setChecked(key == theme.NAME)
        see = menu.addMenu("背景透明度")
        for pct in (0, 15, 30, 45, 60):
            value = 1 - pct / 100
            act = see.addAction("不透明" if pct == 0 else f"{pct}%", lambda v=value: (self.set_bg_opacity(v),
                                                                              self.bg_opacity_changed.emit(v)))
            act.setCheckable(True)
            act.setChecked(abs(self.bg_opacity - value) < 0.01)
        if self.toast_state is not None:
            toast = self.toast_state()
            act = menu.addAction("新問題通知（縮成小圖示時）", lambda: self.toast_requested.emit(not toast))
            act.setCheckable(True)
            act.setChecked(toast)
        if self.autostart_state is not None:
            on = self.autostart_state()
            act = menu.addAction("開機自動啟動", lambda: self.autostart_requested.emit(not on))
            act.setCheckable(True)
            act.setChecked(on)
        menu.addSeparator()
        menu.addAction("結束 AgentDock", self.quit_requested.emit)
        menu.exec(e.globalPos())
