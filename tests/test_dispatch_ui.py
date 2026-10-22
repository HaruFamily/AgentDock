"""Inbox controls for sent tasks: every decision is a click, typed text survives refreshes."""
from agentdock.dispatch.store import owner_for
from agentdock.qa.store import QuestionStore

OWNER = owner_for('profile-1', 'opencode')
FORM = {'id': 'frm_1', 'fields': [
    {'key': 'q0', 'title': 'Env', 'description': 'Where?', 'type': 'string', 'custom': True,
     'options': [{'value': 'staging', 'label': 'staging'}, {'value': 'prod', 'label': 'prod'}]},
    {'key': 'q1', 'title': 'Parts', 'description': 'Which?', 'type': 'array', 'custom': False,
     'options': [{'value': 'a', 'label': 'A'}, {'value': 'b', 'label': 'B'}]}]}


class Recorder:
    clients = ('opencode',)

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def view(tmp_path, ui_app):
    from agentdock.ui.inbox_view import InboxView
    store = QuestionStore(tmp_path)
    sender = Recorder()
    inbox = InboxView(store, sender, lambda: [{'label': 'OpenCode', 'client': 'opencode', 'owner': OWNER}])
    inbox.resize(420, 900)
    inbox.show()
    return store, sender, inbox


def panel(inbox, key='opencode:ses_1'):
    row = next(r for r in inbox.rows.values() if r.panel and r.panel.key == key)
    return row, row.panel


def test_permission_buttons_forward_the_clicked_decision(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Fix', prompt='fix it')
    store.dispatch.update('opencode:ses_1', permissions={'per_1': {'action': 'shell', 'resources': ['npm test']}})
    inbox.refresh()
    row, box = panel(inbox)
    assert row.status.text() == '待授權' and not row.remove_button.isEnabled()
    buttons = {b.text(): b for b in box.permission_rows[0].findChildren(type(row.remove_button))}
    assert set(buttons) == {'允許', '一律允許', '拒絕'}
    assert sender.calls == []  # nothing is decided without a click
    buttons['一律允許'].click()
    assert sender.calls == [('reply_permission', 'opencode:ses_1', 'per_1', 'always')]


def test_form_answers_and_skip(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Deploy', prompt='deploy')
    store.dispatch.update('opencode:ses_1', forms={'frm_1': FORM})
    inbox.refresh()
    row, box = panel(inbox)
    form = box.forms['frm_1']
    assert row.status.text() == '待回答'
    form.submit()
    assert sender.calls == []  # incomplete answers are not sent
    (_, env, custom, _), (_, parts, _, _) = form.inputs
    env[1].setChecked(True)
    parts[0].setChecked(True)
    parts[1].setChecked(True)
    form.submit()
    assert sender.calls[-1] == ('reply_form', 'opencode:ses_1', 'frm_1', {'q0': 'prod', 'q1': ['a', 'b']})
    custom.setPlainText('canary')
    form.submit()
    assert sender.calls[-1][-1]['q0'] == 'canary'
    skip = next(b for b in form.findChildren(type(row.remove_button)) if b.text() == '略過')
    skip.click()
    assert sender.calls[-1] == ('reply_form', 'opencode:ses_1', 'frm_1', None)


def test_reply_box_survives_refresh_and_blocks_removal(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Fix', prompt='fix it')
    store.dispatch.update('opencode:ses_1', state='done', reply='**Done**')
    inbox.refresh()
    row, box = panel(inbox)
    assert row.status.text() == '已完成' and box.reply.source == '**Done**' and not box.stop.isVisibleTo(row)
    box.text.setPlainText('one more thing')
    store.dispatch.update('opencode:ses_1', reply='changed')
    inbox.refresh()
    assert panel(inbox)[1] is box and box.text.toPlainText() == 'one more thing'
    row.remove()
    assert store.dispatch.rows  # typed text keeps the card
    box.send()
    assert sender.calls == [('send', 'opencode:ses_1', 'one more thing')] and box.text.toPlainText() == ''
    store.dispatch.update('opencode:ses_1', state='running')
    inbox.refresh()
    assert box.stop.isVisibleTo(row)
    box.stop.click()
    assert sender.calls[-1] == ('interrupt', 'opencode:ses_1')


def test_status_shows_failure_and_offline(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Fix', prompt='fix it')
    store.dispatch.update('opencode:ses_1', state='failed', error='HTTP 403')
    store.dispatch.track('opencode', 'ses_2', owner=OWNER, title='Other', state='unknown')
    store.dispatch.set_offline('OpenCode 服務未執行。')
    inbox.refresh()
    assert panel(inbox)[0].status.text() == '失敗：HTTP 403'
    assert panel(inbox, 'opencode:ses_2')[0].status.text() == '狀態未知\nOpenCode 服務未執行。'


def test_composer_validates_then_sends(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    inbox.new_button.setChecked(True)
    composer = inbox.composer
    assert composer.isVisible() and not composer.target.isVisible()
    composer.folder.setCurrentText(str(tmp_path / 'missing'))
    composer.text.setPlainText('do it')
    composer.send()
    assert sender.calls == [] and composer.error.text() == '找不到這個資料夾。'
    composer.folder.setCurrentText(str(tmp_path))
    composer.send()
    assert sender.calls == [('start_task', str(tmp_path), 'do it', OWNER)]
    assert not composer.isVisible() and composer.text.toPlainText() == ''


def test_native_opencode_card_can_be_taken_over(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    store.activity.receive(OWNER, {'client': 'opencode', 'session_id': 'ses_t', 'state': 'approval',
                                   'title': 'repo · ses_t', 'request_id': 'per_9'})
    store.activity.receive(OWNER, {'client': 'codex', 'session_id': 'thr_1', 'state': 'idle', 'title': 'codex'})
    inbox.refresh()
    rows = {r.title.text(): r for r in inbox.rows.values()}
    assert rows['codex'].adopt_button is None
    rows['repo · ses_t'].adopt_button.click()
    assert sender.calls == [('adopt', 'opencode', 'ses_t', OWNER, 'repo · ses_t')]


def test_inbox_without_dispatcher_has_no_send_controls(tmp_path, ui_app):
    from agentdock.ui.inbox_view import InboxView
    inbox = InboxView(QuestionStore(tmp_path))
    assert inbox.composer is None and not hasattr(inbox, 'new_button')


def test_pending_count_includes_client_questions(tmp_path, ui_app):
    store, sender, inbox = view(tmp_path, ui_app)
    counts = []
    inbox.pending_changed.connect(counts.append)
    store.dispatch.track('opencode', 'ses_1', owner=OWNER, title='Deploy')
    store.dispatch.update('opencode:ses_1', forms={'frm_1': FORM})
    inbox.refresh()
    assert counts[-1] == 1


def test_targets_use_the_registered_agent_identity(ui_app):
    from types import SimpleNamespace
    from agentdock.agents import Profile
    from agentdock.ui.app import Dock
    profiles = [Profile('p-codex', 'Codex', 'codex', 'c'), Profile('profile-1', '我的 OpenCode', 'opencode', 'o')]
    fake = SimpleNamespace(manager=SimpleNamespace(list=lambda: profiles), dispatcher=SimpleNamespace(clients=('opencode',)))
    assert Dock._dispatch_targets(fake) == [{'label': '我的 OpenCode', 'client': 'opencode', 'owner': OWNER}]
    fake.manager = SimpleNamespace(list=lambda: [])
    assert Dock._dispatch_targets(fake)[0]['owner'] == owner_for(None, 'opencode')


def test_empty_folder_opens_the_picker_then_sends(tmp_path, ui_app, monkeypatch):
    from agentdock.ui import dispatch_view
    store, sender, inbox = view(tmp_path, ui_app)
    inbox.new_button.setChecked(True)
    composer = inbox.composer
    assert composer.folder.currentText() == '' and composer.folder.lineEdit().placeholderText() == '資料夾'
    picked = []
    monkeypatch.setattr(dispatch_view.QFileDialog, 'getExistingDirectory',
                        staticmethod(lambda *a: picked.append(a) or ''))
    composer.text.setPlainText('測試')
    composer.send()
    assert picked and sender.calls == [] and composer.error.isHidden()  # cancelled: nothing sent, no error
    monkeypatch.setattr(dispatch_view.QFileDialog, 'getExistingDirectory', staticmethod(lambda *a: str(tmp_path)))
    composer.send()
    assert sender.calls == [('start_task', str(tmp_path), '測試', OWNER)]
