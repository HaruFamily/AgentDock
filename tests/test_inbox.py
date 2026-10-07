import json
import pytest

from agentdock.files import atomic_json
from agentdock.qa.store import QuestionStore, json_key
from agentdock.qa.schema import ChatEventInput
from test_agentchat import event, question


def test_legacy_mirrors_hidden_but_qa_and_result_survive_restart(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    native = dict(id='native', owner='native-owner', work_id='session', work_title='Mirror',
                  request_key='native', source='Desktop', kind='completed', text='old native chat',
                  created_at='2026-10-07T00:00:00Z', read=False, external=True)
    old = {**store._chat, 'events': [native],
           'sync_aliases': {json_key(q['owner'], 'w'): ['native-owner', 'session']},
           'sync_visible': {q['owner']: []}}
    atomic_json(store.chat_file, old)
    store = QuestionStore(tmp_path)
    assert len(store.conversations()) == 1
    assert store.conversations()[0]['owner'] == q['owner']
    assert store.visible_questions()[0]['id'] == q['id']
    store.answer(q['id'], {'text': 'approved explicitly'})
    store.receive(q['owner'], 'Agent', event())
    restarted = QuestionStore(tmp_path)
    work = restarted.conversations()[0]
    assert work['state'] == 'unread'
    assert [e['kind'] for e in work['entries']] == ['question', 'completed']
    assert native in json.loads(store.chat_file.read_text(encoding='utf-8'))['events']


@pytest.mark.parametrize('kind', ['started', 'user_message', 'progress'])
def test_reports_cannot_mirror_chat_or_stream_progress(kind):
    with pytest.raises(ValueError):
        ChatEventInput.model_validate(event(kind=kind))


def test_app_has_no_native_receiver_import():
    import sys
    from agentdock.ui import app
    assert not hasattr(app, 'ChatSync')
    assert 'agentdock.chat_sync' not in sys.modules


def test_old_answer_is_not_running_but_pending_and_completion_survive(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    store.answer(q['id'], {'text': 'answer'})
    assert store.inbox_tasks()[0]['state'] == 'running'
    restarted = QuestionStore(tmp_path)
    assert restarted.inbox_tasks() == []
    assert restarted.get(q['id'])['status'] == 'answered'
    pending = question(restarted, 'pending', work_id='pending')
    restarted.receive(q['owner'], 'OpenCode', event())
    tasks = QuestionStore(tmp_path).inbox_tasks()
    assert {t['work_id'] for t in tasks} == {'w', 'pending'}
    assert next(t for t in tasks if t['work_id'] == 'w')['latest']['kind'] == 'completed'
    restarted.cancel(pending['id'])
    assert {t['work_id'] for t in restarted.inbox_tasks()} == {'w'}


def test_old_completion_does_not_replace_newer_unconfirmed_question(tmp_path):
    store = QuestionStore(tmp_path)
    q = question(store)
    store.answer(q['id'], {'text': 'answer'})
    store.receive(q['owner'], 'OpenCode', event())
    next_q = question(store, 'next')
    store.answer(next_q['id'], {'text': 'next answer'})
    assert QuestionStore(tmp_path).inbox_tasks() == []
