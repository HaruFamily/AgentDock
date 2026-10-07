from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication

from agentdock.ui.card import FloatingCard


def pointer(x, y):
    return SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton,
                           globalPosition=lambda: QPointF(x, y))


def test_layered_resize_coalesces_and_flushes_final_pointer(monkeypatch):
    from agentdock.ui import card
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(card.sys, 'platform', 'win32')
    window = FloatingCard()
    window.set_mode('card')
    window.setGeometry(100, 100, 600, 500)
    native = Mock()
    monkeypatch.setattr(window, 'windowHandle', lambda: native)
    grip = next(g for g in window.grips if g.edges == Qt.Edge.RightEdge)
    grip.mousePressEvent(pointer(700, 350))
    native.startSystemResize.assert_not_called()
    for x in range(701, 900):
        grip.mouseMoveEvent(pointer(x, 350))
    assert window.width() == 600
    grip._resize_frame.stop()
    grip._flush_resize()
    assert window.width() == 799
    grip.mouseReleaseEvent(pointer(850, 350))
    assert window.width() == 750
    assert not grip._resize_frame.isActive()
    assert grip._next_geometry is None
    window.close()


def test_unchanged_attention_does_not_request_repaints(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = FloatingCard()
    repaint = Mock()
    monkeypatch.setattr(window, 'update', repaint)
    for _ in range(20):
        window.set_pending(0)
        window.set_attention(False)
    repaint.assert_not_called()
    window.set_attention(True)
    repaint.assert_called_once()
    window.close()
