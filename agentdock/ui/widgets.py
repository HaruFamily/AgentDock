"""Small shared widgets for the flat, list-style panels."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


def icon_button(text: str, tip: str, *, danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setProperty("glyph", True)
    if danger:
        b.setProperty("danger", True)
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Meta")
    return lab


def dot(tip: str) -> QLabel:
    lab = QLabel("●")
    lab.setObjectName("Dot")
    lab.setToolTip(tip)
    return lab


class ElidedLabel(QLabel):
    """Single-line label that shrinks with "…" instead of widening the panel."""

    def __init__(self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight) -> None:
        super().__init__()
        self._mode = mode
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text
        self.setToolTip(text)
        self._elide()

    def full_text(self) -> str:
        return self._full

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        hint.setWidth(self.fontMetrics().horizontalAdvance(self._full) + 4)
        return hint

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(24)
        return hint

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._elide()

    def _elide(self) -> None:
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, self._mode, max(self.width(), 24)))


DRAG_MIME = "application/x-agentdock-agent"


class Row(QWidget):
    """One line: name, optional dot + muted note, right-aligned actions."""

    def __init__(self, name: str, *, note: str = "", alert: str = "", dim: bool = False, strike: bool = False) -> None:
        super().__init__()
        self.setObjectName("Row")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(4, 3, 0, 3)
        self.lay.setSpacing(6)
        self.name = QLabel(name)
        self.name.setObjectName("RowNameDim" if dim else "RowName")
        if strike:
            font = self.name.font()
            font.setStrikeOut(True)
            self.name.setFont(font)
        self.lay.addWidget(self.name)
        if alert:
            self.lay.addWidget(dot(alert))
        self.note = ElidedLabel(note)
        self.note.setObjectName("Meta")
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.lay.addWidget(self.note, 1)

    def add(self, widget: QWidget) -> QWidget:
        self.lay.addWidget(widget)
        return widget
