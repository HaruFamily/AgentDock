"""A stand-in for `claude` speaking the stream-json SDK protocol, for tests.

The user's text picks the behaviour: TOOL (permission prompt), ASK (AskUserQuestion), SLOW (waits
for an interrupt), FAIL (error result), CRASH (exits). Anything else gets a two-part text reply.
Every received line is appended to $FAKE_CLAUDE_LOG with the arguments.
"""
import json
import os
import sys
import uuid

LOG = os.environ.get('FAKE_CLAUDE_LOG')
SID = sys.argv[sys.argv.index('--session-id') + 1] if '--session-id' in sys.argv else sys.argv[sys.argv.index('--resume') + 1]


def log(entry):
    if LOG:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')


def out(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    log(message)
    return message


def stream(mid, blocks):
    out({'type': 'stream_event', 'event': {'type': 'message_start', 'message': {'id': mid}}, 'session_id': SID,
         'parent_tool_use_id': None})
    for i, block in enumerate(blocks):
        if block['type'] == 'text':
            out({'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': i,
                                                   'content_block': {'type': 'text', 'text': ''}}, 'parent_tool_use_id': None})
            for piece in block['pieces']:
                out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': i,
                                                       'delta': {'type': 'text_delta', 'text': piece}}, 'parent_tool_use_id': None})
        else:
            out({'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': i, 'content_block': {
                'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': {}}}, 'parent_tool_use_id': None})
            text = json.dumps(block['input'], ensure_ascii=False)
            out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': i, 'delta': {
                'type': 'input_json_delta', 'partial_json': text[:5]}}, 'parent_tool_use_id': None})
            out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': i, 'delta': {
                'type': 'input_json_delta', 'partial_json': text[5:]}}, 'parent_tool_use_id': None})
        out({'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': i}, 'parent_tool_use_id': None})
    out({'type': 'stream_event', 'event': {'type': 'message_stop'}, 'parent_tool_use_id': None})


def result(**extra):
    out({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'ok', 'session_id': SID,
         'uuid': str(uuid.uuid4()), **extra})


def ask(tool, data, suggestions=None):
    rid = 'perm-' + uuid.uuid4().hex[:6]
    out({'type': 'control_request', 'request_id': rid, 'request': {
        'subtype': 'can_use_tool', 'tool_name': tool, 'input': data, 'tool_use_id': 'toolu_x',
        'description': data.get('description', ''),
        'permission_suggestions': suggestions or []}})
    while True:
        message = read()
        if message.get('type') == 'control_response' and message['response'].get('request_id') == rid:
            return message['response']['response']


def turn(text):
    mid = 'msg_' + uuid.uuid4().hex[:8]
    if text.startswith('CRASH'):
        sys.stderr.write('boom: something broke\n')
        sys.stderr.flush()
        sys.exit(3)
    if text.startswith('FAIL'):
        out({'type': 'result', 'subtype': 'success', 'is_error': True, 'result': 'API Error: 401 unauthorized',
             'session_id': SID, 'uuid': str(uuid.uuid4())})
        return
    if text.startswith('SLOW'):
        stream(mid, [{'type': 'text', 'pieces': ['thinking…']}])
        while True:
            message = read()
            if message.get('type') == 'control_request' and message['request'].get('subtype') == 'interrupt':
                out({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                              'response': {}}})
                break
        out({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': '[Request interrupted by user]'}]},
             'parent_tool_use_id': None})
        result(subtype='error_during_execution', is_error=True, terminal_reason='aborted_streaming')
        return
    if text.startswith('TOOL') or text.startswith('ASK'):
        tool, data = (('Bash', {'command': 'npm test', 'description': 'run tests'}) if text.startswith('TOOL') else
                      ('AskUserQuestion', {'questions': [{'question': 'Which colour?', 'header': 'Colour', 'multiSelect': False,
                                                          'options': [{'label': 'Red', 'description': 'warm'},
                                                                      {'label': 'Blue', 'description': 'cool'}]}]}))
        stream(mid, [{'type': 'text', 'pieces': ['Let me check.']},
                     {'type': 'tool', 'id': 'toolu_1', 'name': tool, 'input': data}])
        decision = ask(tool, data, [{'type': 'addRules', 'rules': [{'toolName': 'Bash', 'ruleContent': 'npm test'}],
                                     'behavior': 'allow', 'destination': 'localSettings'},
                                    {'type': 'setMode', 'mode': 'acceptEdits', 'destination': 'session'}])
        allowed = decision.get('behavior') == 'allow'
        out({'type': 'user', 'message': {'role': 'user', 'content': [{
            'type': 'tool_result', 'tool_use_id': 'toolu_1', 'is_error': not allowed,
            'content': 'tests passed' if allowed else decision.get('message', 'denied')}]}, 'parent_tool_use_id': None})
        answers = (decision.get('updatedInput') or {}).get('answers')
        reply = f'answers {json.dumps(answers, ensure_ascii=False)}' if answers else ('done' if allowed else 'stopped')
        stream('msg_' + uuid.uuid4().hex[:8], [{'type': 'text', 'pieces': [reply]}])
        result()
        return
    stream(mid, [{'type': 'text', 'pieces': ['Hello ', 'there']}])
    out({'type': 'assistant', 'message': {'id': mid, 'content': [{'type': 'text', 'text': 'Hello there'}]},
         'parent_tool_use_id': None})
    result()


log({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'entrypoint': os.environ.get('CLAUDE_CODE_ENTRYPOINT')})
while True:
    message = read()
    if message.get('type') == 'control_request' and message['request'].get('subtype') == 'initialize':
        out({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'], 'response': {}}})
        out({'type': 'system', 'subtype': 'init', 'session_id': SID})
    elif message.get('type') == 'user':
        turn(str(message['message']['content']))
