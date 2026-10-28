"""A stand-in for `codex app-server` (JSON-RPC lines on stdio), for tests.

turn/start text picks the behaviour: CMD (command approval), EDIT (file change approval), ASK
(request_user_input), SLOW (waits for turn/interrupt), FAIL (failed turn), CRASH (exits).
Anything else gets a two-part agent message. Received lines go to $FAKE_CODEX_LOG.
"""
import json
import os
import sys

LOG = os.environ.get('FAKE_CODEX_LOG')
threads: dict[str, list] = {}
counter = [0]


def log(entry):
    if LOG:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def out(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + '\n')
    sys.stdout.flush()


log({'method': 'process/start', 'argv': sys.argv[1:]})


def new_id(prefix):
    counter[0] += 1
    return f'{prefix}_{counter[0]}'


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log(message)
    return message


def note(method, **params):
    out({'method': method, 'params': params})


def side(message):
    """Requests that the real server answers while a turn runs."""
    if message.get('method') == 'thread/read':
        params = message['params']
        out({'id': message['id'], 'result': {'thread': {'id': params['threadId'],
                                                        'turns': threads.get(params['threadId'], [])}}})


def server_request(method, params):
    rid = 100 + counter[0]
    counter[0] += 1
    out({'id': rid, 'method': method, 'params': params})
    while True:
        message = read()
        if message.get('id') == rid and 'method' not in message:
            return message.get('result') or {}
        side(message)


def agent_message(thread, turn, text, items):
    mid = new_id('msg')
    note('item/started', threadId=thread, turnId=turn, item={'type': 'agentMessage', 'id': mid, 'text': ''})
    note('item/agentMessage/delta', threadId=thread, turnId=turn, itemId=mid, delta=text[:3])
    note('item/agentMessage/delta', threadId=thread, turnId=turn, itemId=mid, delta=text[3:])
    item = {'type': 'agentMessage', 'id': mid, 'text': text}
    note('item/completed', threadId=thread, turnId=turn, item=item)
    items.append(item)


def run_turn(thread, turn, text):
    user = {'type': 'userMessage', 'id': new_id('user'), 'content': [{'type': 'text', 'text': text}]}
    items = [user]
    note('turn/started', threadId=thread, turn={'id': turn, 'items': [], 'status': 'inProgress'})
    note('item/started', threadId=thread, turnId=turn, item=user)
    note('item/completed', threadId=thread, turnId=turn, item=user)
    status, error = 'completed', None
    if text.startswith('CRASH'):
        sys.stderr.write('codex panicked\n')
        sys.stderr.flush()
        sys.exit(2)
    if text.startswith('CMD') or text.startswith('EDIT'):
        if text.startswith('CMD'):
            item = {'type': 'commandExecution', 'id': new_id('call'), 'command': "pwsh -Command 'npm test'",
                    'commandActions': [{'type': 'unknown', 'command': 'npm test'}], 'status': 'inProgress'}
            method = 'item/commandExecution/requestApproval'
            params = {'threadId': thread, 'turnId': turn, 'itemId': item['id'], 'reason': 'needs network',
                      'command': item['command'], 'commandActions': item['commandActions']}
        else:
            item = {'type': 'fileChange', 'id': new_id('patch'), 'status': 'inProgress',
                    'changes': [{'path': 'src/app.py', 'kind': 'update', 'diff': '+print(1)'}]}
            method = 'item/fileChange/requestApproval'
            params = {'threadId': thread, 'turnId': turn, 'itemId': item['id']}
        note('item/started', threadId=thread, turnId=turn, item=item)
        decision = server_request(method, params).get('decision')
        log({'decision': decision})
        accepted = decision in ('accept', 'acceptForSession')
        done = {**item, 'status': 'completed' if accepted else 'declined', 'aggregatedOutput': 'ok' if accepted else None}
        note('item/completed', threadId=thread, turnId=turn, item=done)
        items.append(done)
        agent_message(thread, turn, f'decision {decision}', items)
    elif text.startswith('ASK'):
        answer = server_request('item/tool/requestUserInput', {
            'threadId': thread, 'turnId': turn, 'itemId': new_id('ask'), 'isBlocking': True,
            'questions': [{'id': 'colour', 'header': 'Colour', 'question': 'Which colour?', 'isOther': True,
                           'options': [{'label': 'Red', 'description': 'warm'}, {'label': 'Blue', 'description': 'cool'}]}]})
        agent_message(thread, turn, f"answers {json.dumps(answer.get('answers'), ensure_ascii=False)}", items)
    elif text.startswith('SLOW'):
        status = 'interrupted'
        while True:
            message = read()
            if message.get('method') == 'turn/interrupt':
                out({'id': message['id'], 'result': {}})
                break
            if message.get('method') == 'turn/steer':
                steer = message['params']
                log({'steer_expected': steer['expectedTurnId'] == turn})
                out({'id': message['id'], 'result': {'turnId': turn}})
                extra = {'type': 'userMessage', 'id': new_id('user'), 'content': steer['input']}
                note('item/started', threadId=thread, turnId=turn, item=extra)
                items.append(extra)
                agent_message(thread, turn, f"steered {steer['input'][0]['text']}", items)
                status = 'completed'
                break
            side(message)
    elif text.startswith('FAIL'):
        note('error', threadId=thread, turnId=turn, willRetry=False, error={'message': 'quota exceeded'})
        status, error = 'failed', {'message': 'quota exceeded'}
    else:
        agent_message(thread, turn, 'Hello there', items)
    turn_record = {'id': turn, 'items': items, 'status': status, 'error': error}
    threads.setdefault(thread, []).append(turn_record)
    note('thread/tokenUsage/updated', threadId=thread, turnId=turn, tokenUsage={
        'last': {'totalTokens': 1200 * len(threads[thread]), 'inputTokens': 1000}, 'total': {'totalTokens': 9999},
        'modelContextWindow': 10000})
    note('turn/completed', threadId=thread, turn={**turn_record, 'items': []})


pending_turn = None
while True:
    message = read()
    method, params, mid = message.get('method'), message.get('params') or {}, message.get('id')
    if mid is None:
        continue
    if method == 'initialize':
        out({'id': mid, 'result': {'userAgent': 'fake'}})
    elif method == 'thread/start':
        thread = new_id('thr')
        threads[thread] = []
        out({'id': mid, 'result': {'thread': {'id': thread, 'cwd': params.get('cwd')}}})
    elif method == 'thread/resume':
        if params['threadId'] not in threads:
            threads[params['threadId']] = []
        out({'id': mid, 'result': {'thread': {'id': params['threadId']}}})
    elif method == 'thread/read':
        out({'id': mid, 'result': {'thread': {'id': params['threadId'], 'turns': threads.get(params['threadId'], [])}}})
    elif method == 'thread/compact/start':
        out({'id': mid, 'result': {}})
        turn = new_id('turn')
        note('turn/started', threadId=params['threadId'], turn={'id': turn, 'items': [], 'status': 'inProgress'})
        note('turn/completed', threadId=params['threadId'], turn={'id': turn, 'items': [], 'status': 'completed'})
    elif method == 'model/list':
        out({'id': mid, 'result': {'data': [
            {'id': 'gpt-x', 'model': 'gpt-x', 'displayName': 'GPT X', 'hidden': False, 'isDefault': True,
             'defaultReasoningEffort': 'high',
             'supportedReasoningEfforts': [{'reasoningEffort': 'low', 'description': ''},
                                           {'reasoningEffort': 'high', 'description': ''}]},
            {'id': 'old', 'model': 'old', 'displayName': 'Old', 'hidden': True, 'supportedReasoningEfforts': []}]}})
    elif method == 'thread/fork':
        thread = new_id('thr')
        threads[thread] = list(threads.get(params['threadId'], []))  # excludeTurns only trims the reply
        out({'id': mid, 'result': {'thread': {'id': thread}}})
    elif method == 'thread/revert':
        turns = threads[params['threadId']]
        cut = next(i for i, t in enumerate(turns) if t['id'] == params['beforeTurnId'])
        threads[params['threadId']] = turns[:cut]
        out({'id': mid, 'result': {}})
    elif method == 'thread/list':
        out({'id': mid, 'result': {'data': [{'id': t, 'preview': f'preview {t}', 'name': None, 'cwd': '/w',
                                             'updatedAt': 1700000000} for t in threads]}})
    elif method == 'turn/start':
        turn = new_id('turn')
        out({'id': mid, 'result': {'turn': {'id': turn, 'status': 'inProgress', 'items': []}}})
        run_turn(params['threadId'], turn, params['input'][0]['text'])
    else:
        out({'id': mid, 'error': {'code': -32601, 'message': f'unknown {method}'}})
