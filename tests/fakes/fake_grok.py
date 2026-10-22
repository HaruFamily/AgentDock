"""A stand-in for `grok agent stdio` (ACP JSON-RPC lines), for tests.

The prompt text picks the behaviour: CMD (session/request_permission), ASK (_x.ai/ask_user_question),
SLOW (waits for session/cancel), FAIL (error response), CRASH (exits). Anything else gets a
two-chunk reply. session/load replays what this process (or $FAKE_GROK_STORE) recorded.
Received lines go to $FAKE_GROK_LOG.
"""
import json
import os
import sys
import uuid

LOG = os.environ.get('FAKE_GROK_LOG')
STORE = os.environ.get('FAKE_GROK_STORE')
sessions: dict[str, list] = json.load(open(STORE, encoding='utf-8')) if STORE and os.path.exists(STORE) else {}
counter = [0]
inbox: list[dict] = []


def log(entry):
    if LOG:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def out(message):
    sys.stdout.write(json.dumps({'jsonrpc': '2.0', **message}, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def save():
    if STORE:
        with open(STORE, 'w', encoding='utf-8') as f:
            json.dump(sessions, f)


def read(queued=True):
    if queued and inbox:
        return inbox.pop(0)
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log(message)
    return message


def update(sid, body, prompt_id=None, record=True):
    counter[0] += 1
    meta = {'eventId': f'{sid}-{counter[0]}', **({'promptId': prompt_id} if prompt_id else {})}
    out({'method': 'session/update', 'params': {'sessionId': sid, 'update': body, '_meta': meta}})
    if record:
        sessions.setdefault(sid, []).append({'update': body, 'meta': meta})
        save()


def ask_client(method, params):
    counter[0] += 1
    rid = 500 + counter[0]
    out({'id': rid, 'method': method, 'params': params})
    while True:
        message = read(queued=False)
        if message.get('id') == rid and 'method' not in message:
            return message.get('result') or {}
        inbox.append(message)
        if message.get('method') == 'session/cancel':
            return None


def text(sid, pid, value):
    update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': value[:3]}}, pid)
    update(sid, {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': value[3:]}}, pid)


def prompt(mid, sid, value):
    pid = str(uuid.uuid4())
    sessions.setdefault(sid, []).append({'update': {'sessionUpdate': 'user_message_chunk',
                                                    'content': {'type': 'text', 'text': value}},
                                         'meta': {'eventId': f'{sid}-u{counter[0]}'}})
    if value.startswith('CRASH'):
        sys.stderr.write('grok crashed\n')
        sys.stderr.flush()
        sys.exit(4)
    if value.startswith('FAIL'):
        out({'id': mid, 'error': {'code': -32603, 'message': 'Internal error', 'data': 'quota exceeded'}})
        return
    if value.startswith('SLOW'):
        text(sid, pid, 'working')
        while True:
            message = read()
            if message.get('method') == 'session/cancel':
                break
        out({'id': mid, 'result': {'stopReason': 'cancelled'}})
        return
    if value.startswith('CMD'):
        text(sid, pid, 'Let me run it.')
        tool = {'toolCallId': 'call_1', 'title': 'run_terminal_command', 'kind': 'execute',
                'rawInput': {'command': 'npm test', 'description': 'run tests'},
                '_meta': {'x.ai/tool': {'name': 'run_terminal_command', 'label': 'Run Command',
                                        'input': {'command': 'npm test', 'description': 'run tests'}}}}
        update(sid, {'sessionUpdate': 'tool_call', **tool}, pid)
        answer = ask_client('session/request_permission', {'sessionId': sid, 'toolCall': {**tool, 'title': 'Execute `npm test`'},
                            'options': [{'optionId': 'always-allow', 'kind': 'allow_always', 'name': 'Always'},
                                        {'optionId': 'allow-once', 'kind': 'allow_once', 'name': 'Yes'},
                                        {'optionId': 'reject-once', 'kind': 'reject_once', 'name': 'No'}]})
        option = ((answer or {}).get('outcome') or {}).get('optionId', 'cancelled')
        log({'option': option})
        ok = option in ('allow-once', 'always-allow')
        update(sid, {'sessionUpdate': 'tool_call_update', 'toolCallId': 'call_1', 'status': 'completed' if ok else 'failed',
                     'content': [{'type': 'content', 'content': {'type': 'text', 'text': 'tests passed' if ok else 'denied'}}]}, pid)
        text(sid, pid, f'option {option}')
        out({'id': mid, 'result': {'stopReason': 'end_turn'}})
        return
    if value.startswith('ASK'):
        answer = ask_client('_x.ai/ask_user_question', {'sessionId': sid, 'toolCallId': 'call_q', 'questions': [{
            'question': 'Which colour?', 'multiSelect': None,
            'options': [{'label': 'Red', 'description': 'warm'}, {'label': 'Blue', 'description': 'cool'}]}]})
        text(sid, pid, f'answers {json.dumps(answer, ensure_ascii=False)}')
        out({'id': mid, 'result': {'stopReason': 'end_turn'}})
        return
    text(sid, pid, 'Hello there')
    out({'id': mid, 'result': {'stopReason': 'end_turn'}})


while True:
    message = read()
    method, params, mid = message.get('method'), message.get('params') or {}, message.get('id')
    if mid is None:
        continue
    if method == 'initialize':
        out({'id': mid, 'result': {'protocolVersion': 1, 'agentCapabilities': {'loadSession': True}}})
    elif method == 'session/new':
        sid = str(uuid.uuid4())
        sessions[sid] = []
        save()
        out({'id': mid, 'result': {'sessionId': sid}})
    elif method == 'session/load':
        sid = params['sessionId']
        for entry in sessions.get(sid, []):
            out({'method': 'session/update', 'params': {'sessionId': sid, 'update': entry['update'], '_meta': entry['meta']}})
        out({'id': mid, 'result': {}})
    elif method == '_x.ai/models/list':
        out({'id': mid, 'result': {'result': {'currentModelId': 'grok-a', 'availableModels': [
            {'modelId': 'grok-a', 'name': 'Grok A', '_meta': {'reasoningEfforts': [{'value': 'low'}, {'value': 'high'}]}},
            {'modelId': 'grok-b', 'name': 'Grok B'}]}}})
    elif method == 'session/set_config_option':
        log({'config': params})
        out({'id': mid, 'result': {'configOptions': []}})
    elif method == '_x.ai/session/delete':
        sessions.pop(params['sessionId'], None)
        save()
        out({'id': mid, 'result': {'success': True}})
    elif method == 'session/prompt':
        prompt(mid, params['sessionId'], params['prompt'][0]['text'])
    else:
        out({'id': mid, 'error': {'code': -32601, 'message': f'unknown {method}'}})
