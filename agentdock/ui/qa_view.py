"""Inbox: task conversations with shared question and report history."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut, QTextDocument
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QTextEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget, QMessageBox)

from agentdock.qa.store import QuestionStore
from agentdock.ui import theme
from agentdock.ui.widgets import ElidedLabel, RibbonBar, Row, dot, icon_button, muted

HINTS = {"text": "用文字回答，也可以加入檔案或貼上圖片。",
         "single": "選一項，或直接在下方輸入自己的答案。",
         "multiple": "可選多項，也可以在下方補充想法。"}


def button(text: str, *, primary: bool = False, danger: bool = False, flat: bool = False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if flat:
        b.setProperty("flat", True)
    return b


_LONG = re.compile(r"\S{24,}")


def breakable(text: str) -> str:
    """Let long paths/URLs wrap: add zero-width break points after separators."""
    return _LONG.sub(lambda m: re.sub(r"([\\/._\-:?&=,])", "\\1\u200b", m.group(0)), text)


def message_html(text: str) -> str:
    """Render Markdown without interpreting source HTML or loading inline images."""
    document = QTextDocument()
    document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                         QTextDocument.MarkdownFeature.MarkdownNoHTML)
    html = re.sub(r'<img\b[^>]*>', '', document.toHtml(), flags=re.IGNORECASE)
    def color_span(match):
        style = match.group(1)
        if 'font-family:' in style:
            style += ' color:#FFD580; background-color:#30343B;'
        elif 'font-weight:700' in style or 'font-weight:600' in style:
            style += ' color:#7DCCFF;'
        elif 'font-style:italic' in style:
            style += ' color:#DBAFF7;'
        return '<span style="' + style + '">'
    return re.sub(r'<span style="([^"]*)">', color_span, html)


def label(text: str, *, muted: bool = False, wrap: bool = True, name: str | None = None) -> QLabel:
    lab = QLabel(breakable(text) if wrap and "<" not in text else text)
    lab.setTextFormat(Qt.TextFormat.PlainText)
    lab.setWordWrap(wrap)
    if wrap:
        lab.setMinimumWidth(0)
        lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if muted:
        lab.setProperty("muted", True)
    if name:
        lab.setObjectName(name)
    return lab


def chip(text: str, kind: str = "Chip") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(kind)
    return lab


class ImageDialog(QDialog):
    def __init__(self, pixmap: QPixmap, caption: str, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Dialog)
        self.setWindowTitle(caption or "圖片")
        layout = QVBoxLayout(self)
        screen = self.screen().availableGeometry()
        img = QLabel()
        img.setPixmap(pixmap.scaled(int(screen.width() * 0.7), int(screen.height() * 0.7),
                                    Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                      if pixmap.width() > screen.width() * 0.7 or pixmap.height() > screen.height() * 0.7 else pixmap)
        layout.addWidget(img)
        if caption:
            layout.addWidget(label(caption, muted=True))


class ClickableImage(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class FullRowCheckBox(QCheckBox):
    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)


class FullRowRadioButton(QRadioButton):
    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)


class OptionRow(QWidget):
    """Click the row background to select; embedded inputs keep their own clicks."""

    def __init__(self, choice) -> None:
        super().__init__()
        self.choice = choice
        self._press = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None:
            pos, self._press = self._press, None
            released = e.position().toPoint()
            if (self.rect().contains(released)
                    and (released - pos).manhattanLength() < QApplication.startDragDistance()):
                self.choice.click()
            e.accept()
        else:
            super().mouseReleaseEvent(e)


class PasteTextEdit(QPlainTextEdit):
    """Ctrl+V of an image adds it as an attachment instead of text."""
    image_pasted = Signal(bytes)

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        if source.hasImage():
            image = QImage(source.imageData())
            data = QByteArray()
            buf = QBuffer(data)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buf, "PNG")
            self.image_pasted.emit(bytes(data))
            return
        if source.hasUrls() and all(u.isLocalFile() for u in source.urls()):
            for u in source.urls():
                self.parent_view.add_file(Path(u.toLocalFile()))  # type: ignore[attr-defined]
            return
        super().insertFromMimeData(source)


class AutoGrowText(PasteTextEdit):
    """Multi-line input that grows with its content (1 line → max_lines), so notes can wrap."""

    def __init__(self, text: str = "", placeholder: str = "", max_lines: int = 8) -> None:
        super().__init__()
        self.setObjectName("Grow")
        self.max_lines = max_lines
        self._hint = placeholder
        self.setPlaceholderText(placeholder)
        self.setPlainText(text)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.document().documentLayout().documentSizeChanged.connect(lambda _s: self._fit())
        self._fit()

    def _fit(self) -> None:
        line = self.fontMetrics().lineSpacing()
        needed = max(1, int(self.document().size().height() + 0.5))  # plain-text layout reports height in lines
        lines = min(self.max_lines, needed)
        chrome = int(2 * self.document().documentMargin()) + 2 * self.frameWidth() + 2
        self.setFixedHeight(lines * line + chrome)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if needed > self.max_lines
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    # Qt keeps drawing the placeholder while an IME (e.g. 注音/倉頡) is composing, because the document is
    # still empty; the hint then overlaps the characters being typed. Hide it while the box has focus.
    def focusInEvent(self, e) -> None:  # noqa: N802
        self.setPlaceholderText("")
        if callable(getattr(self, "focused", None)):
            self.focused()
        super().focusInEvent(e)

    def focusOutEvent(self, e) -> None:  # noqa: N802
        self.setPlaceholderText(self._hint)
        super().focusOutEvent(e)
        if callable(getattr(self, "blurred", None)):
            self.blurred()


def _when(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except Exception:
        return ""
    return t.strftime("%H:%M") if t.date() == datetime.now().date() else t.strftime("%m/%d %H:%M")


class QaView(QWidget):
    pending_changed = Signal(int)
    pending_items = Signal(list)  # [{id, source, question}] — one per pending unit, for the floating card
    answered = Signal()           # an answer was submitted from this view
    closed = Signal()
    read_finished = Signal()
    PAGE_SIZE = 30
    PREVIEW_CHARS = 3000

    def __init__(self, store: QuestionStore, *, inline: bool = False) -> None:
        super().__init__()
        self.store = store
        self.inline = inline
        self.active: str | None = None
        self.history = False
        self.conversation: tuple[str, str] | None = None
        self._timeline_key = ""
        self._list_key = None
        self._list_limit = 60
        self._timeline_pages = [None]
        self._timeline_widgets = {}
        self.read_finished.connect(self.refresh)
        self.drafts: dict[str, dict[str, Any]] = {}
        self._rendered_key = ""
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self._save_draft)
        self._notice_timer = QTimer(self, singleShot=True, interval=9000)
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.notice = QLabel("")
        self.notice.setObjectName("Toast")
        self.notice.setProperty("error", True)
        self.notice.setWordWrap(True)
        self.notice.hide()
        self._notice_timer.timeout.connect(self.notice.hide)
        root.addWidget(self.notice)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        if inline:
            self.stack.addWidget(QWidget())
        else:
            # list page ---------------------------------------------------------
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            tabs = QHBoxLayout()
            tabs.setContentsMargins(16, 10, 14, 6)
            tabs.setSpacing(14)
            tabs.addWidget(label("Inbox", wrap=False))
            tabs.addStretch()
            tabs.addWidget(label("問答與任務結果", muted=True, wrap=False))
            lay.addLayout(tabs)
            rule = QFrame()
            rule.setObjectName("Rule")
            rule.setFixedHeight(1)
            lay.addWidget(rule)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.list_body = QWidget()
            self.list_lay = QVBoxLayout(self.list_body)
            self.list_lay.setContentsMargins(12, 6, 12, 12)
            self.list_lay.setSpacing(0)
            scroll.setWidget(self.list_body)
            lay.addWidget(scroll, 1)
            self.stack.addWidget(page)

        self.detail = QWidget()
        self.stack.addWidget(self.detail)
        self.timeline = QWidget()
        self.stack.addWidget(self.timeline)
        self._submit_now = None
        for keys in ("Ctrl+Return", "Ctrl+Enter"):
            shortcut = QShortcut(QKeySequence(keys), self, activated=lambda: self._submit_now and self._submit_now())
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.refresh()

    # ------------------------------------------------------------------ list
    def refresh(self) -> None:
        if not self.inline:
            self.refresh_list()
        if self.stack.currentIndex() == 2:
            self._refresh_timeline()
        if self.active and self.stack.currentIndex() == 1:
            try:
                members = self.store.group_members(self.active)
            except KeyError:
                self.back()
                return
            if self._key(members) != self._rendered_key:
                self.render(members[0])
            else:
                self._update_waiting(members)

    def tick(self) -> None:
        """Periodic, cheap: only updates the open question's connection label (no rebuild)."""
        if self.active and self.stack.currentIndex() == 1:
            try:
                self._update_waiting(self.store.group_members(self.active))
            except KeyError:
                pass

    @staticmethod
    def _key(members: list[dict[str, Any]]) -> str:
        return "|".join(f"{m['id']}/{m['status']}" for m in members)

    @staticmethod
    def _units(questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        """Questions asked together (a group) are one unit; everything else is a unit of one."""
        units: dict[str, list[dict[str, Any]]] = {}
        for q in questions:
            units.setdefault(q["group"]["id"] if q.get("group") else q["id"], []).append(q)
        return [sorted(u, key=lambda q: q.get("group", {}).get("index", 0)) for u in units.values()]

    @staticmethod
    def _unit_status(unit: list[dict[str, Any]]) -> str:
        states = {q["status"] for q in unit}
        return "cancelled" if "cancelled" in states else "answered" if states == {"answered"} else "pending"

    def refresh_list(self) -> None:
        units = self._units(self.store.visible_questions())
        pending = [u for u in units if self._unit_status(u) == "pending"]
        self.pending_changed.emit(len(pending))
        self.pending_items.emit([{"id": u[0]["id"], "source": u[0].get("source", ""), "question": u[0].get("question", "")}
                                 for u in sorted(pending, key=lambda u: u[0]["created_at"])])
        all_work = self.store.conversation_summaries()
        shown = all_work[:self._list_limit]
        key = [(w["owner"], w["work_id"], w["work_title"], w["source"], w["state"]) for w in shown]
        key = (key, len(all_work))
        if key == self._list_key:
            return
        self._list_key = key
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        agents: dict[str, list[dict]] = {}
        for work in shown:
            agents.setdefault(work["source"], []).append(work)
        for source, tasks in agents.items():
            heading = label(source, wrap=False)
            heading.setObjectName("AgentHeading")
            heading.setContentsMargins(4, 14, 0, 4)
            self.list_lay.addWidget(heading)
            for index, work in enumerate(tasks):
                self.list_lay.addWidget(self._task_row(work, index == len(tasks) - 1))
        if not shown:
            empty = muted("尚無待辦或通知。Agent 提問或回報任務結果後，會出現在這裡。")
            empty.setWordWrap(True)
            empty.setContentsMargins(4, 12, 4, 0)
            self.list_lay.addWidget(empty)
        if len(all_work) > len(shown):
            more = button(f"載入更多對話（還有 {len(all_work) - len(shown)} 個）", flat=True)
            more.clicked.connect(self._more_conversations)
            self.list_lay.addWidget(more)
        self.list_lay.addStretch()

    def _more_conversations(self):
        self._list_limit += 60
        self.refresh_list()

    def _task_row(self, work: dict, last: bool) -> QWidget:
        row = QWidget()
        row.setObjectName("Row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 4, 4, 4)
        layout.addWidget(label("└" if last else "├", muted=True, wrap=False))
        state = {"waiting": "待回覆", "running": "進行中", "unread": "未讀取"}.get(work["state"], "")
        title = work["work_title"]
        open_btn = button(title, flat=True)
        open_btn.setMinimumWidth(0)
        open_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        open_btn.setStyleSheet("text-align: left;")
        open_btn.setToolTip(title)
        key = (work["owner"], work["work_id"])
        open_btn.clicked.connect(lambda _=False, k=key: self.open_conversation(*k))
        layout.addWidget(open_btn, 1)
        if state:
            layout.addWidget(chip(state))
        remove = icon_button("×", "移除此對話的本機顯示歷史（不取消 Agent 任務）", danger=True)
        remove.clicked.connect(lambda _=False, k=key: self._remove_conversation(*k))
        layout.addWidget(remove)
        return row

    @staticmethod
    def _chat_messages(entries: list[dict]) -> list[dict]:
        """Answers use their submission time, rather than their question's creation time."""
        messages = []
        for entry in entries:
            if entry["kind"] != "question":
                if entry.get("external") and not entry["text"] and not entry.get("images"):
                    continue  # Lifecycle records update state without empty chat bubbles.
                messages.append({**entry, "side": "right" if entry["kind"] == "user_message" else "left"})
                continue
            q = entry
            messages.append({**q, "text": q["question"], "side": "left", "qid": q["id"]})
            if q.get("answer"):
                answer = q["answer"]
                parts = [f"• {o['label']}" for o in q["options"] if o["id"] in answer["selected"]]
                if answer.get("text"):
                    parts.append(answer["text"])
                for option in q["options"]:
                    note = answer.get("notes", {}).get(option["id"])
                    if note:
                        parts.append(f"{option['label']}：{note}")
                if not parts:
                    parts.append("已提交附件" if answer["attachment_ids"] else "已提交回答")
                text = "\n".join(parts)
                messages.append({"id": q["id"] + "/answer", "kind": "answer", "text": text, "side": "right",
                                 "created_at": q["resolved_at"], "qid": q["id"],
                                 "images": [a for a in q["uploads"] if a["id"] in answer["attachment_ids"] and a["mime"].startswith("image/")]})
            elif q["status"] == "cancelled":
                messages.append({"id": q["id"] + "/cancel", "kind": "question_cancelled", "text": "已取消，未傳送答案。",
                                 "side": "right", "created_at": q["resolved_at"]})
        return sorted(messages, key=lambda message: message["created_at"])

    def _chat_bubble(self, message: dict) -> tuple[QWidget, QLabel]:
        right = message["side"] == "right"
        row = QWidget()
        row.setObjectName("ChatMessage")
        row.setProperty("chatSide", message["side"])
        row.setProperty("messageKind", message["kind"])
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(0)
        bubble = QFrame()
        bubble.setObjectName("ChatBubble")
        bubble.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bubble.setStyleSheet(f"QFrame#ChatBubble {{ background: {theme.CARD}; border: 1px solid {theme.LINE}; border-radius: 6px; }}")
        content = QVBoxLayout(bubble)
        content.setContentsMargins(12, 9, 12, 10)
        content.setSpacing(6)
        kind = message["kind"]
        status = {"answer": "你的回答", "completed": "已完成", "failed": "失敗",
                  "cancelled": "已取消", "question_cancelled": "已取消"}.get(kind, "問題")
        if kind == "question":
            status = "待回答" if message.get("status") == "pending" else "已回答的問題" if message.get("status") == "answered" else "已取消的問題"
        caption = label(f"{status} · {_when(message['created_at'])}", muted=True)
        content.addWidget(caption)
        preview = self._message_preview(message["text"])
        text = label(preview)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setText(message_html(preview))
        content.addWidget(text)
        text.setVisible(bool(message["text"]))
        for meta in message.get("images", []):
            if meta.get("unavailable"):
                content.addWidget(label("圖片無法取得", muted=True))
                continue
            try:
                if message.get("qid"):
                    _, data = self.store.asset(message["qid"], meta["id"])
                else:
                    data = (self.store.assets / meta["id"]).read_bytes()
                pix = QPixmap()
                pix.loadFromData(data)
                if pix.isNull():
                    raise ValueError("invalid image")
                thumb = ClickableImage()
                thumb.setObjectName("ChatImage")
                thumb.setCursor(Qt.CursorShape.PointingHandCursor)
                thumb.setPixmap(pix.scaled(180, 140, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
                thumb.clicked.connect(lambda p=pix, m=meta: ImageDialog(p, m.get("name", "圖片"), self).exec())
                content.addWidget(thumb)
            except (OSError, ValueError, KeyError):
                content.addWidget(label("圖片無法取得", muted=True))
        full = button("查看全文", flat=True)
        full.setVisible(len(message["text"]) > self.PREVIEW_CHARS)
        # Read the latest text at click time; streamed updates keep the same button.
        row._full_text = message["text"]
        full.clicked.connect(lambda: self._show_message(row._full_text))
        content.addWidget(full)
        row._full_button = full
        if message.get("qid"):
            pending = message["kind"] == "question" and message["status"] == "pending"
            action = button("回答問題" if pending else "查看詳細資料", flat=not pending, primary=pending)
            action.clicked.connect(lambda _=False, qid=message["qid"]: self.open_question(qid))
            content.addWidget(action)
        layout.addWidget(bubble, 1)
        return row, text

    def _message_preview(self, text: str) -> str:
        return text if len(text) <= self.PREVIEW_CHARS else text[:self.PREVIEW_CHARS] + "\n…"

    def _show_message(self, text: str):
        dialog = QDialog(self)
        dialog.setWindowTitle("訊息全文")
        layout = QVBoxLayout(dialog)
        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setHtml(message_html(text))
        layout.addWidget(editor)
        dialog.resize(650, 500)
        dialog.exec()

    def _remove_conversation(self, owner: str, work_id: str) -> None:
        if QMessageBox.question(self, "移除對話", "移除這個區域及顯示歷史？\n不會取消 Agent 任務；新的回報會重新建立區域。\n尚在等待的問題仍保留於內部紀錄，移除不代表回答或同意。") != QMessageBox.StandardButton.Yes:
            return
        self._flush()
        self.store.remove_conversation(owner, work_id)
        if self.conversation == (owner, work_id):
            self.back()
        self.refresh_list()

    def open_conversation(self, owner: str, work_id: str) -> None:
        self._flush()
        self.active = None
        self._submit_now = None
        self.conversation = (owner, work_id)
        self._timeline_key = ""
        self._timeline_pages = [None]
        self._timeline_reset = True
        self._stick_bottom = True
        self.stack.setCurrentIndex(2)
        self._refresh_timeline()

    def _refresh_timeline(self) -> None:
        if not self.conversation:
            return
        work = self.store.conversation_page(*self.conversation, limit=self.PAGE_SIZE, before=self._timeline_pages[-1])
        if work is None:
            self.back()
            return
        stamp = (work["work_title"], work["source"], work["has_older"], len(self._timeline_pages),
                 [(e["id"], e.get("status"), e["kind"], e.get("text"), e.get("read"), e.get("answer"),
                   e.get("images")) for e in work["entries"]])
        if stamp == self._timeline_key:
            return
        self._timeline_key = stamp
        if not hasattr(self, "timeline_scroll"):
            self._make_timeline()
        self._building_timeline = True
        scroll, lay = self.timeline_scroll, self._timeline_layout
        bar = scroll.verticalScrollBar()
        latest = len(self._timeline_pages) == 1
        position = (-1 if latest else 0) if self._timeline_reset else (
            -1 if latest and getattr(self, "_stick_bottom", False) else bar.value())
        self._timeline_reset = False
        self._timeline_title.setText(f"{work['source']} · {work['work_title']}")
        self._older.setEnabled(work["has_older"])
        self._newer.setEnabled(len(self._timeline_pages) > 1)
        self._latest.setVisible(len(self._timeline_pages) > 1)
        self._first_key = work["first_key"]
        messages = self._chat_messages(work["entries"])
        wanted = {m["id"] for m in messages}
        for eid in list(self._timeline_widgets):
            if eid not in wanted:
                row, _, _ = self._timeline_widgets.pop(eid)
                lay.removeWidget(row)
                row.hide()
                row.deleteLater()
        self._read_markers = []
        self._empty_terminal_ids = [e["id"] for e in work["entries"]
                                    if e["kind"] in ("completed", "failed", "cancelled") and not e.get("text") and not e.get("read")]
        for position_in_page, message in enumerate(messages):
            old = self._timeline_widgets.get(message["id"])
            # Only Q&A status changes alter the bubble's actions; ordinary text stays in place.
            if old and (old[2].get("status") != message.get("status") or old[2].get("images") != message.get("images")):
                lay.removeWidget(old[0])
                old[0].hide()
                old[0].deleteLater()
                old = None
            if old:
                row, text, previous = old
                if previous["text"] != message["text"]:
                    text.setText(message_html(self._message_preview(message["text"])))
                    text.setVisible(bool(message["text"]))
                    row._full_text = message["text"]
                    row._full_button.setVisible(len(message["text"]) > self.PREVIEW_CHARS)
                row.setProperty("messageKind", message["kind"])
            else:
                row, text = self._chat_bubble(message)
            if lay.indexOf(row) != position_in_page:
                lay.insertWidget(position_in_page, row)
            self._timeline_widgets[message["id"]] = (row, text, message)
            if message["kind"] in ("completed", "failed", "cancelled") and not message.get("read"):
                self._read_markers.append((message["id"], text))
        self._layout_generation = getattr(self, "_layout_generation", 0) + 1
        generation = self._layout_generation
        QTimer.singleShot(0, lambda: self._restore_timeline_scroll(scroll, position, generation))

    def _make_timeline(self):
        outer = QVBoxLayout(self.timeline)
        top = QHBoxLayout()
        back = icon_button("‹", "回到對話列表")
        back.clicked.connect(self.back)
        top.addWidget(back)
        self._timeline_title = label("")
        top.addWidget(self._timeline_title, 1)
        outer.addLayout(top)
        navigation = QHBoxLayout()
        self._older = button("較早訊息", flat=True)
        self._newer = button("較新訊息", flat=True)
        self._latest = button("最新訊息", flat=True)
        self._older.clicked.connect(lambda: self._history_page("older"))
        self._newer.clicked.connect(lambda: self._history_page("newer"))
        self._latest.clicked.connect(lambda: self._history_page("latest"))
        for control in (self._older, self._newer, self._latest):
            navigation.addWidget(control)
        navigation.addStretch()
        outer.addLayout(navigation)
        scroll = QScrollArea()
        self.timeline_scroll = scroll
        scroll.setWidgetResizable(True)
        # Reserve the gutter: toggling it changes wrapped bubble heights, which
        # can toggle the scrollbar again while the window is being shrunk.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._timeline_layout = QVBoxLayout(body)
        self._timeline_layout.setContentsMargins(4, 8, 4, 12)
        self._timeline_layout.setSpacing(8)
        self._timeline_layout.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        scroll.verticalScrollBar().valueChanged.connect(self._chat_scrolled)
        scroll.verticalScrollBar().rangeChanged.connect(self._chat_range_changed)

    def _chat_scrolled(self, *_args):
        if not getattr(self, "_building_timeline", False):
            bar = self.timeline_scroll.verticalScrollBar()
            self._stick_bottom = len(self._timeline_pages) == 1 and bar.maximum() - bar.value() <= 8
        self._mark_visible_read()

    def _chat_range_changed(self, _minimum, maximum):
        if not getattr(self, "_building_timeline", False) and getattr(self, "_stick_bottom", False):
            self.timeline_scroll.verticalScrollBar().setValue(maximum)

    def _history_page(self, direction):
        if direction == "older" and self._first_key:
            self._timeline_pages.append(self._first_key)
        elif direction == "newer" and len(self._timeline_pages) > 1:
            self._timeline_pages.pop()
        elif direction == "latest":
            self._timeline_pages = [None]
        self._timeline_reset = True
        self._timeline_key = ""
        self._refresh_timeline()

    def _restore_timeline_scroll(self, scroll, position: int, generation=None) -> None:
        if scroll is not self.timeline_scroll or (generation is not None and generation != self._layout_generation):
            return
        scroll.widget().layout().activate()
        self._stick_bottom = position == -1
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() if self._stick_bottom else position)
        self._building_timeline = False
        self._mark_visible_read()

    def _mark_visible_read(self, *_args) -> None:
        if (getattr(self, "_building_timeline", False) or not self.isVisible() or not self.window().isActiveWindow()
                or self.stack.currentIndex() != 2 or not self.conversation):
            return
        viewport = self.timeline_scroll.viewport()
        ids = list(getattr(self, "_empty_terminal_ids", [])) + [eid for eid, widget in self._read_markers
               if viewport.rect().intersects(widget.rect().translated(widget.mapTo(viewport, widget.rect().topLeft()))) ]
        if ids:
            self.store.queue_mark_read(*self.conversation, ids, self.read_finished.emit)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.inline:
            self.window().installEventFilter(self)
        QTimer.singleShot(0, self._mark_visible_read)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.WindowActivate and watched is self.window():
            QTimer.singleShot(0, self._mark_visible_read)
        return super().eventFilter(watched, event)

    def _list_row(self, unit: list[dict[str, Any]]) -> QWidget:
        """Two lines: the question (elided; a group shows its intro or first question), then source · time · state."""
        q = unit[0]
        status = self._unit_status(unit)
        row = QWidget()
        row.setObjectName("Row")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QVBoxLayout(row)
        lay.setContentsMargins(6, 5, 6, 5)
        lay.setSpacing(1)
        first = QHBoxLayout()
        first.setSpacing(6)
        text = (q.get("group", {}).get("intro") or q["question"]) if len(unit) > 1 else q["question"]
        title = ElidedLabel((f"({len(unit)} 題) " if len(unit) > 1 else "") + " ".join(text.split()))
        title.setObjectName("RowName")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        first.addWidget(title, 1)
        if status == "pending":
            first.addWidget(dot("Agent 等待中"))
        lay.addLayout(first)
        state = {"answered": "已回答", "cancelled": "已取消"}.get(status, "")
        lay.addWidget(muted("  ·  ".join(p for p in (q["source"], _when(q["created_at"]), state) if p)))
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.mouseReleaseEvent = lambda e, qid=q["id"]: self.open_question(qid) \
            if e.button() == Qt.MouseButton.LeftButton else None  # type: ignore[method-assign]
        row.setToolTip("\n\n".join(m["question"][:300] for m in unit))
        return row

    def open_question(self, qid: str) -> None:
        self._flush()
        self._return_to_chat = self.stack.currentIndex() == 2 or (
            self.active == qid and getattr(self, "_return_to_chat", False))
        members = self.store.group_members(qid)
        self.active = members[0]["id"]
        self.conversation = (members[0]["owner"], members[0]["work_id"])
        self.render(members[0])
        self.stack.setCurrentIndex(1)

    def _leave_question(self) -> None:
        if self.inline:
            self._flush()
            self.active = None
            self._submit_now = None
            self.closed.emit()
            return
        if getattr(self, "_return_to_chat", False) and self.conversation:
            self.open_conversation(*self.conversation)
        else:
            self.back()

    def back(self) -> None:
        self._flush()
        self.active = None
        self._submit_now = None
        self._rendered_key = ""
        self.stack.setCurrentIndex(0)
        self.refresh_list()

    def show_first_pending(self) -> None:
        if self.stack.currentIndex() == 1 and self.active:
            return  # never yank the user away from what they are answering
        if self.stack.currentIndex() == 2:
            return
        pending = [u for u in self._units(self.store.visible_questions()) if self._unit_status(u) == "pending"]
        if len(pending) == 1:
            self.open_question(pending[0][0]["id"])

    # ------------------------------------------------------------------ helpers
    def error(self, err: Any) -> None:
        self.notice.setText(str(err))
        self.notice.show()
        self._notice_timer.start()

    def _draft(self, q: dict[str, Any]) -> dict[str, Any]:
        if q["id"] not in self.drafts:
            src = q.get("answer") or q["draft"]
            self.drafts[q["id"]] = {"selected": list(src["selected"]), "text": src["text"], "notes": dict(src["notes"]),
                                    "attachment_ids": list(src["attachment_ids"])}
        return self.drafts[q["id"]]

    def _changed(self) -> None:
        self.save_label.setText("儲存中…")
        self._save_timer.start()

    def _save_draft(self) -> None:
        if not self.active:
            return
        try:
            for qid in self.blocks:
                self.store.save_draft(qid, self._draft(self.store.get(qid)))
            if self.stack.currentIndex() == 1:
                self.save_label.setText("草稿已自動保存")
        except Exception as e:
            self.error(e)

    def _flush(self) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_draft()

    def _pixmap(self, q: dict[str, Any], meta: dict[str, Any]) -> QPixmap:
        _, data = self.store.asset(q["id"], meta["id"])
        pix = QPixmap()
        pix.loadFromData(data)
        return pix

    def _images(self, q: dict[str, Any], images: list[dict[str, Any]], size: int = 96) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        for meta in images:
            pix = self._pixmap(q, meta)
            thumb = ClickableImage()
            thumb.setObjectName("Thumb")
            thumb.setCursor(Qt.CursorShape.PointingHandCursor)
            thumb.setToolTip(meta.get("caption") or meta["name"])
            thumb.setPixmap(pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            thumb.clicked.connect(lambda p=pix, m=meta: ImageDialog(p, m.get("caption") or m["name"], self).exec())
            lay.addWidget(thumb)
        lay.addStretch()
        return row

    def _update_waiting(self, members: list[dict[str, Any]]) -> None:
        if self._unit_status(members) != "pending" or not hasattr(self, "waiting"):
            return
        connected = any(self.store.connected(m["id"]) for m in members)
        self.waiting.setText("● Agent 等待中" if connected else "○ 等待重新連線")
        self.waiting.setProperty("live", connected)
        self.waiting.style().unpolish(self.waiting)
        self.waiting.style().polish(self.waiting)

    # ------------------------------------------------------------------ detail
    def render(self, head: dict[str, Any]) -> None:
        members = self.store.group_members(head["id"])
        self._rendered_key = self._key(members)
        status = self._unit_status(members)
        readonly = status != "pending"
        self.blocks: dict[str, dict[str, Any]] = {}
        self.target_qid = members[0]["id"]
        old = self.detail
        self.detail = QWidget()
        self.stack.insertWidget(1, self.detail)
        self.stack.removeWidget(old)
        old.deleteLater()
        outer = QVBoxLayout(self.detail)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QHBoxLayout()
        top.setContentsMargins(8, 8, 14, 6)
        if not self.inline:
            back = icon_button("‹", "返回")
            back.clicked.connect(self._leave_question)
            top.addWidget(back)
            title = ElidedLabel(head["work_title"])
            top.addWidget(title, 1)
        if readonly:
            top.addWidget(muted("已回答" if status == "answered" else "已取消"))
        else:
            self.waiting = QLabel("")
            self.waiting.setObjectName("Waiting")
            top.addWidget(self.waiting)
            self._update_waiting(members)
        outer.addLayout(top)
        rule = QFrame()
        rule.setObjectName("Rule")
        rule.setFixedHeight(1)
        outer.addWidget(rule)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)
        many = len(members) > 1
        lay.addWidget(muted(f"{head['source']}  ·  {_when(head['created_at'])}" + (f"  ·  共 {len(members)} 題" if many else "")))
        intro = head.get("group", {}).get("intro") if many else ""
        if intro:
            lay.addWidget(label(intro, name="Question"))
        if status == "cancelled":
            lay.addWidget(muted("這題已取消，沒有傳送任何預設答案。"))
        for n, q in enumerate(members, 1):
            if many:
                rule = QFrame()
                rule.setObjectName("Rule")
                rule.setFixedHeight(1)
                lay.addWidget(rule)
            lay.addWidget(self._block(q, readonly, f"第 {n} 題" if many else ""))
        lay.addStretch()
        if self.inline:
            scroll.deleteLater()
            outer.addWidget(body)
        else:
            scroll.setWidget(body)
            outer.addWidget(scroll, 1)

        bar = RibbonBar()
        bar.setObjectName("ActionBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 12, 8)
        self.save_label = muted("" if readonly else theme.t("draft"))
        self.save_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bl.addWidget(self.save_label, 1)
        if not readonly:
            cancel = button(theme.t("cancel_q"), flat=True, danger=True)
            cancel.setToolTip("告訴 Agent 不回答" + ("這組問題" if many else "這題") + "（不會被當成同意）")
            cancel.clicked.connect(lambda: self._cancel(members))
            submit = button(theme.t("submit_all") if many else theme.t("submit"), primary=True)
            submit.setToolTip("Ctrl+Enter")
            submit.clicked.connect(lambda: self._submit(members))
            self._submit_now = lambda: self._submit(members)
            bl.addWidget(cancel)
            bl.addWidget(submit)
        outer.addWidget(bar)

    def _block(self, q: dict[str, Any], readonly: bool, number: str) -> QWidget:
        """One question: text, images, options with notes, free text, attachments."""
        draft = self._draft(q)
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        if number:
            lay.addWidget(self._section_label(number))
        lay.addWidget(label(q["question"], name="Question"))
        if q["images"]:
            lay.addWidget(self._images(q, q["images"]))
        state: dict[str, Any] = {"option_rows": {}}
        self.blocks[q["id"]] = state
        if q["options"]:
            lay.addWidget(self._section_label({"single": "選一項", "multiple": "可選多項"}.get(q["mode"], "選項")))
            group = QButtonGroup(box)
            group.setExclusive(q["mode"] == "single")
            opts = QVBoxLayout()
            opts.setSpacing(2)
            for option in q["options"]:
                choice = FullRowRadioButton(option["label"]) if q["mode"] == "single" else FullRowCheckBox(option["label"])
                row = OptionRow(choice)
                row.setObjectName("OptionRow")
                row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
                rl = QVBoxLayout(row)
                rl.setContentsMargins(8, 6, 8, 6)
                rl.setSpacing(4)
                choice.setChecked(option["id"] in draft["selected"])
                choice.setEnabled(not readonly)
                group.addButton(choice)
                line = QHBoxLayout()
                line.setSpacing(4)
                line.addWidget(choice, 1)
                add_note = icon_button("＋備註", "幫這個選項加備註（不必選它）")
                add_note.setProperty("small", True)
                add_note.setVisible(not readonly)
                line.addWidget(add_note)
                rl.addLayout(line)
                if option.get("description"):
                    desc = muted(option["description"])
                    desc.setWordWrap(True)
                    desc.setContentsMargins(26, 0, 0, 0)
                    desc.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    rl.addWidget(desc)
                if option.get("images"):
                    rl.addWidget(self._images(q, option["images"], 72))
                note = AutoGrowText(draft["notes"].get(option["id"], ""), "備註（選填，可換行）", max_lines=6)
                note.parent_view = self  # type: ignore[attr-defined]
                note.setReadOnly(readonly)
                note.textChanged.connect(lambda n=note, oid=option["id"]: self._note(q, oid, n.toPlainText()))
                note.focused = lambda qid=q["id"]: setattr(self, "target_qid", qid)  # type: ignore[attr-defined]
                rl.addWidget(note)
                opts.addWidget(row)
                state["option_rows"][option["id"]] = {"row": row, "note": note, "add": add_note, "open": False}
                add_note.clicked.connect(lambda _=False, oid=option["id"]: self._open_note(q, oid))
                note.blurred = lambda oid=option["id"]: self._close_note_if_empty(q, oid)  # type: ignore[attr-defined]
                choice.toggled.connect(lambda checked, oid=option["id"]: self._toggle(q, oid, checked))
            lay.addLayout(opts)
            self._paint_options(q)

        lay.addWidget(self._section_label("補充說明" if q["options"] else "你的回答"))
        text = AutoGrowText(draft["text"], "" if readonly else "輸入文字（可換行、可貼上圖片或拖放檔案）", max_lines=12)
        text.parent_view = self  # type: ignore[attr-defined]
        text.setReadOnly(readonly)
        text.textChanged.connect(lambda: self._text(q))
        text.image_pasted.connect(lambda data, qid=q["id"]: self._add_bytes(f"貼上圖片-{datetime.now():%H%M%S}.png", data, qid))
        text.focused = lambda qid=q["id"]: setattr(self, "target_qid", qid)  # type: ignore[attr-defined]
        state["text"] = text
        lay.addWidget(text)

        att_head = QHBoxLayout()
        att_head.addWidget(self._section_label("附件"))
        att_head.addStretch()
        if not readonly:
            add = icon_button("＋", "加入檔案")
            add.clicked.connect(lambda _=False, qid=q["id"]: self._pick_files(qid))
            att_head.addWidget(add)
        lay.addLayout(att_head)
        attach_box = QVBoxLayout()
        attach_box.setSpacing(0)
        state["attach_box"] = attach_box
        lay.addLayout(attach_box)
        self._render_attachments(q, readonly)
        return box

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("FieldLabel")
        return lab

    def _paint_options(self, q: dict[str, Any]) -> None:
        """Selected options get a highlight. A note box shows only when "＋備註" was clicked or a note exists;
        selecting an option does not open one. An empty box folds back when you leave it."""
        draft = self._draft(q)
        readonly = q["status"] != "pending"
        for oid, parts in self.blocks[q["id"]]["option_rows"].items():
            selected = oid in draft["selected"]
            parts["row"].setProperty("selected", selected)
            parts["row"].style().unpolish(parts["row"])
            parts["row"].style().polish(parts["row"])
            has_note = bool(draft["notes"].get(oid))
            show = has_note or (not readonly and parts["open"])
            parts["note"].setVisible(show)
            parts["add"].setVisible(not readonly and not show)

    def _close_note_if_empty(self, q: dict[str, Any], oid: str) -> None:
        parts = self.blocks.get(q["id"], {}).get("option_rows", {}).get(oid)
        if parts and not parts["note"].toPlainText().strip():
            parts["open"] = False
            QTimer.singleShot(0, lambda: self._paint_options(q))  # after the focus change has finished

    def _open_note(self, q: dict[str, Any], oid: str) -> None:
        parts = self.blocks[q["id"]]["option_rows"][oid]
        parts["open"] = True
        self._paint_options(q)
        parts["note"].setFocus()

    def _render_attachments(self, q: dict[str, Any], readonly: bool) -> None:
        box = self.blocks[q["id"]]["attach_box"]
        while box.count():
            item = box.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        chosen = set(self._draft(q)["attachment_ids"])
        shown = [m for m in q["uploads"] if m["id"] in chosen]
        if not shown:
            box.addWidget(muted("沒有附件" if readonly else "可按 ＋、拖放檔案或在上方貼上圖片"))
        for meta in shown:
            row = Row(meta["name"], note=f"{max(1, meta['size'] // 1024)} KB")
            if not readonly:
                rm = icon_button("×", "移除附件", danger=True)
                rm.clicked.connect(lambda _=False, aid=meta["id"]: self._remove_upload(q["id"], aid))
                row.add(rm)
            box.addWidget(row)

    # ------------------------------------------------------------------ edits
    def _toggle(self, q: dict[str, Any], oid: str, checked: bool) -> None:
        draft = self._draft(q)
        if q["mode"] == "single":
            draft["selected"] = [oid] if checked else [s for s in draft["selected"] if s != oid]
        elif checked and oid not in draft["selected"]:
            draft["selected"].append(oid)
        elif not checked:
            draft["selected"] = [s for s in draft["selected"] if s != oid]
        self._paint_options(q)
        self._changed()

    def _note(self, q: dict[str, Any], oid: str, text: str) -> None:
        notes = self._draft(q)["notes"]
        if text:
            notes[oid] = text
        else:
            notes.pop(oid, None)
        self._changed()

    def _text(self, q: dict[str, Any]) -> None:
        self._draft(q)["text"] = self.blocks[q["id"]]["text"].toPlainText()
        self._changed()

    def _pick_files(self, qid: str | None = None) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "加入附件")
        for f in files:
            self.add_file(Path(f), qid)

    def add_file(self, path: Path, qid: str | None = None) -> None:
        try:
            if not path.is_file():
                raise ValueError(f"{path.name} 不是檔案。")
            self._add_bytes(path.name, path.read_bytes(), qid)
        except Exception as e:
            self.error(e)

    def _add_bytes(self, name: str, data: bytes, qid: str | None = None) -> None:
        """Attach to the given question, or to the one last typed in (first question by default)."""
        qid = qid or getattr(self, "target_qid", None) or self.active
        if not qid:
            return
        try:
            self._flush()
            meta = self.store.add_upload(qid, name, data)
            q = self.store.get(qid)
            self._draft(q)["attachment_ids"].append(meta["id"])
            self._render_attachments(q, False)
        except Exception as e:
            self.error(e)

    def _remove_upload(self, qid: str, aid: str) -> None:
        try:
            self._flush()
            self.store.remove_upload(qid, aid)
            q = self.store.get(qid)
            draft = self._draft(q)
            draft["attachment_ids"] = [a for a in draft["attachment_ids"] if a != aid]
            self._render_attachments(q, False)
        except Exception as e:
            self.error(e)

    def _submit(self, members: list[dict[str, Any]]) -> None:
        try:
            self._save_timer.stop()
            answers = {m["id"]: self._draft(m) for m in members}
            if len(members) == 1:
                self.store.answer(members[0]["id"], answers[members[0]["id"]])
            else:
                self.store.answer_group(answers)
            for m in members:
                self.drafts.pop(m["id"], None)
            self._submit_now = None
            self._leave_question()
            self.answered.emit()
        except Exception as e:
            self.error(e)

    def _cancel(self, members: list[dict[str, Any]]) -> None:
        try:
            self._save_timer.stop()
            if len(members) == 1:
                self.store.cancel(members[0]["id"])
            else:
                self.store.cancel_group([m["id"] for m in members])
            self._leave_question()
        except Exception as e:
            self.error(e)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if self.active and self.stack.currentIndex() == 1 and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:  # noqa: N802
        for url in e.mimeData().urls():
            if url.isLocalFile():
                self.add_file(Path(url.toLocalFile()))
