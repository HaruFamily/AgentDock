import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextDocument
from agentdock.ui.qa_view import message_html


def test_markdown_formats_text_without_losing_quotes_or_interpreting_html():
    app = QApplication.instance() or QApplication([])
    html = message_html("**粗體** 與 `code`，保留 '原文'\n\n- 選項\n\n```python\nprint('hi')\n```\n\n<b>literal</b>")
    doc = QTextDocument()
    doc.setHtml(html)
    plain = doc.toPlainText()
    assert '**' not in plain and '`' not in plain
    assert "'原文'" in plain and "print('hi')" in plain
    assert '<b>literal</b>' in plain
    assert 'font-weight:700' in html and '<li' in html
    assert '<img' not in message_html('![image](file:///private/image.png)')
    colored = message_html('**bold** *italic* `code`')
    assert all(color in colored for color in ('#7DCCFF', '#DBAFF7', '#FFD580', '#30343B'))
