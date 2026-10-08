import os
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QLineEdit
from PySide6.QtTest import QSignalSpy

from agentdock.qa.store import QuestionStore
from agentdock.ui.app import LauncherIcon
from agentdock.ui.inbox_view import InboxView
from agentdock.ui.notifications import NotificationController, notice_for
from test_agentchat import OWNER, event, question
from test_activity import observation


def test_priority_navigation_and_permission_lifecycle(tmp_path, ui_app):
    store = QuestionStore(tmp_path)
    store.receive(OWNER, 'Codex', event(kind='failed', work_id='failed'))
    store.receive(OWNER, 'Codex', event(work_id='done'))
    q = question(store)
    store.activity.receive(OWNER, observation('approval', request_id='p1'))
    notice = notice_for(store.inbox_tasks())
    assert notice.text == '待授權 1 · 待回答 1 · 失敗 1 · 已完成 1'
    view = InboxView(store)
    target = view.notification_target()
    native = next(t for t in store.inbox_tasks() if t['state'] == 'approval')
    assert target == ('task', OWNER, native['work_id'])
    store.mark_read(OWNER, native['work_id'], [native['latest']['id']])
    assert notice_for(store.inbox_tasks()).kind == 'approval'
    # Unrelated native events must neither clear nor re-notify an approval.
    store.activity.receive(OWNER, observation('running'))
    assert notice_for(store.inbox_tasks()).action_keys == notice.action_keys
    store.activity.receive(OWNER, observation('approval_done', request_id='p1'))
    assert view.notification_target() == ('question', q['id'])
    store.cancel(q['id'])
    assert notice_for(store.inbox_tasks()).kind == 'failed'
    assert view.notification_target() == ('task', OWNER, 'failed')
    store.activity.receive(OWNER, observation('idle'))
    assert dict(notice_for(store.inbox_tasks()).counts)['completed'] == 1
    view.close()
    ui_app.processEvents()
    view.deleteLater()


def test_icon_only_focus_sound_and_animation(tmp_path, ui_app):
    app = ui_app
    store = QuestionStore(tmp_path)
    icon = LauncherIcon()
    icon.set_mode('heart')
    icon.show()
    editor = QLineEdit()
    editor.show()
    editor.activateWindow()
    editor.setFocus()
    app.processEvents()
    assert app.focusWidget() is editor
    windows = set(app.topLevelWidgets())
    controller = NotificationController(icon, {'notification_sound': True})
    controller.play_sound = Mock()
    try:
        controller.update_notice(notice_for(store.inbox_tasks()))
        question(store)
        controller.update_notice(notice_for(store.inbox_tasks()))
        app.processEvents()
        assert app.focusWidget() is editor
        assert set(app.topLevelWidgets()) == windows
        assert icon.notification_symbol == '?'
        controller.play_sound.assert_called_once()
        controller.update_notice(notice_for(store.inbox_tasks()))
        controller.play_sound.assert_called_once()
        assert icon._notification_burst.interval() == 4800
        assert icon._notification_burst.isActive()
        expired = QSignalSpy(icon._notification_burst.timeout)
        icon._notification_burst.start(50)
        assert expired.wait(1000)
        assert not icon._ripple_timer.isActive()
        assert icon.notification_symbol == '?' and icon.notification_count == 1
        controller.set_suspended(True)
        question(store, key='q2', work_id='q2')
        controller.update_notice(notice_for(store.inbox_tasks()))
        assert not icon._ripple_timer.isActive()
        controller.play_sound.assert_called_once()
        controller.set_suspended(False)
        for q in store.visible_questions():
            store.cancel(q['id'])
        controller.update_notice(notice_for(store.inbox_tasks()))
        assert icon.notification_count == 0
        store.receive(OWNER, 'Codex', event())
        controller.update_notice(notice_for(store.inbox_tasks()))
        assert icon.notification_symbol == '✓'
        controller.play_sound.assert_called_once()
        assert set(app.topLevelWidgets()) == windows
    finally:
        controller.close()
        icon.close()
        icon.deleteLater()
        controller.deleteLater()
        editor.close()
        editor.deleteLater()


def test_theme_switch_and_optional_notification_palette(tmp_path, theme_state, monkeypatch):
    theme = theme_state
    store = QuestionStore(tmp_path)
    question(store)
    icon = LauncherIcon()
    icon.set_mode('heart')
    icon.show()
    controller = NotificationController(icon, {})
    controller.play_sound = Mock()
    try:
        theme.apply('clean')
        controller.update_notice(notice_for(store.inbox_tasks()))
        assert icon.notification_color == theme.ACCENT
        assert not icon._ripple_timer.isActive()  # restart stays quiet
        theme.apply('white_orange')
        controller.render()
        assert icon.notification_color == theme.ACCENT
        assert icon.notification_symbol == '?'
        monkeypatch.setitem(theme.THEMES, 'notification_test',
                            {**theme.THEMES['clean'], 'NOTIFICATION_ACTION': '#7355aa'})
        theme.apply('notification_test')
        controller.render()
        assert icon.notification_color == '#7355aa'
        theme.apply('clean')
        controller.render()
        assert icon.notification_color == theme.ACCENT  # no stale custom color
        controller.play_sound.assert_not_called()
    finally:
        controller.close()
        icon.close()
        icon.deleteLater()
        controller.deleteLater()
