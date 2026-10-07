import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from PySide6.QtTest import QTest
from agentdock.ui.app import LauncherIcon
from agentdock.ui.dock_layout import ContentWindow, DockLayout


def make_layout(ui=None):
    app = QApplication.instance() or QApplication([])
    icon, content = LauncherIcon(), ContentWindow()
    icon.set_mode('heart')
    icon.move(700, 220)
    content.set_qa(QPlainTextEdit())
    content.set_settings(QPlainTextEdit())
    state = ui if ui is not None else {}
    layout = DockLayout(icon, content, state, lambda: None)
    icon.show()
    app.processEvents()
    return app, icon, content, layout, state


def clean(icon, content, layout):
    layout.strip.close()
    icon.close()
    content.close()
    layout.strip.deleteLater()
    icon.deleteLater()
    content.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tabs_restore_content_sizes_and_draft():
    app, icon, content, layout, ui = make_layout()
    layout.show()
    assert layout.strip.isVisible() and not content.isVisible()
    layout.select('qa')
    content.qa_view.setPlainText('keep draft')
    content.resize(490, 460)
    layout.remember()
    qa_size = content.size()
    layout.select('settings')
    assert content.size() != qa_size
    layout.select('qa')
    assert content.size() == qa_size
    assert content.qa_view.toPlainText() == 'keep draft'
    content.qa_view.setPlainText('\n'.join(f'line {i}' for i in range(200)))
    app.processEvents()
    content.qa_view.verticalScrollBar().setValue(80)
    scroll = content.qa_view.verticalScrollBar().value()
    layout.select('settings')
    layout.select('qa')
    app.processEvents()
    assert content.qa_view.verticalScrollBar().value() == scroll
    layout.toggle()
    QTest.qWait(260)
    assert not content.isVisible() and not layout.strip.isVisible() and icon.isVisible()
    layout.toggle()
    assert content.isVisible() and layout.buttons['qa'].isChecked()
    assert content.size() == qa_size
    layout.select('qa')
    assert not content.isVisible() and layout.strip.isVisible()
    layout.toggle()
    layout.toggle()
    assert not content.isVisible() and layout.strip.isVisible()
    clean(icon, content, layout)


def test_integrated_base_animation_and_page_heights():
    app, icon, content, layout, ui = make_layout()
    layout.show()
    start = layout.strip.reveal_rect.width()
    native_geometry = layout.strip.geometry()
    QTest.qWait(110)
    middle = layout.strip.reveal_rect.width()
    QTest.qWait(160)
    assert start < middle < layout.strip.reveal_rect.width()
    assert layout.strip.geometry() == native_geometry
    assert layout.strip.geometry().contains(icon.geometry().center())
    layout.select('qa')
    inbox_height = content.height()
    layout.select('settings')
    assert inbox_height > content.height()
    layout.hide()
    QTest.qWait(80)
    assert layout.strip.isVisible()
    layout.show()  # rapid reversal finishes expanded
    QTest.qWait(260)
    assert layout.strip.isVisible() and layout.progress == 1
    icon.move(0, 220)
    layout.place()
    assert layout.strip.x() < icon.geometry().center().x() < layout.strip.geometry().right()
    assert QApplication.primaryScreen().availableGeometry().contains(layout.strip.geometry())
    layout.hide()
    QTest.qWait(260)
    assert not layout.strip.isVisible()
    clean(icon, content, layout)


def test_vertical_orientation_preserves_content_and_stays_on_screen():
    app, icon, content, layout, ui = make_layout()
    layout.select('qa')
    content.qa_view.setPlainText('draft survives direction change')
    layout.set_orientation('vertical')
    app.processEvents()
    assert ui['dock_orientation'] == 'vertical'
    assert layout.buttons['quota'].y() < layout.buttons['qa'].y() < layout.buttons['settings'].y()
    area = QApplication.primaryScreen().availableGeometry()
    for pos in (QPoint(area.left(), area.top()), QPoint(area.right() - 68, area.bottom() - 68)):
        icon.move(pos)
        layout.place()
        geometry = layout.strip.geometry()
        for progress in (0, .4, 1):
            layout.animate_frame(progress)
            assert layout.strip.geometry() == geometry
            assert not layout.strip.mask().contains(layout.strip.mapFromGlobal(icon.geometry().center()))
        assert area.contains(layout.strip.geometry())
        assert area.contains(content.geometry())
        assert not content.geometry().intersects(layout.strip.geometry())
    layout.hide()
    layout.show()
    assert content.qa_view.toPlainText() == 'draft survives direction change'
    saved = copy.deepcopy(ui)
    layout.set_orientation('horizontal')
    assert content.isVisible() and layout.page == 'qa'
    clean(icon, content, layout)
    app, icon, content, layout, ui = make_layout(saved)
    assert layout.orientation == 'vertical'
    clean(icon, content, layout)


def test_launcher_remains_clickable_when_strip_is_raised():
    import sys
    if sys.platform == 'win32' and os.environ.get('AGENTDOCK_NATIVE_DOCK_TEST') != '1':
        import subprocess
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', __file__ + '::test_launcher_remains_clickable_when_strip_is_raised'],
            env={**os.environ, 'QT_QPA_PLATFORM': 'windows', 'AGENTDOCK_NATIVE_DOCK_TEST': '1'},
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        return
    app, icon, content, layout, ui = make_layout()
    icon.mode_changed.connect(lambda mode: layout.toggle() if mode == 'card' else None)
    center = QPoint(icon.width() // 2, icon.height() // 2 + 2)
    try:
        for x in (0, 700):
            icon.move(x, 220)
            QTest.mouseClick(icon, Qt.MouseButton.LeftButton, pos=center)
            assert layout.expanded
            for elapsed in (60, 140, 220):
                layout.animation.setCurrentTime(elapsed)
                layout.strip.raise_()  # reproduce reversed native stacking order
                app.processEvents()
                point = layout.strip.mapFromGlobal(icon.mapToGlobal(center))
                assert not layout.strip.mask().contains(point)
                if sys.platform == 'win32':
                    import ctypes
                    from ctypes import wintypes
                    user32 = ctypes.windll.user32
                    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
                    user32.WindowFromPoint.argtypes = [wintypes.POINT]
                    user32.WindowFromPoint.restype = wintypes.HWND
                    rect = wintypes.RECT()
                    user32.GetWindowRect(int(icon.winId()), ctypes.byref(rect))
                    hit = user32.WindowFromPoint(wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
                    assert hit == int(icon.winId()), 'Animated strip intercepted the launcher'
            layout.select('settings')
            QTest.mouseClick(icon, Qt.MouseButton.LeftButton, pos=center)
            QTest.qWait(260)
            assert not layout.expanded and not content.isVisible() and not layout.strip.isVisible()
            assert icon.isVisible()
    finally:
        clean(icon, content, layout)


def test_detach_attach_follow_and_persist():
    app, icon, content, layout, ui = make_layout()
    layout.select('qa')
    content.move(35, 45)
    content.drag_moved.emit()
    content.moved.emit(content.pos())
    assert layout.attach_button.isVisible()
    icon.move(60, 180)
    icon.drag_moved.emit()
    assert content.pos() == QPoint(35, 45)
    layout.hide()
    layout.show()
    assert content.pos() == QPoint(35, 45)
    layout.select('settings')
    layout.select('qa')
    assert content.pos() == QPoint(35, 45)
    saved = copy.deepcopy(ui)
    layout.attach()
    assert not layout.attach_button.isVisible()
    before = content.pos()
    icon.move(120, 240)
    icon.drag_moved.emit()
    assert content.pos() != before
    area = QApplication.primaryScreen().availableGeometry()
    assert area.contains(layout.strip.geometry())
    assert area.contains(content.geometry())
    assert not content.geometry().intersects(layout.strip.geometry())
    clean(icon, content, layout)
    app, icon, content, layout, ui = make_layout(saved)
    content.set_page(layout.page)
    layout.show()
    assert content.page == 'qa' and content.isVisible()
    assert content.pos() == QPoint(35, 45)
    clean(icon, content, layout)


def test_icon_restores_page_but_new_result_still_prioritizes_pending_question(tmp_path):
    from types import SimpleNamespace
    from agentdock.ui.app import Dock
    from agentdock.ui.inbox_view import InboxView
    from agentdock.qa.store import QuestionStore
    from test_agentchat import question, event, OWNER
    app = QApplication.instance() or QApplication([])
    store = QuestionStore(tmp_path)
    q = question(store)
    qa = InboxView(store)
    qa.open_question(q['id'])
    icon, content = LauncherIcon(), ContentWindow()
    icon.set_mode('heart')
    icon.move(700, 220)
    content.set_qa(qa)
    content.set_settings(QPlainTextEdit())
    layout = DockLayout(icon, content, {}, lambda: None)
    dock = SimpleNamespace(layout_controller=layout, qa=qa)
    dock._open_notification = lambda **kw: Dock._open_notification(dock, **kw)
    layout.select('settings')
    Dock.toggle_card(dock)
    assert not layout.expanded
    Dock.toggle_card(dock)
    assert layout.page == 'settings'  # an old question alone doesn't override restore
    Dock.toggle_card(dock)
    store.receive(OWNER, 'Codex', event(work_id='result'))
    Dock.toggle_card(dock)
    assert layout.page == 'qa'
    assert qa.rows[(OWNER, 'w')].foldouts[q['id']].toggle.isChecked()
    clean(icon, content, layout)


def test_dock_starts_with_only_icon_and_restores_selected_tab(tmp_path, monkeypatch):
    # A real app.exec() must not run callbacks left by other widget tests.
    if os.environ.get('AGENTDOCK_DOCK_TEST_CHILD') != '1':
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', '-q', __file__ + '::test_dock_starts_with_only_icon_and_restores_selected_tab'],
            env={**os.environ, 'AGENTDOCK_DOCK_TEST_CHILD': '1'},
            capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        return
    from PySide6.QtCore import QTimer
    from agentdock.ui import app as module
    from agentdock.tools import tokengauge
    app = QApplication.instance() or QApplication([])
    single_shot = QTimer.singleShot
    monkeypatch.setattr(QTimer, 'singleShot', staticmethod(
        lambda delay, *args: single_shot(delay, *args) if delay < 1000 else None))
    monkeypatch.setenv('AGENTDOCK_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'discover', lambda: [])
    monkeypatch.setattr(module.winutil, 'IS_WINDOWS', False)
    for method in ('_tray', '_keep_visible', '_launcher_checks'):
        monkeypatch.setattr(module.Dock, method, lambda self: None)
    monkeypatch.setattr(module.AgentsView, 'check_updates', lambda *a, **kw: None)
    def no_quota(*args):
        raise RuntimeError('Quota disabled in isolated UI test')
    monkeypatch.setattr(tokengauge, 'QuotaModel', no_quota)
    dock = module.Dock(app, True)
    assert dock.icon.isVisible() and not dock.ball.isVisible()
    assert not dock.layout_controller.strip.isVisible()
    dock.icon.set_mode('card')
    assert dock.layout_controller.strip.isVisible() and not dock.ball.isVisible()
    dock.layout_controller.buttons['settings'].click()
    assert dock.ball.isVisible() and dock.ball.page == 'settings'
    dock.icon.set_mode('card')
    assert not dock.ball.isVisible() and dock.icon.isVisible()
    dock.icon.set_mode('card')
    assert dock.ball.isVisible() and dock.ball.page == 'settings'
    QTimer.singleShot(0, dock.quit)
    app.exec()
    for timer in dock.findChildren(QTimer):
        timer.stop()
    dock.store.flush_reads()
    clean(dock.icon, dock.ball, dock.layout_controller)
    dock.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
