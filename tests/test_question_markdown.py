import asyncio
import os
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument

from agentdock.ui import theme
from agentdock.ui.question_text import QuestionText, question_html, sections


def test_question_markdown_theme_and_literal_code(theme_state):
    source = '### 要重建嗎？\n\n**重點** 與 `UsesCost`\n\n- 一\n- 二\n\n:::warning\n會覆蓋資料\n:::\n\n:::note\n補充資料\n:::\n\n```text\n:::warning\n原樣程式\n:::\n```\n\n<b>不是 HTML</b>'
    for name in ('clean', 'white_orange'):
        theme.apply(name)
        html = question_html(source)
        doc = QTextDocument()
        doc.setHtml(html)
        plain = doc.toPlainText()
        assert '**' not in plain and '###' not in plain
        assert '會覆蓋資料' in plain and '補充資料' in plain
        assert ':::warning\n原樣程式\n:::' in plain
        assert '<b>不是 HTML</b>' in plain
        assert theme.WARN_BG in html and theme.MUTED in html
        note = doc.find('補充資料').charFormat()
        normal = doc.find('重點').charFormat()
        assert note.font().pixelSize() < normal.font().pixelSize()
        assert doc.find('會覆蓋資料').charFormat().foreground().color().name() == theme.WARN_INK
    assert sections(':::note\n未關閉') == [('', ':::note\n未關閉')]
    assert '<img' not in question_html('![x](file:///private.png)')


def test_question_widget_wraps_code_and_restyles(ui_app, theme_state):
    view = QuestionText('普通段落\n\n:::note\n補充\n:::\n\n```\n' + 'very_long_name_' * 50 + '\n```')
    try:
        view.resize(270, view.height())
        view.show()
        ui_app.processEvents()
        assert view.height() >= view.document().size().height()
        assert view.document().size().width() <= 270
        before = view.toPlainText()
        theme.apply('white_orange')
        view.refresh_theme()
        assert view.toPlainText() == before
        assert view.document().find('補充').charFormat().foreground().color().name() == theme.MUTED
        assert view.document().loadResource(QTextDocument.ResourceType.ImageResource, QUrl('file:///private.png')) is None
    finally:
        view.close()
        view.deleteLater()


def test_mcp_publishes_format_guidance_without_gui_imports():
    from agentdock.mcp_server import mcp
    tools = asyncio.run(mcp.list_tools())
    ask = next(t for t in tools if t.name == 'ask_user')
    assert ':::warning' in ask.description
    descriptions = [
        ask.inputSchema['properties']['question']['description'],
        ask.inputSchema['$defs']['QuestionItem']['properties']['question']['description'],
        ask.inputSchema['$defs']['OptionInput']['properties']['description']['description'],
    ]
    for description in descriptions:
        assert 'Markdown' in description and ':::note' in description
    result = subprocess.run([sys.executable, '-c', 'import sys; import agentdock.mcp_server; assert not any(n.startswith("PySide6") for n in sys.modules)'], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
