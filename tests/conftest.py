import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def ui_app():
    """Opt-in Qt setup; importing conftest alone must not import the GUI."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def theme_state(ui_app):
    from agentdock.ui import theme
    previous = theme.NAME
    yield theme
    theme.apply(previous)
