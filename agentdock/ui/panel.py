"""The expanded floating panel: frameless, always on top, draggable header, resizable corner."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from agentdock import VERSION

SHADOW = 12
GRIP = 6  # px of the frame edge that resizes


class EdgeGrip(QWidget):
    """Invisible strip on the panel border; dragging it resizes the window natively (startSystemResize)."""

    CURSORS = {
        Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    }

    def __init__(self, panel: QWidget, edges: Qt.Edge) -> None:
        super().__init__(panel)
        self.panel = panel
        self.edges = edges
        diag = {Qt.Edge.TopEdge | Qt.Edge.LeftEdge, Qt.Edge.BottomEdge | Qt.Edge.RightEdge}
        if edges in self.CURSORS:
            self.setCursor(self.CURSORS[edges])
        else:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if edges in diag else Qt.CursorShape.SizeBDiagCursor)
        self._start = None

    def paintEvent(self, _e) -> None:  # noqa: N802
        # Nearly transparent but not fully: fully transparent pixels are click-through on Windows.
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        handle = self.panel.windowHandle()
        if handle is not None and handle.startSystemResize(self.edges):
            return
        self._start = (e.globalPosition().toPoint(), self.panel.geometry())  # fallback: manual resize

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if not self._start:
            return
        origin, geo = self._start
        d = e.globalPosition().toPoint() - origin
        g = QRect(geo)
        if self.edges & Qt.Edge.LeftEdge:
            g.setLeft(min(geo.left() + d.x(), geo.right() - self.panel.minimumWidth()))
        if self.edges & Qt.Edge.RightEdge:
            g.setRight(geo.right() + d.x())
        if self.edges & Qt.Edge.TopEdge:
            g.setTop(min(geo.top() + d.y(), geo.bottom() - self.panel.minimumHeight()))
        if self.edges & Qt.Edge.BottomEdge:
            g.setBottom(geo.bottom() + d.y())
        self.panel.setGeometry(g)

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        self._start = None
        self.panel.geometry_changed.emit()


class Header(QFrame):
    def __init__(self, panel: "Panel") -> None:
        super().__init__()
        self.setObjectName("Header")
        self.panel = panel
        self._press: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint() - self.panel.pos()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is not None:
            self.panel.move(e.globalPosition().toPoint() - self._press)

    def mouseReleaseEvent(self, _e) -> None:  # noqa: N802
        if self._press is not None:
            self._press = None
            self.panel.geometry_changed.emit()


class Panel(QWidget):
    geometry_changed = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("AgentDock")
        self.setMinimumSize(360, 420)
        self.resize(460, 640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        frame = QFrame()
        frame.setObjectName("PanelFrame")
        shadow = QGraphicsDropShadowEffect(blurRadius=24, offset=QPoint(0, 4), color=QColor(20, 35, 60, 60))
        frame.setGraphicsEffect(shadow)
        outer.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = Header(self)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 8, 8, 8)
        title = QLabel("AgentDock")
        title.setObjectName("Title")
        hl.addWidget(title)
        version = QLabel(VERSION)
        version.setObjectName("Muted")
        hl.addWidget(version)
        hl.addStretch()
        close = QPushButton("—")
        close.setProperty("flat", True)
        close.setToolTip("收回小球")
        close.clicked.connect(self.hide)
        hl.addWidget(close)
        lay.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs, 1)
        E = Qt.Edge
        self.grips = [EdgeGrip(self, edges) for edges in (
            E.LeftEdge, E.RightEdge, E.TopEdge, E.BottomEdge, E.TopEdge | E.LeftEdge, E.TopEdge | E.RightEdge,
            E.BottomEdge | E.LeftEdge, E.BottomEdge | E.RightEdge)]


    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_grips()
        self.geometry_changed.emit()

    def _place_grips(self) -> None:
        """Grips sit on the visible frame border (inside the transparent shadow margin)."""
        E = Qt.Edge
        x0, y0 = SHADOW - GRIP // 2, SHADOW - GRIP // 2
        x1, y1 = self.width() - SHADOW - GRIP // 2, self.height() - SHADOW - GRIP // 2
        w, h, c = x1 - x0, y1 - y0, GRIP * 3
        rects = {
            E.LeftEdge: QRect(x0, y0 + c, GRIP, h - 2 * c), E.RightEdge: QRect(x1, y0 + c, GRIP, h - 2 * c),
            E.TopEdge: QRect(x0 + c, y0, w - 2 * c, GRIP), E.BottomEdge: QRect(x0 + c, y1, w - 2 * c, GRIP),
            E.TopEdge | E.LeftEdge: QRect(x0, y0, c, c), E.TopEdge | E.RightEdge: QRect(x1 + GRIP - c, y0, c, c),
            E.BottomEdge | E.LeftEdge: QRect(x0, y1 + GRIP - c, c, c),
            E.BottomEdge | E.RightEdge: QRect(x1 + GRIP - c, y1 + GRIP - c, c, c),
        }
        for grip in self.grips:
            grip.setGeometry(rects[grip.edges])
            grip.raise_()

    def place_near(self, ball: QRect) -> None:
        """Open beside the ball, on whichever side has room, clamped to the screen."""
        screen = QApplication.screenAt(ball.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = ball.left() - w + SHADOW if ball.center().x() > area.center().x() else ball.right() - SHADOW
        y = ball.top() - SHADOW
        x = min(max(x, area.left() - SHADOW), area.right() - w + SHADOW)
        y = min(max(y, area.top() - SHADOW), area.bottom() - h + SHADOW)
        self.move(x, y)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(460, 640)
