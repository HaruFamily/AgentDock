"""The conversation drawn as a web page (Chromium via QtWebEngine), as in the Claude / Codex desktop apps.

Answers are Markdown turned into HTML by markdown-it with raw HTML and images off, so nothing from an
Agent becomes markup or loads anything. The page's own script only places and updates the parts Python
sends; its buttons are links to https://agentdock.invalid/<action>/<argument>, caught before loading.

Only used when the app called prepare() before creating QApplication (WebEngine needs that) and the look
does not paint the chat itself; otherwise the Qt widgets in chat_window draw the conversation.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from urllib.parse import quote, unquote

from agentdock.ui import theme

ENABLED = False
ACTION_HOST = 'agentdock.invalid'


def prepare() -> None:
    """Call before QApplication exists. WebEngine and markdown-it missing just leave the Qt drawing."""
    global ENABLED
    try:
        import markdown_it  # noqa: F401
        from PySide6 import QtWebEngineWidgets  # noqa: F401
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        ENABLED = True
    except Exception:
        logging.exception('web transcript unavailable; using the Qt one')
        ENABLED = False


def tool_name(name: str) -> str:
    """mcp__google-sheets__list_sheets → google-sheets · list_sheets."""
    found = re.match(r'mcp__(.+?)__(.+)$', name)
    return f'{found.group(1)} · {found.group(2)}' if found else name


def action_url(action: str, arg: str = '') -> str:
    return f'https://{ACTION_HOST}/{action}/{quote(arg, safe="")}'


# --------------------------------------------------------------------------- Markdown
_MD = None


def _markdown():
    global _MD
    if _MD is None:
        from markdown_it import MarkdownIt
        md = MarkdownIt('commonmark', {'html': False, 'linkify': False, 'typographer': False})
        md.enable(['table', 'strikethrough'])
        md.disable('image')   # ![x](url) stays a plain link: nothing is fetched
        md.renderer.rules['fence'] = _fence
        md.renderer.rules['code_block'] = _fence
        _MD = md
    return _MD


def _highlight(code: str, language: str) -> str:
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name
        return highlight(code, get_lexer_by_name(language), HtmlFormatter(nowrap=True))
    except Exception:
        return html.escape(code)


def _fence(tokens, idx, _options, env):
    token = tokens[idx]
    language = (token.info or '').strip().split(' ')[0]
    code = token.content.rstrip('\n')
    key = hashlib.sha1(code.encode('utf-8')).hexdigest()[:12]
    env.setdefault('codes', {})[key] = code
    return (f'<div class="code"><div class="code-head"><span>{html.escape(language)}</span>'
            f'<a class="code-copy" href="{action_url("code", key)}">複製</a></div>'
            f'<pre class="hl"><code>{_highlight(code, language) if language else html.escape(code)}</code></pre></div>')


def markdown_html(text: str, codes: dict | None = None) -> str:
    from agentdock.ui.question_text import cjk_emphasis
    env = {'codes': codes if codes is not None else {}}
    return _markdown().render(cjk_emphasis(text), env)


# --------------------------------------------------------------------------- transcript → parts of the page
ICONS = {
    'copy': '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
    'edit': '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    'fork': '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/>'
            '<path d="M6 7v2a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 12v5"/>',
    'retry': '<path d="M4 12a8 8 0 1 0 2.3-5.7M4 4v5h5"/>',
}


def _icon_link(name: str, label: str, href: str) -> str:
    return (f'<a class="act" href="{href}" title="{label}" aria-label="{label}"><svg viewBox="0 0 24 24">'
            f'{ICONS[name]}</svg></a>')


def _has_content(item: dict) -> bool:
    if item.get('error'):
        return True
    return any(p['type'] == 'tool' or str(p.get('text') or '').strip() for p in item.get('parts', []))


def _tool_mark(parts: list[dict]) -> str:
    states = {p.get('status', '') for p in parts}
    if 'error' in states:
        return '<span class="mark err">✕</span>'
    if states & {'running', 'pending'}:
        return '<span class="mark busy">…</span>'
    return ''


def _tool_html(part: dict) -> str:
    from agentdock.dispatch.transcript import tool_summary
    from agentdock.ui.chat_messages import diff_of
    summary = tool_summary(part)
    head = (f'<span class="tname">{html.escape(tool_name(part.get("name") or "tool"))}</span>'
            + (f' <span class="targ">{html.escape(summary)}</span>' if summary else '') + _tool_mark([part]))
    diff = diff_of(part)
    hidden = {'old_string', 'new_string', 'oldString', 'newString', 'edits'} if diff else set()
    lines = [f'{k}: {v}' for k, v in (part.get('input') or {}).items()
             if isinstance(v, (str, int, float, bool)) and k not in hidden]
    if part.get('output') and part.get('output') != diff:
        lines += ['', part['output']]
    if part.get('error'):
        lines += ['', part['error']]
    body = ''
    detail = '\n'.join(lines).strip()
    if detail:
        body += f'<pre class="tdetail">{html.escape(detail)}</pre>'
    if diff:
        rows = []
        for line in diff.splitlines()[:400]:
            kind = ('add' if line.startswith('+') and not line.startswith('+++') else
                    'del' if line.startswith('-') and not line.startswith('---') else
                    'hunk' if line.startswith('@@') else '')
            rows.append(f'<span class="{kind}">{html.escape(line) or " "}</span>')
        body += f'<pre class="diff">{chr(10).join(rows)}</pre>'
    if not body:
        return f'<div class="tool line">{head}</div>'
    return (f'<details class="tool" data-key="tool-{html.escape(part["key"])}"><summary>{head}</summary>'
            f'{body}</details>')


def turn_html(items: list[dict], codes: dict, live: bool = False) -> str:
    """Consecutive answer steps as one block. While the Agent works every step shows as it comes (thinking
    open, tools on quiet lines). Once done, everything up to the last tool or thought — the remarks in
    between too — folds into one line on top, a rule under it, then the answer, as in Codex."""
    entries: list[tuple[str, str]] = []   # ('step' | 'text' | 'error', html)
    run: list[dict] = []
    steps = 0

    def flush():
        if len(run) == 1:
            entries.append(('step', _tool_html(run[0])))
        elif run:
            entries.append(('step', f'<details class="steps" data-key="steps-{html.escape(run[0]["key"])}"><summary>'
                                    f'{len(run)} 個工具{_tool_mark(run)}</summary><div class="steps-body">'
                                    + ''.join(_tool_html(p) for p in run) + '</div></details>'))
        run.clear()

    for item in items:
        for part in item.get('parts', []):
            if part['type'] == 'tool':
                run.append(part)
                steps += 1
                continue
            text = str(part.get('text') or '')
            if not text.strip():
                continue
            flush()
            if part['type'] == 'reasoning':
                steps += 1
                entries.append(('step', f'<details class="think"{" open data-live" if live else ""} '
                                        f'data-key="think-{html.escape(item["id"])}-{html.escape(part["key"])}">'
                                        f'<summary>思考過程</summary><div class="think-body">{markdown_html(text, codes)}'
                                        f'</div></details>'))
            else:
                entries.append(('text', f'<div class="md">{markdown_html(text, codes)}</div>'))
        if item.get('error'):
            flush()
            entries.append(('error', f'<div class="err">失敗：{html.escape(item["error"])}</div>'))
    flush()
    last_step = max((i for i, (kind, _h) in enumerate(entries) if kind == 'step'), default=-1)
    if live or last_step < 0:
        body = ''.join(h for _k, h in entries)
    else:
        process = ''.join(h for k, h in entries[:last_step + 1] if k != 'error')
        answer = ''.join(h for k, h in entries[:last_step + 1] if k == 'error') + \
            ''.join(h for _k, h in entries[last_step + 1:])
        body = (f'<details class="process" data-key="process-{html.escape(items[0]["id"])}">'
                f'<summary>處理了 {steps} 個步驟</summary><div class="process-body">{process}</div></details>'
                + (f'<hr class="sep">{answer}' if answer else ''))
    actions = (_icon_link('copy', '複製', action_url('copy', 't-' + items[0]['id']))
               + _icon_link('fork', '分叉', action_url('fork', items[-1]['id'])))
    return body + f'<div class="actions">{actions}</div>'


_THUMBS: dict[tuple, str] = {}


def image_data(path: str) -> str:
    """A small JPEG of an image file as a data: URL (the page loads no files), '' when it is not one."""
    import os
    if not path or not path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')) \
            or not os.path.isfile(path):
        return ''
    key = (path, os.path.getmtime(path))
    if key not in _THUMBS:
        import base64

        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage
        image = QImage(path)
        if image.isNull():
            return ''
        if image.width() > 720 or image.height() > 480:
            image = image.scaled(720, 480, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG' if path.lower().endswith('.png') else 'JPEG', 90)
        _THUMBS[key] = f'data:image/{"png" if path.lower().endswith(".png") else "jpeg"};base64,' + \
            base64.b64encode(bytes(data)).decode('ascii')
    return _THUMBS[key]


def user_html(item: dict, resolve=None) -> str:
    """resolve(file name) → the file's path when known, so a sent picture shows itself."""
    images, files = [], []
    for name in item.get('files') or []:
        src = image_data(resolve(name)) if resolve else ''
        if src:
            images.append(f'<a href="{action_url("image", name)}"><img src="{src}" alt="{html.escape(name)}" title="{html.escape(name)}"></a>')
        else:
            files.append(f'<div class="file">附件：{html.escape(name)}</div>')
    images_html = f'<div class="images">{"".join(images)}</div>' if images else ''
    files = images_html + ''.join(files)
    text = f'<div class="text">{html.escape(item.get("text", ""))}</div>' if item.get('text') else ''
    edit = (f'<a class="act" href="#" data-edit="{html.escape(item["id"])}" title="編輯" aria-label="編輯">'
            f'<svg viewBox="0 0 24 24">{ICONS["edit"]}</svg></a>')   # edited in place: see startEdit
    actions = _icon_link('copy', '複製', action_url('copy', 'u-' + item['id'])) + edit
    return f'<div class="bubble">{text}{files}</div><div class="actions">{actions}</div>'


def end_text(item: dict) -> str:
    return {'failed': '失敗' + (f"：{item['error']}" if item.get('error') else ''),
            'interrupted': '已停止'}.get(item.get('outcome', ''), '')


def units(items: list[dict], retry_id: str = '', running: bool = False) -> list[tuple[str, str, str, list]]:
    """(page id, kind, signature, the items it shows) in order; the signature changes when its look does.
    The newest turn while the Agent works is 'turn live' (its steps not folded away yet)."""
    out: list[tuple[str, str, str, list]] = []
    turn: list[dict] = []

    def close():
        shown = [i for i in turn if _has_content(i)]
        if shown:
            out.append(('t-' + shown[0]['id'], 'turn', json.dumps(shown, ensure_ascii=False, sort_keys=True), shown))
        turn.clear()

    for item in items:
        if item['kind'] == 'assistant':
            turn.append(item)
            continue
        close()
        if item['kind'] == 'user':
            out.append(('u-' + item['id'], 'user', json.dumps(item, ensure_ascii=False, sort_keys=True), [item]))
        elif end_text(item):
            retry = item['id'] == retry_id
            out.append(('e-' + item['id'], 'end', json.dumps([item, retry], ensure_ascii=False), [item]))
    close()
    if running and out and out[-1][1] == 'turn':
        page_id, _kind, signature, shown = out[-1]
        out[-1] = (page_id, 'turn live', signature + 'live', shown)
    return out


def unit_html(kind: str, shown: list[dict], retry_id: str, codes: dict, resolve=None) -> str:
    if kind == 'user':
        return user_html(shown[0], resolve)
    if kind.startswith('turn'):
        return turn_html(shown, codes, live='live' in kind)
    item = shown[0]
    retry = (_icon_link('retry', '重試', action_url('retry')) if item['id'] == retry_id else '')
    failed = ' failed' if item.get('outcome') == 'failed' else ''
    return f'<div class="end{failed}">{html.escape(end_text(item))}{retry}</div>'


def plain_text(kind: str, shown: list[dict]) -> str:
    if kind == 'user':
        return shown[0].get('text', '')
    return '\n\n'.join(p['text'] for i in shown for p in i.get('parts', []) if p['type'] == 'text' and p.get('text'))


# --------------------------------------------------------------------------- the page
def css(column: int) -> str:
    t = theme
    dark = t.is_dark()
    ink = t.INK if dark else '#1f2328'
    strong = '#ffffff' if dark else '#0d1117'
    soft = 'rgba(255,255,255,0.06)' if dark else 'rgba(15,23,42,0.045)'
    link = '#7aa7ff' if dark else '#1a66d2'
    size = round(14 * (1 + (t.TEXT_SCALE - 1) / 2), 1)   # answers grow only half as much as the rest
    try:
        from pygments.formatters import HtmlFormatter
        hl = HtmlFormatter(style='monokai' if dark else 'friendly').get_style_defs('.hl')
    except Exception:
        hl = ''
    return f"""
:root {{ color-scheme: {'dark' if dark else 'light'}; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; background: {t.CANVAS}; }}
body {{ color: {ink}; font: {size}px/1.75 "Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC",
        "Noto Sans CJK TC", sans-serif; -webkit-font-smoothing: antialiased; overflow-wrap: anywhere; }}
#page {{ max-width: {column}px; margin: 0 auto; padding: 18px 24px 28px; }}
.unit {{ margin-top: 14px; position: relative; }}
.unit:first-child {{ margin-top: 4px; }}
#older {{ display: none; text-align: center; margin: 4px 0 8px; }}
#older a {{ color: {t.MUTED}; text-decoration: none; font-size: .9em; }}
#status {{ display: none; color: {t.MUTED}; font-size: .9em; margin-top: 14px; }}
::selection {{ background: {t.ACCENT_SOFT}; }}

/* the user's own words */
.user {{ display: flex; flex-direction: column; align-items: flex-end; }}
.bubble {{ background: {t.LAVENDER}; color: {strong}; border-radius: 18px; padding: 9px 16px; max-width: 82%; }}
.bubble .text {{ white-space: pre-wrap; }}
.bubble .file {{ color: {t.MUTED}; font-size: .88em; }}

/* the user's message edited in place */
.bubble.editing {{ width: 82%; box-sizing: border-box; }}
.bubble.editing textarea {{ width: 100%; border: none; outline: none; resize: none; background: transparent;
                            color: inherit; font: inherit; padding: 0; overflow: hidden; }}
.edit-acts {{ display: flex; justify-content: flex-end; gap: 6px; margin-top: 6px; }}
.edit-acts button {{ border: none; border-radius: 8px; padding: 4px 12px; font: inherit; font-size: .9em;
                     cursor: pointer; background: transparent; color: {t.MUTED}; }}
.edit-acts button:hover {{ background: {soft}; }}
.edit-acts button.send {{ background: {strong}; color: {t.CANVAS}; }}
.unit.is-editing .actions {{ display: none; }}

/* buttons under a message, shown while the pointer is over it */
.actions {{ display: flex; gap: 2px; margin-top: 4px; opacity: 0; transition: opacity .12s; }}
.user .actions {{ justify-content: flex-end; }}
.unit:hover .actions {{ opacity: 1; }}
.act {{ display: inline-flex; width: 28px; height: 28px; align-items: center; justify-content: center;
        border-radius: 7px; color: {t.MUTED}; }}
.act:hover {{ background: {soft}; color: {strong}; }}
.act svg {{ width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8;
            stroke-linecap: round; stroke-linejoin: round; }}

/* answer text */
.md > :first-child {{ margin-top: 0; }}
.md > :last-child {{ margin-bottom: 0; }}
.md p {{ margin: 0 0 .7em; }}
.md h1, .md h2, .md h3, .md h4, .md h5, .md h6 {{ color: {strong}; font-weight: 650; line-height: 1.4;
        margin: 1.3em 0 .5em; }}
.md h1 {{ font-size: 1.3em; }} .md h2 {{ font-size: 1.15em; }} .md h3 {{ font-size: 1.05em; }}
.md h4, .md h5, .md h6 {{ font-size: 1em; }}
.md strong {{ color: {strong}; font-weight: 650; }}
.md a {{ color: {link}; text-decoration: none; font-weight: 600; }}
.md a:hover {{ text-decoration: underline; }}
.md ul, .md ol {{ margin: 0 0 .7em; padding-left: 1.5em; }}
.md li {{ margin: .2em 0; padding-left: .2em; }}
.md li > p {{ margin: 0 0 .3em; }}
.md li::marker {{ color: {t.MUTED}; }}
.md ol > li::marker {{ font-weight: 600; }}
.md blockquote {{ margin: 0 0 .7em; padding: .1em 0 .1em 1em; border-left: 3px solid {t.LINE}; color: {t.MUTED}; }}
.md hr {{ border: 0; border-top: 1px solid {t.LINE}; margin: 1.2em 0; }}
.md code {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: .88em; background: {soft};
            border-radius: 5px; padding: .12em .38em; }}
.md table {{ border-collapse: collapse; margin: .2em 0 .9em; display: block; overflow-x: auto; max-width: 100%; }}
.md th, .md td {{ border: 1px solid {t.LINE}; padding: .45em .8em; text-align: left; vertical-align: top; }}
.md th {{ background: {soft}; color: {strong}; font-weight: 650; }}

/* code blocks */
.code {{ border: 1px solid {t.LINE}; border-radius: 10px; margin: .3em 0 .9em; overflow: hidden;
         background: {'#15181c' if dark else '#f7f8fa'}; }}
.code-head {{ display: flex; justify-content: space-between; align-items: center; padding: 4px 8px 0 12px;
              color: {t.MUTED}; font-size: .8em; }}
.md .code-copy {{ font-weight: 400; color: {t.MUTED}; text-decoration: none; padding: 2px 8px; border-radius: 6px; }}
.md .code-copy:hover {{ text-decoration: none;  background: {soft}; color: {strong}; }}
.code pre {{ margin: 0; padding: 6px 14px 12px; overflow-x: auto; font: .86em/1.6 "Cascadia Mono", Consolas, monospace; }}
.code pre code {{ background: none; padding: 0; font-size: 1em; }}
{hl}
.hl {{ background: transparent !important; }}

/* thinking and tools: one quiet line each, opened on click */
details > summary {{ list-style: none; cursor: pointer; color: {t.MUTED}; font-size: .9em; width: fit-content;
                     border-radius: 6px; padding: 1px 6px; margin-left: -6px; user-select: none; }}
details > summary::-webkit-details-marker {{ display: none; }}
details > summary::before {{ content: "›"; display: inline-block; width: 1em; transition: transform .12s; }}
details[open] > summary::before {{ transform: rotate(90deg); }}
details > summary:hover {{ color: {strong}; background: {soft}; }}
.steps, .tool, .think, .tool.line {{ margin: 0 0 .5em; }}
.tool.line {{ color: {t.MUTED}; font-size: .9em; padding-left: 1em; }}
.steps-body {{ border-left: 2px solid {t.LINE}; margin: .3em 0 .2em .3em; padding-left: .8em; }}
.tname {{ font-weight: 600; }}
.targ {{ font-family: "Cascadia Mono", Consolas, monospace; font-size: .92em; }}
.mark {{ margin-left: .4em; }} .mark.err {{ color: {t.DANGER}; }}
.tdetail, .diff {{ margin: .3em 0 .4em; padding: 8px 12px; border: 1px solid {t.LINE}; border-radius: 8px;
                   background: {t.CARD}; font: .82em/1.55 "Cascadia Mono", Consolas, monospace; white-space: pre-wrap;
                   max-height: 360px; overflow: auto; }}
.diff .add {{ color: {t.OK}; }} .diff .del {{ color: {t.DANGER}; }} .diff .hunk {{ color: {t.MUTED}; }}
.think-body {{ color: {t.MUTED}; font-size: .92em; border-left: 2px solid {t.LINE}; margin: .3em 0 .4em .3em;
               padding-left: .8em; }}
.think-body strong {{ color: inherit; }}
.process-body {{ border-left: 2px solid {t.LINE}; margin: .3em 0 .2em .3em; padding-left: .8em; }}
.process-body .md {{ color: {t.MUTED}; font-size: .94em; margin-bottom: .5em; }}
hr.sep {{ border: 0; border-top: 1px solid {t.LINE}; margin: .5em 0 1em; }}
.bubble .images {{ display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 4px; justify-content: flex-end; }}
.bubble img {{ max-height: 220px; max-width: 100%; border-radius: 10px; display: block; }}
.err, .end.failed {{ color: {t.DANGER}; }}
.end {{ color: {t.MUTED}; font-size: .9em; display: flex; align-items: center; gap: 6px; }}

/* the ↓ while reading older messages, and a soft edge above the message box */
#latest {{ position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); width: 34px; height: 34px;
           border-radius: 17px; border: 1px solid {t.LINE}; background: {t.CARD}; color: {t.MUTED};
           display: none; align-items: center; justify-content: center; cursor: pointer;
           box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
#latest:hover {{ color: {strong}; }}
#latest svg {{ width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }}
#fade {{ position: fixed; left: 0; right: 0; bottom: 0; height: 22px; pointer-events: none;
         background: linear-gradient(to bottom, transparent, {t.CANVAS}); }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-thumb {{ background: {t.LINE}; border-radius: 5px; border: 2px solid {t.CANVAS}; }}
::-webkit-scrollbar-thumb:hover {{ background: {t.DIM}; }}
"""


SCRIPT = r"""
const log = document.getElementById('log');
const root = document.scrollingElement;
let stick = true;
function fromBottom() { return root.scrollHeight - root.scrollTop - innerHeight; }
function showLatest() { document.getElementById('latest').style.display = fromBottom() > 200 ? 'flex' : 'none'; }
addEventListener('scroll', () => { stick = fromBottom() < 40; showLatest(); });
addEventListener('resize', () => { if (stick) root.scrollTop = root.scrollHeight; showLatest(); });
function toBottom() { stick = true; root.scrollTop = root.scrollHeight; showLatest(); }
function goTo(below) { stick = false; root.scrollTop = root.scrollHeight - innerHeight - below; showLatest(); }
document.addEventListener('click', e => {
  const link = e.target.closest('a[data-edit]');
  if (link) { e.preventDefault(); startEdit(link.closest('.unit'), link.dataset.edit); }
});
// The bubble turns into a box holding the message; sending replaces it and everything after it.
function startEdit(unit, id) {
  const open = unit.querySelector('.editing textarea');
  if (open) { open.focus(); return; }
  const bubble = unit.querySelector('.bubble');
  const box = document.createElement('div');
  box.className = 'bubble editing';
  box.innerHTML = '<textarea rows="1"></textarea><div class="edit-acts"><button class="cancel">取消</button>'
    + '<button class="send">送出</button></div>';
  const area = box.querySelector('textarea');
  const text = bubble.querySelector('.text');
  area.value = text ? text.textContent : '';
  const fit = () => { area.style.height = 'auto'; area.style.height = area.scrollHeight + 'px'; };
  const close = () => { box.remove(); bubble.style.display = ''; unit.classList.remove('is-editing'); };
  const send = () => {
    const value = area.value.trim();
    if (!value) return;
    close();
    location.href = EDIT_URL + encodeURIComponent(JSON.stringify([id, value]));
  };
  area.addEventListener('input', fit);
  area.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') close();
  });
  box.querySelector('.cancel').onclick = close;
  box.querySelector('.send').onclick = send;
  bubble.style.display = 'none';
  bubble.after(box);
  unit.classList.add('is-editing');
  fit();
  area.focus({preventScroll: true});
  area.setSelectionRange(area.value.length, area.value.length);
}
function apply(order, kinds, parts, older, status) {
  const first = log.firstElementChild ? log.firstElementChild.id : '';
  const below = root.scrollHeight - root.scrollTop;
  const keep = new Set(order);
  for (const el of [...log.children]) if (!keep.has(el.id)) el.remove();
  let prev = null;
  for (const id of order) {
    let el = document.getElementById(id);
    if (!el) { el = document.createElement('div'); el.id = id; }
    el.className = 'unit ' + kinds[id];
    if (id in parts) {
      // Thinking open only because the answer was live closes with it; what the reader opened stays.
      const open = new Set([...el.querySelectorAll('details[open]:not([data-live])')].map(d => d.dataset.key));
      el.innerHTML = parts[id];
      for (const d of el.querySelectorAll('details')) if (open.has(d.dataset.key)) d.open = true;
    }
    const want = prev ? prev.nextSibling : log.firstChild;
    if (want !== el) log.insertBefore(el, want);
    prev = el;
  }
  document.getElementById('older').style.display = older ? 'block' : 'none';
  const line = document.getElementById('status');
  line.textContent = status; line.style.display = status ? 'block' : 'none';
  if (stick) root.scrollTop = root.scrollHeight;
  else if (first && log.firstElementChild && log.firstElementChild.id !== first) root.scrollTop = root.scrollHeight - below;
  showLatest();
}
"""


def page_html(column: int) -> str:
    down = '<svg viewBox="0 0 24 24"><path d="M12 5v14M19 12l-7 7-7-7"/></svg>'
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{css(column)}</style></head><body>'
            f'<div id="page"><div id="older"><a href="{action_url("older")}">載入更早的訊息</a></div>'
            f'<div id="log"></div><div id="status"></div></div>'
            f'<div id="latest" onclick="toBottom()" title="最新訊息" aria-label="最新訊息">{down}</div><div id="fade"></div>'
            f'<script>const EDIT_URL = {json.dumps(action_url("edit"))};{SCRIPT}</script></body></html>')


# --------------------------------------------------------------------------- the Qt side
def make_view(owner):
    """A QWebEngineView showing the conversation; owner gets on_action(action, argument)."""
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QColor, QDesktopServices
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import QMenu

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self, url, kind, main):  # noqa: N802
            if url.host() == ACTION_HOST:
                parts = url.path().strip('/').split('/', 1)
                owner.on_action(parts[0], unquote(parts[1]) if len(parts) > 1 else '')
                return False
            if kind == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                if url.scheme() in ('http', 'https'):
                    from agentdock.ui.external_tools import open_target
                    open_target('browser', url.toString())
                return False
            return url.scheme() in ('about', 'data')

        def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                logging.warning('transcript page: %s (%s)', message, line)

    class View(QWebEngineView):
        def contextMenuEvent(self, event):  # noqa: N802
            menu = QMenu(self)
            copy = menu.addAction('複製', lambda: self.page().triggerAction(QWebEnginePage.WebAction.Copy))
            copy.setEnabled(self.page().hasSelection())
            menu.addAction('全選', lambda: self.page().triggerAction(QWebEnginePage.WebAction.SelectAll))
            menu.exec(event.globalPos())

    view = View()
    page = Page(view)
    view.setPage(page)
    settings = page.settings()
    for attribute, on in ((QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False),
                          (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False),
                          (QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False),
                          (QWebEngineSettings.WebAttribute.PluginsEnabled, False)):
        settings.setAttribute(attribute, on)
    page.setBackgroundColor(QColor(theme.CANVAS or '#ffffff'))
    view.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
    view.page_url = QUrl('about:blank')
    return view


class WebTranscript:
    """Keeps the page in step with a Transcript: only parts whose look changed are sent again."""

    def __init__(self, column: int, on_action):
        self.column = column
        self.on_action_ = on_action
        self.view = make_view(self)
        self.view.loadFinished.connect(self._loaded)
        self.ready = False
        self.style = ''
        self.sent: dict[str, str] = {}      # page id → signature on the page
        self.html: dict[str, str] = {}      # signature → html (rendering Markdown is the costly part)
        self.copies: dict[str, str] = {}    # page id → text the copy button takes
        self.codes: dict[str, str] = {}     # code block key → code
        self.state = ([], '', False, '', False)    # items, retry id, older, status, running
        self.resolve = None                 # file name → path, for pictures in the user's messages
        self.restyle()

    def on_action(self, action: str, arg: str) -> None:
        if action == 'copy':
            self.on_action_('copy', self.copies.get(arg, ''))
        elif action == 'code':
            self.on_action_('copy', self.codes.get(arg, ''))
        else:
            self.on_action_(action, arg)

    def restyle(self) -> None:
        """A new look or text size: the page is built again and everything resent once it loads."""
        style = css(self.column)
        if style == self.style:
            return
        self.style = style
        self.ready = False
        self.sent = {}
        from PySide6.QtGui import QColor
        self.view.page().setBackgroundColor(QColor(theme.CANVAS or '#ffffff'))
        self.view.setHtml(page_html(self.column), self.view.page_url)

    def _loaded(self, ok: bool) -> None:
        # Rejected action-link navigations also emit loadFinished(False), while
        # the existing document is still usable. Only restyle invalidates it.
        if ok:
            self.ready = True
            self.show(*self.state)

    def clear(self) -> None:
        self.html.clear()
        self.codes.clear()
        self.show([], '', False, '', False)

    def show(self, items: list[dict], retry_id: str, older: bool, status: str, running: bool = False) -> None:
        self.state = (items, retry_id, older, status, running)
        if not self.ready:
            return
        order, kinds, parts = [], {}, {}
        for page_id, kind, signature, shown in units(items, retry_id, running):
            order.append(page_id)
            kinds[page_id] = kind
            self.copies[page_id] = plain_text(kind, shown)
            if self.sent.get(page_id) != signature:
                if signature not in self.html:
                    self.html[signature] = unit_html(kind, shown, retry_id, self.codes, self.resolve)
                parts[page_id] = self.html[signature]
                self.sent[page_id] = signature
        for gone in set(self.sent) - set(order):
            self.sent.pop(gone, None)
        live = set(self.sent.values())   # streaming makes a new signature per update; drop the old ones
        self.html = {s: h for s, h in self.html.items() if s in live}
        self.view.page().runJavaScript(
            f'apply({json.dumps(order)}, {json.dumps(kinds)}, {json.dumps(parts, ensure_ascii=False)}, '
            f'{json.dumps(older)}, {json.dumps(status, ensure_ascii=False)})')

    def to_bottom(self) -> None:
        if self.ready:
            self.view.page().runJavaScript('toBottom()')

    def where(self, done) -> None:
        """How far above the bottom the page is scrolled (to `done`, later)."""
        if self.ready:
            self.view.page().runJavaScript('stick ? 0 : fromBottom()', 0, done)

    def go_to(self, from_bottom: int) -> None:
        if self.ready:
            self.view.page().runJavaScript(f'goTo({int(from_bottom)})')

    def find(self, text: str, backward: bool, done) -> None:
        from PySide6.QtWebEngineCore import QWebEnginePage
        flags = QWebEnginePage.FindFlag.FindBackward if backward else QWebEnginePage.FindFlag(0)
        self.view.page().findText(text, flags, lambda r: done(r.activeMatch(), r.numberOfMatches()))
