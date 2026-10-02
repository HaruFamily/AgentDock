"""Always-on-top floating ball. Click to open the panel, drag to move, right-click for menu."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from agentdock.ui import theme

SIZE = 58
MARGIN = 10  # room for the glow


class FloatingBall(QWidget):
    clicked = Signal()
    moved = Signal(QPoint)
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE + MARGIN * 2, SIZE + MARGIN * 2)
        self.setToolTip("AgentDock：點一下展開，拖曳移動，右鍵選單")
        self.pending = 0
        self._glow = 0.0
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False
        # Low-cost pulse: ~12 repaints/s for a few seconds after a new question, then a steady glow.
        self._pulse = QTimer(self, interval=80)
        self._pulse.timeout.connect(self._on_pulse)
        self._phase = 0

    # -- state -----------------------------------------------------------------
    def set_pending(self, count: int) -> None:
        self.pending = count
        if count > getattr(self, "_last_count", 0):
            self._phase = 0
            self._pulse.start()
        elif not count:
            self._pulse.stop()
            self._glow = 0.0
        self._last_count = count
        self.setToolTip(f"AgentDock：{count} 題待回答" if count else "AgentDock：點一下展開，拖曳移動，右鍵選單")
        self.update()

    def _on_pulse(self) -> None:
        self._phase += 1
        cycle = (self._phase % 18) / 18  # 1.44 s per pulse
        self._glow = 1 - abs(cycle * 2 - 1)
        if self._phase >= 18 * 4:  # 4 pulses, then stay lit without repainting
            self._pulse.stop()
            self._glow = 0.6
        self.update()

    def center(self) -> QPoint:
        return self.frameGeometry().center()

    def keep_on_screen(self) -> None:
        screen = QApplication.screenAt(self.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(self.x(), area.left() - MARGIN), area.right() - self.width() + MARGIN)
        y = min(max(self.y(), area.top() - MARGIN), area.bottom() - self.height() + MARGIN)
        self.move(x, y)

    # -- painting --------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = SIZE / 2
        base = QColor(theme.ALERT if self.pending else theme.ACCENT)
        if self.pending:
            glow = QColor(base)
            glow.setAlphaF(0.18 + 0.32 * self._glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(c, r + 3 + 6 * self._glow, r + 3 + 6 * self._glow)
        shadow = QColor(0, 0, 0, 40)
        p.setBrush(shadow)
        p.drawEllipse(c + QPointF(0, 2), r, r)
        grad = QRadialGradient(c - QPointF(r / 3, r / 3), r * 1.6)
        grad.setColorAt(0, base.lighter(130))
        grad.setColorAt(1, base)
        p.setBrush(grad)
        p.setPen(QPen(QColor(255, 255, 255, 200), 2))
        p.drawEllipse(c, r, r)
        # bow-tie mark (⋈) drawn as two triangles
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        w, h = r * 0.62, r * 0.5
        path = QPainterPath()
        path.moveTo(c.x() - w, c.y() - h); path.lineTo(c.x(), c.y()); path.lineTo(c.x() - w, c.y() + h); path.closeSubpath()
        path.moveTo(c.x() + w, c.y() - h); path.lineTo(c.x(), c.y()); path.lineTo(c.x() + w, c.y() + h); path.closeSubpath()
        p.drawPath(path)
        if self.pending:
            badge = QRectF(self.width() - MARGIN - 22, MARGIN - 4, 24, 20)
            p.setBrush(QColor(theme.DANGER))
            p.setPen(QPen(QColor("white"), 1.5))
            p.drawRoundedRect(badge, 10, 10)
            font = QFont(p.font()); font.setBold(True); font.setPixelSize(12)
            p.setFont(font)
            p.setPen(QColor("white"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self.pending) if self.pending < 100 else "99+")

    # -- mouse -----------------------------------------------------------------
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
            self.keep_on_screen()
            self.moved.emit(self.pos())
        else:
            self.clicked.emit()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("展開／收合面板", self.clicked.emit)
        menu.addSeparator()
        menu.addAction("結束 AgentDock", self.quit_requested.emit)
        menu.exec(e.globalPos())
