"""The conversation as a web page: what Python sends to it (WebEngine itself is not started here)."""
import json
import os
import subprocess
import sys
import textwrap

from agentdock.ui import web_transcript as wt


def test_edit_navigation_keeps_live_transcript(tmp_path):
    # Separate process: WebEngine must be prepared before QApplication exists.
    script = textwrap.dedent('''
        import json, time
        from agentdock.ui import web_transcript as wt
        wt.prepare()
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        actions, loads = [], []
        web = wt.WebTranscript(768, lambda action, arg: actions.append((action, arg)))
        web.view.loadFinished.connect(loads.append)
        def until(check):
            end = time.monotonic() + 10
            while not check() and time.monotonic() < end:
                app.processEvents()
                time.sleep(.01)
            assert check(), (loads, web.ready)
        def js(code):
            result = []
            web.view.page().runJavaScript(code, result.append)
            until(lambda: bool(result))
            return result[0]
        until(lambda: web.ready)
        user = {'kind': 'user', 'id': 'u1', 'text': 'same text', 'files': []}
        web.show([user], '', False, '')
        assert js("document.querySelector('.bubble .text').textContent") == 'same text'
        for attempt in range(2):
            js("startEdit(document.getElementById('u-u1'), 'u1'); document.querySelector('.send').click()")
            until(lambda: len(actions) == attempt + 1 and loads[-1] is False)
            assert actions[-1] == ('edit', json.dumps(['u1', 'same text'], separators=(',', ':')))
            assert web.ready
            # Editing removes the old turn, then the resent prompt and answer stream in.
            web.clear()
            assert js("document.getElementById('log').textContent") == ''
            answer = {'kind': 'assistant', 'id': 'a1', 'parts': [
                {'type': 'text', 'key': 't', 'text': 'new answer'}]}
            web.show([user, answer], '', False, '', True)
            assert js("document.querySelector('.turn').textContent").startswith('new answer')
        web.style = ''
        web.restyle()
        assert not web.ready
        until(lambda: web.ready)
        assert js("document.querySelector('.turn').textContent").startswith('new answer')
    ''')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_DISABLE_SANDBOX='1',
               QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu', XDG_CACHE_HOME=str(tmp_path))
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stdout + result.stderr


def test_markdown_never_lets_agent_markup_or_images_through(ui_app):
    out = wt.markdown_html('<script>alert(1)</script> <img src=x onerror=y> ![pic](http://evil/p.png) '
                           '[bad](javascript:alert(1)) **粗體：**中文')
    assert '<script' not in out and '<img' not in out and 'href="javascript' not in out
    assert '&lt;script&gt;' in out
    assert '<strong>' in out                      # bold next to Chinese punctuation still closes


def test_markdown_tables_and_code_blocks(ui_app):
    codes = {}
    out = wt.markdown_html('| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint("<x>")\n```', codes)
    assert '<table>' in out and '<th>a</th>' in out
    assert list(codes.values()) == ['print("<x>")'] and '<x>' not in out
    key = next(iter(codes))
    assert wt.action_url('code', key) in out


def test_steps_of_an_answer_become_one_turn_with_folded_tools(ui_app):
    tool = lambda i, name='shell': {'kind': 'assistant', 'id': f'a{i}', 'error': '', 'parts': [
        {'type': 'tool', 'key': f'tool:c{i}', 'name': name, 'input': {'command': 'ls'}, 'status': 'completed',
         'output': 'out', 'error': '', 'diff': ''}]}
    items = [{'kind': 'user', 'id': 'u1', 'text': '問 <b>', 'files': []},
             {'kind': 'assistant', 'id': 'a0', 'error': '', 'parts': [{'type': 'reasoning', 'key': 'r', 'text': ''}]},
             tool(1, 'mcp__google-sheets__list_sheets'), tool(2), tool(3),
             {'kind': 'assistant', 'id': 'a4', 'error': '', 'parts': [{'type': 'text', 'key': 't', 'text': '**答**'}]},
             {'kind': 'end', 'id': 'e1', 'outcome': 'done', 'error': ''},
             {'kind': 'user', 'id': 'u2', 'text': '再問', 'files': []},
             {'kind': 'end', 'id': 'e2', 'outcome': 'interrupted', 'error': ''}]
    found = wt.units(items, retry_id='e2')
    assert [(page_id, kind) for page_id, kind, _s, _i in found] == [
        ('u-u1', 'user'), ('t-a1', 'turn'), ('u-u2', 'user'), ('e-e2', 'end')]   # empty thought and normal end: nothing
    codes = {}
    turn = wt.unit_html('turn', found[1][3], 'e2', codes)
    assert '3 個工具' in turn and 'google-sheets · list_sheets' in turn and '<strong>答</strong>' in turn
    assert wt.action_url('fork', 'a4') in turn
    assert '問 &lt;b&gt;' in wt.unit_html('user', found[0][3], '', codes)
    assert wt.action_url('retry') in wt.unit_html('end', found[3][3], 'e2', codes)
    assert wt.action_url('retry') not in wt.unit_html('end', found[3][3], '', codes)
    assert wt.plain_text('turn', found[1][3]) == '**答**'
    # The signature changes with what is shown, so only changed parts are sent again.
    assert wt.units(items, 'e2')[1][2] == found[1][2] and wt.units(items, '')[3][2] != found[3][2]


def test_user_message_is_edited_in_place(ui_app):
    html_ = wt.user_html({'kind': 'user', 'id': 'u1', 'text': '問 <b>'})
    assert 'data-edit="u1"' in html_ and wt.action_url('edit', 'u1') not in html_   # the page opens the box
    page = wt.page_html(768)
    assert 'function startEdit' in page and json.dumps(wt.action_url('edit')) in page
    assert '!e.isComposing' in page   # Enter that picks an IME word does not send


def test_page_has_no_remote_resources(ui_app):
    page = wt.page_html(768)
    assert 'http://' not in page and 'src=' not in page


def test_finished_answer_folds_its_steps_on_top(ui_app):
    items = [{'kind': 'assistant', 'id': 'a1', 'error': '', 'parts': [
                 {'type': 'text', 'key': 't0', 'text': '先查一下'},
                 {'type': 'tool', 'key': 'tool:c1', 'name': 'shell', 'input': {}, 'status': 'completed',
                  'output': '', 'error': '', 'diff': ''}]},
             {'kind': 'assistant', 'id': 'a2', 'error': '', 'parts': [{'type': 'text', 'key': 't1', 'text': '答案'}]}]
    done = wt.turn_html(items, {})
    assert '處理了 1 個步驟' in done and '<hr class="sep">' in done
    process, answer = done.split('<hr class="sep">')
    assert '先查一下' in process and '答案' in answer and '先查一下' not in answer
    live = wt.turn_html(items, {}, live=True)
    assert 'process' not in live and live.index('先查一下') < live.index('答案')
    assert [k for _p, k, _s, _i in wt.units(items, running=True)] == ['turn live']
    assert [k for _p, k, _s, _i in wt.units(items)] == ['turn']
    # Thinking open only because the answer was live is marked, so the page closes it once done.
    thought = [{'kind': 'assistant', 'id': 'a0', 'error': '', 'parts': [{'type': 'reasoning', 'key': 'r0', 'text': '想'}]}]
    assert '<details class="think" open data-live' in wt.turn_html(thought, {}, live=True)
    assert 'data-live' not in wt.turn_html(thought, {}) and ':not([data-live])' in wt.SCRIPT


def test_sent_pictures_show_in_the_message(ui_app, tmp_path):
    from PySide6.QtGui import QColor, QImage
    picture = tmp_path / 'paste-1.png'
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor('red'))
    image.save(str(picture))
    item = {'kind': 'user', 'id': 'u1', 'text': '看這張', 'files': ['paste-1.png', 'notes.txt']}
    out = wt.user_html(item, lambda name: str(tmp_path / name))
    assert '<img src="data:image/png;base64,' in out and '附件：notes.txt' in out
    assert '<img' not in wt.user_html(item)
