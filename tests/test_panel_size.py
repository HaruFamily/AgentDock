import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from agentdock.ui.card import FloatingCard


def test_content_updates_and_page_switch_keep_manual_size():
    app = QApplication.instance() or QApplication([])
    card = FloatingCard()
    body = QWidget()
    layout = QVBoxLayout(body)
    card.add_tool_page("tool:test", "Test", body)
    card.show()
    card.resize(500, 420)
    QTest.qWait(600)
    size = card.size()
    card.set_page("tool:test")
    for i in range(70):
        layout.addWidget(QLabel(f"Row {i}"))
    QTest.qWait(600)
    assert card.size() == size
    assert card.pages["tool:test"].verticalScrollBar().maximum() > 0
    card.set_page("quota")
    QTest.qWait(600)
    assert card.size() == size
    card.set_mode("heart")
    card.set_mode("card")
    assert card.size() == size
    card.close()
