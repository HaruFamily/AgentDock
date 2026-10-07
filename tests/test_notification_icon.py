import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from agentdock.ui.card import FloatingCard


def test_notification_animation_stops_when_hidden_read_or_expanded():
    app = QApplication.instance() or QApplication([])
    icon = FloatingCard()
    icon.set_mode('heart')
    icon.set_attention(True)
    assert not icon._ripple_timer.isActive()
    icon.show()
    app.processEvents()
    assert icon._ripple_timer.isActive()
    icon._ripple_started -= 0.35
    first = icon.grab().toImage()
    icon._ripple_started -= 0.35
    second = icon.grab().toImage()
    assert first != second
    icon.hide()
    assert not icon._ripple_timer.isActive()
    icon.show()
    icon.set_attention(False)
    assert not icon._ripple_timer.isActive()
    icon.set_pending(1)
    assert icon._ripple_timer.isActive()
    icon.set_mode('card')
    assert not icon._ripple_timer.isActive()
    icon.set_mode('heart')
    assert icon._ripple_timer.isActive()
    icon.set_pending(0)
    assert not icon._ripple_timer.isActive()
    icon.close()
    icon.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
