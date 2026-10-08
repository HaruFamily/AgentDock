"""Theme-aware question Markdown, with no HTML or resource loading from input."""
import re

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QSizePolicy, QTextBrowser

from agentdock.ui import theme


class LocalTextDocument(QTextDocument):
    def loadResource(self, resource_type, url):  # noqa: N802
        return None


def sections(text):
    """Recognize standalone directives, but never inside fenced code blocks."""
    output, lines, kind, fence = [], [], '', None
    for line in text.splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            run, tail = marker.groups()
            if fence is None:
                fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not tail.strip():
                fence = None
            lines.append(line)
            continue
        if fence is None:
            if not kind and line in (':::warning', ':::note'):
                output.append(('', '\n'.join(lines)))
                lines, kind = [], line[3:]
                continue
            if kind and line == ':::':
                output.append((kind, '\n'.join(lines)))
                lines, kind = [], ''
                continue
        lines.append(line)
    # Unclosed directives remain literal instead of swallowing the rest of the question.
    output.append(('', (':::' + kind + '\n' if kind else '') + '\n'.join(lines)))
    return [(kind, body) for kind, body in output if body.strip()]


def question_html(text):
    parts = []
    for kind, body in sections(text):
        doc = LocalTextDocument()
        font = QFont(QApplication.font())
        font.setPixelSize(12 if kind == 'note' else 14)
        doc.setDefaultFont(font)
        doc.setMarkdown(body, QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                        QTextDocument.MarkdownFeature.MarkdownNoHTML)
        html = re.search(r'<body[^>]*>(.*)</body>', doc.toHtml(), re.S).group(1)
        html = re.sub(r'<img\b[^>]*>', '', html, flags=re.I)
        color = theme.MUTED if kind == 'note' else theme.WARN_INK if kind == 'warning' else theme.INK
        def style_span(match):
            style = match.group(1)
            if 'font-family:' in style:
                style += f' color:{color}; background-color:{theme.ACCENT_SOFT};'
            return f'<span style="{style}">'
        html = re.sub(r'<span style="([^"]*)">', style_span, html)
        size = 12 if kind == 'note' else 14
        if kind == 'warning':
            html = f'<table width="100%" cellspacing="0" cellpadding="8" bgcolor="{theme.WARN_BG}"><tr><td><b>注意</b>{html}</td></tr></table>'
        parts.append(f'<div style="color:{color}; font-size:{size}px;">{html}</div>')
    return '<html><body>' + ''.join(parts) + '</body></html>'


class QuestionText(QTextBrowser):
    """Read-only content that grows inside the existing question scroll area."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.source = text
        self.setDocument(LocalTextDocument(self))
        self.setObjectName('QuestionMarkdown')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setStyleSheet('QTextBrowser { background: transparent; border: none; padding: 0; }')
        self.refresh_theme()

    def refresh_theme(self):
        cursor = self.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        self.setHtml(question_html(self.source))
        block = self.document().begin()
        while block.isValid():
            block_cursor = QTextCursor(block)
            fmt = block.blockFormat()
            if fmt.nonBreakableLines():
                fmt.setBackground(QColor(theme.ACCENT_SOFT))
            fmt.setNonBreakableLines(False)
            fmt.setBottomMargin(4 if block.textList() else 10)
            fmt.setLineHeight(135, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            block_cursor.setBlockFormat(fmt)
            block = block.next()
        self.document().setDocumentMargin(0)
        cursor.setPosition(min(anchor, self.document().characterCount() - 1))
        cursor.setPosition(min(position, self.document().characterCount() - 1), cursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self._fit()

    def _fit(self):
        self.document().setTextWidth(max(1, self.viewport().width()))
        self.setFixedHeight(int(self.document().size().height()) + 4)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange and hasattr(self, 'source'):
            self.refresh_theme()
