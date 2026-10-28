"""Pieces of the settings page in the conversation window: the category list, cards, switches, look tiles.

Everything is painted here with the current look's colours; the local look (data/private_theme.py) may paint a
part itself through draw_settings_card / draw_settings_nav / draw_switch (see theme.decorate). Nothing here
knows about Agents or MCPs: agents_view.py builds the pages from these.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from agentdock.ui import theme
from agentdock.ui.qa_view import button

# Switch states (MCP / extension cells and plain on/off settings)
ON, OFF = "on", "off"
ADDING, REMOVING, PENDING = "adding", "removing", "pending"   # queued, not written yet
ALERT, MISSING, DISABLED = "alert", "missing", "disabled"     # on but out of date / removed by others / off in its config
SYSTEM, NA = "system", "na"                                   # managed by the client / cannot have it
LOCKED = (SYSTEM, NA)


def _c(name: str, fallback: str = "#888888") -> QColor:
    return QColor(getattr(theme, name, "") or fallback)


def _focus_ring(p: QPainter, widget: QWidget, radius: float = 8) -> None:
    if widget.hasFocus() and widget.isEnabled():
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c("ACCENT"), 1.5, Qt.PenStyle.DashLine))
        p.drawRoundedRect(QRectF(widget.rect()).adjusted(2, 2, -2, -2), radius, radius)
        p.restore()


def heart_path(c: QPointF, s: float) -> QPainterPath:
    path = QPainterPath()
    path.moveTo(c + QPointF(0, s * 0.35))
    path.cubicTo(c + QPointF(-s, -s * 0.3), c + QPointF(-s * 0.45, -s * 0.95), c + QPointF(0, -s * 0.4))
    path.cubicTo(c + QPointF(s * 0.45, -s * 0.95), c + QPointF(s, -s * 0.3), c + QPointF(0, s * 0.35))
    return path


def card_margins() -> tuple[int, int, int, int]:
    return (28, 28, 28, 30) if theme.decorated("settings_card") else (18, 16, 18, 16)


class Card(QFrame):
    """A rounded card; the sweet look trims it with lace."""

    def __init__(self, parent: QWidget | None = None, layout: str = "v") -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.lay = QVBoxLayout(self) if layout == "v" else QHBoxLayout(self)
        self.lay.setSpacing(10)
        self.restyle()

    def restyle(self) -> None:
        self.lay.setContentsMargins(*card_margins())
        self.update()

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        radius = float(getattr(theme, "RADIUS", 12) or 12)
        if theme.decorate("settings_card", p, r, radius):
            return
        p.setPen(QPen(_c("LINE"), 1))
        p.setBrush(_c("CARD", "#ffffff"))
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class NavButton(QPushButton):
    """One category in the settings list; a small count when something there needs a look."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setObjectName("SettingsNavItem")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.badge = 0

    def set_badge(self, n: int) -> None:
        if n != self.badge:
            self.badge = n
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(160, 40)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hovered = self.underMouse()
        if not theme.decorate("settings_nav", p, r, self.isChecked(), hovered):
            if self.isChecked() or hovered:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_c("ACCENT_SOFT") if self.isChecked() else _c("HOVER"))
                p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        font = QFont(self.font())
        font.setBold(self.isChecked())
        p.setFont(font)
        p.setPen(_c("ACCENT_DEEP") if self.isChecked() else _c("INK"))
        text_rect = r.adjusted(32 if theme.decorated("settings_nav") else 16, 0, -40, 0)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        _focus_ring(p, self, r.height() / 2)
        if self.badge:
            small = QFont(self.font())
            if small.pointSizeF() > 0:
                small.setPointSizeF(max(7.0, small.pointSizeF() * 0.8))
            else:
                small.setPixelSize(max(10, int(small.pixelSize() * 0.8)))
            small.setBold(True)
            p.setFont(small)
            text = str(self.badge) if self.badge < 100 else "99"
            width = max(20.0, p.fontMetrics().horizontalAdvance(text) + 12.0)
            pill = QRectF(r.right() - 12 - width, r.center().y() - 10, width, 20)
            p.setPen(QPen(QColor("white"), 1.4) if theme.CUTE else Qt.PenStyle.NoPen)
            p.setBrush(_c("ALERT", "#d08a2e"))
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(QColor("white"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)


class Switch(QAbstractButton):
    """A hand-drawn toggle. As a plain setting it is checkable (on/off); as an MCP cell it shows a state and
    the page decides what a click does."""

    def __init__(self, state: str = OFF, *, checkable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Switch")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)
        self.state = state
        self.set_state(state)

    def set_state(self, state: str) -> None:
        self.state = state
        self.setEnabled(state not in LOCKED)
        self.setCursor(Qt.CursorShape.ArrowCursor if state in LOCKED else Qt.CursorShape.PointingHandCursor)
        self.update()

    def current(self) -> str:
        if self.isCheckable():
            return ON if self.isChecked() else OFF
        return self.state

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        state = self.current()
        hovered = self.underMouse() and self.isEnabled()
        if not theme.decorate("switch", p, r, state, hovered):
            paint_switch(p, r, state, hovered)
        _focus_ring(p, self, r.height() / 2)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(46, 26)


def paint_switch(p: QPainter, r: QRectF, state: str, hovered: bool) -> None:
    """The plain-look switch (also used by the local look for parts it leaves alone)."""
    radius = r.height() / 2
    if state == NA:
        p.setPen(QPen(_c("DIM"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(r.center().x() - 6, r.center().y()), QPointF(r.center().x() + 6, r.center().y()))
        return
    right = state in (ON, ADDING, PENDING, ALERT, SYSTEM)
    fill = {ON: _c("ACCENT"), PENDING: _c("ACCENT"), ALERT: _c("ACCENT"), ADDING: _c("ACCENT_SOFT"),
            SYSTEM: _c("DIM"), DISABLED: _c("HOVER"), REMOVING: _c("HOVER"), MISSING: _c("HOVER")}.get(state, _c("HOVER"))
    if hovered:
        fill = fill.lighter(108) if right else fill.darker(104)
    dashed = state in (ADDING, REMOVING, PENDING, MISSING)
    edge = {ADDING: _c("ACCENT"), REMOVING: _c("DANGER"), PENDING: _c("ALERT"), MISSING: _c("ALERT"),
            ALERT: _c("ALERT")}.get(state, _c("LINE") if not right else fill)
    p.setBrush(fill)
    p.setPen(QPen(edge, 1.6 if dashed or state == ALERT else 1,
                  Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
    p.drawRoundedRect(r, radius, radius)
    knob = r.height() - 6
    x = r.right() - 3 - knob if right else r.left() + 3
    k = QRectF(x, r.top() + 3, knob, knob)
    p.setPen(QPen(_c("LINE"), 1) if not right else Qt.PenStyle.NoPen)
    p.setBrush(_c("DIM") if state == DISABLED else QColor("#ffffff"))
    if theme.CUTE:
        p.drawPath(heart_path(k.center() + QPointF(0, knob * 0.12), knob * 0.62))
    else:
        p.drawEllipse(k)
    if state == ALERT:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_c("ALERT"))
        p.drawEllipse(QPointF(r.left() + radius, r.center().y()), 3.2, 3.2)


class ThemeTile(QAbstractButton):
    """A look to pick, shown as a tiny window in its own colours."""

    def __init__(self, key: str, config: dict) -> None:
        super().__init__()
        self.key, self.config = key, config
        self.setObjectName("ThemeTile")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(188, 150)
        self.setText(config.get("label", key))

    def paintEvent(self, _e) -> None:  # noqa: N802
        c = self.config
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        ring = _c("ACCENT") if self.isChecked() else (_c("LINE").darker(110) if self.underMouse() else _c("LINE"))
        p.setPen(QPen(ring, 2.4 if self.isChecked() else 1.2))
        p.setBrush(_c("CARD", "#ffffff"))
        p.drawRoundedRect(r, 18, 18)
        shot = QRectF(r.left() + 10, r.top() + 10, r.width() - 20, r.height() - 48)
        p.setPen(QPen(QColor(c.get("LINE", "#cccccc")), 1))
        p.setBrush(QColor(c.get("CANVAS", "#ffffff")))
        radius = min(14.0, float(c.get("RADIUS", 12)) * 0.6)
        p.drawRoundedRect(shot, radius, radius)
        side = QRectF(shot.left() + 7, shot.top() + 7, shot.width() * 0.28, shot.height() - 14)
        p.setBrush(QColor(c.get("CARD", "#ffffff")))
        p.drawRoundedRect(side, radius * 0.6, radius * 0.6)
        bubble = QRectF(side.right() + 9, shot.top() + 12, shot.width() * 0.5, 14)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.get("ACCENT_SOFT", "#eeeeee")))
        p.drawRoundedRect(bubble, 7, 7)
        bar = QRectF(side.right() + 9, shot.bottom() - 26, shot.right() - side.right() - 16, 18)
        p.setPen(QPen(QColor(c.get("LINE", "#cccccc")), 1))
        p.setBrush(QColor(c.get("CARD", "#ffffff")))
        p.drawRoundedRect(bar, 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(c.get("ACCENT", "#888888")))
        if c.get("CUTE"):
            p.drawPath(heart_path(QPointF(bar.right() - 10, bar.center().y() + 1), 7))
            p.setBrush(QColor(c.get("HEADER_A", c.get("ACCENT_SOFT", "#eeeeee"))))
            for fx, fy, s in ((0.9, 0.2, 6), (0.62, 0.55, 4.5)):
                p.drawPath(heart_path(QPointF(shot.left() + shot.width() * fx, shot.top() + shot.height() * fy), s))
        else:
            p.drawEllipse(QPointF(bar.right() - 10, bar.center().y()), 5, 5)
        for i in range(3):
            p.setBrush(QColor(c.get("LINE", "#cccccc")))
            p.drawRoundedRect(QRectF(side.left() + 6, side.top() + 8 + i * 11, side.width() - 12, 5), 2.5, 2.5)
        font = QFont(self.font())
        font.setBold(self.isChecked())
        p.setFont(font)
        p.setPen(_c("ACCENT_DEEP") if self.isChecked() else _c("INK"))
        p.drawText(QRectF(r.left(), shot.bottom() + 4, r.width(), r.bottom() - shot.bottom() - 4),
                   Qt.AlignmentFlag.AlignCenter, self.text())
        _focus_ring(p, self, 18)


def setting_row(title: str, control: QWidget, note: str = "") -> QWidget:
    """A line of a settings card: the name (and a short fact) on the left, its control on the right."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(12)
    text = QVBoxLayout()
    text.setSpacing(1)
    name = QLabel(title)
    name.setObjectName("SettingName")
    text.addWidget(name)
    if note:
        small = QLabel(note)
        small.setObjectName("Meta")
        small.setWordWrap(True)
        text.addWidget(small)
    lay.addLayout(text, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def page_heading(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("SettingsHeading")
    return lab


class GeneralPage(QWidget):
    """一般: what the tray menu also offers, as switches. `items`: (key, title, on, toggled)."""

    def __init__(self, items: list[tuple[str, str, bool, Callable[[bool], None]]],
                 restart: Callable[[], None] | None = None) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.card = Card()
        self.switches: dict[str, Switch] = {}
        for key, title, on, toggled in items:
            switch = Switch(checkable=True)
            switch.setChecked(on)
            switch.toggled.connect(toggled)
            self.switches[key] = switch
            self.card.lay.addWidget(setting_row(title, switch))
        lay.addWidget(self.card)
        self.cards = [self.card]
        if restart is not None:
            self.restart_button = button("重新啟動")
            self.restart_button.clicked.connect(restart)
            self.card.lay.addWidget(setting_row("AgentDock", self.restart_button))
        lay.addStretch(1)

    def set_on(self, key: str, on: bool) -> None:
        switch = self.switches.get(key)
        if switch is not None and switch.isChecked() != on:
            switch.blockSignals(True)
            switch.setChecked(on)
            switch.blockSignals(False)
            switch.update()

    def restyle(self) -> None:
        for card in self.cards:
            card.restyle()


class LookPage(QWidget):
    """外觀: one tile per look and the text size; picking applies at once."""
    picked = Signal(str)
    size_picked = Signal(str)

    def __init__(self, looks: dict[str, dict], current: str, sizes: dict[str, str] | None = None,
                 size: str = "") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.card = Card()
        self.row = QHBoxLayout()
        self.row.setSpacing(16)
        self.card.lay.addLayout(self.row)
        lay.addWidget(self.card)
        self.size_buttons: dict[str, QPushButton] = {}
        self.size_card = Card()
        if sizes:
            holder = QWidget()
            seg = QHBoxLayout(holder)
            seg.setContentsMargins(0, 0, 0, 0)
            seg.setSpacing(6)
            for key, text in sizes.items():
                b = QPushButton(text)
                b.setCheckable(True)
                b.setAutoExclusive(True)
                b.setProperty("seg", True)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setChecked(key == size)
                b.clicked.connect(lambda _c=False, k=key: self.size_picked.emit(k))
                seg.addWidget(b)
                self.size_buttons[key] = b
            self.size_card.lay.addWidget(setting_row("文字大小", holder))
            lay.addWidget(self.size_card)
        lay.addStretch(1)
        self.tiles: dict[str, ThemeTile] = {}
        self.set_looks(looks, current)

    def set_looks(self, looks: dict[str, dict], current: str) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.tiles = {}
        for key, config in looks.items():
            tile = ThemeTile(key, config)
            tile.setChecked(key == current)
            tile.clicked.connect(lambda _c=False, k=key: self.picked.emit(k))
            self.row.addWidget(tile)
            self.tiles[key] = tile
        self.row.addStretch(1)

    def set_current(self, key: str) -> None:
        for name, tile in self.tiles.items():
            tile.setChecked(name == key)

    def restyle(self) -> None:
        self.card.restyle()
        self.size_card.restyle()
        for tile in self.tiles.values():
            tile.update()


class SettingsNav(QWidget):
    """The category column: the same lace card as the conversation list in the sweet look."""

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme.decorate("panel", p, QRectF(self.rect()), float(getattr(theme, "RADIUS", 12) or 12))
