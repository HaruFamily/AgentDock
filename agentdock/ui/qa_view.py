"""Answer center: pending/history list -> question detail with options, notes, text and attachments."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from agentdock.qa.store import QuestionStore
from agentdock.ui.widgets import ElidedLabel, Row, dot, icon_button, muted

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


def label(text: str, *, muted: bool = False, wrap: bool = True, name: str | None = None) -> QLabel:
    lab = QLabel(breakable(text) if wrap and "<" not in text else text)
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

    def __init__(self, store: QuestionStore) -> None:
        super().__init__()
        self.store = store
        self.active: str | None = None
        self.history = False
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

        # list page ---------------------------------------------------------
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tabs = QHBoxLayout()
        tabs.setContentsMargins(16, 10, 14, 6)
        tabs.setSpacing(14)
        self.pending_btn = QPushButton("待回答")
        self.history_btn = QPushButton("已處理")
        for b in (self.pending_btn, self.history_btn):
            b.setCheckable(True)
            b.setProperty("textTab", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            tabs.addWidget(b)
        tabs.addStretch()
        self.pending_btn.setChecked(True)
        self.pending_btn.clicked.connect(lambda: self._set_history(False))
        self.history_btn.clicked.connect(lambda: self._set_history(True))
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
        self.refresh()

    # ------------------------------------------------------------------ list
    def _set_history(self, value: bool) -> None:
        self.history = value
        self.pending_btn.setChecked(not value)
        self.history_btn.setChecked(value)
        self.refresh_list()

    def refresh(self) -> None:
        self.refresh_list()
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
        units = self._units(self.store.list())
        pending = [u for u in units if self._unit_status(u) == "pending"]
        self.pending_changed.emit(len(pending))
        self.pending_btn.setText(f"待回答 ({len(pending)})" if pending else "待回答")
        shown = [u for u in units if (self._unit_status(u) != "pending") == self.history]
        stamp = (lambda u: max(q.get("resolved_at", "") for q in u)) if self.history else (lambda u: u[0]["created_at"])
        shown.sort(key=stamp, reverse=True)
        while self.list_lay.count():
            item = self.list_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        works: dict[str, list[list[dict]]] = {}
        for u in shown:
            works.setdefault(f"{u[0]['owner']}/{u[0]['work_id']}", []).append(u)
        for work in works.values():
            head = QLabel(work[0][0]["work_title"])
            head.setObjectName("GroupLabel")
            self.list_lay.addWidget(head)
            for u in work:
                self.list_lay.addWidget(self._list_row(u))
        if not shown:
            empty = muted("目前沒有待回答的問題。Agent 需要你決定時，會出現在這裡。" if not self.history else "還沒有已處理的問題。")
            empty.setWordWrap(True)
            empty.setContentsMargins(4, 12, 4, 0)
            self.list_lay.addWidget(empty)
        self.list_lay.addStretch()

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
        members = self.store.group_members(qid)
        self.active = members[0]["id"]
        self.render(members[0])
        self.stack.setCurrentIndex(1)

    def back(self) -> None:
        self._flush()
        self.active = None
        self._rendered_key = ""
        self.stack.setCurrentIndex(0)
        self.refresh_list()

    def show_first_pending(self) -> None:
        if self.stack.currentIndex() == 1 and self.active:
            return  # never yank the user away from what they are answering
        pending = [u for u in self._units(self.store.list()) if self._unit_status(u) == "pending"]
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
        top.setSpacing(6)
        back = icon_button("‹", "回到列表")
        back.clicked.connect(self.back)
        top.addWidget(back)
        title = ElidedLabel(head["work_title"])
        title.setObjectName("GroupTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
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
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        bar = QFrame()
        bar.setObjectName("ActionBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 12, 8)
        self.save_label = muted("" if readonly else "草稿會自動保存")
        self.save_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bl.addWidget(self.save_label, 1)
        if not readonly:
            cancel = button("取消問題", flat=True, danger=True)
            cancel.setToolTip("告訴 Agent 不回答" + ("這組問題" if many else "這題") + "（不會被當成同意）")
            cancel.clicked.connect(lambda: self._cancel(members))
            submit = button("全部提交" if many else "提交", primary=True)
            submit.clicked.connect(lambda: self._submit(members))
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
                row = QWidget()
                row.setObjectName("OptionRow")
                row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
                rl = QVBoxLayout(row)
                rl.setContentsMargins(8, 6, 8, 6)
                rl.setSpacing(4)
                choice = QRadioButton(option["label"]) if q["mode"] == "single" else QCheckBox(option["label"])
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
            self.back()
        except Exception as e:
            self.error(e)

    def _cancel(self, members: list[dict[str, Any]]) -> None:
        try:
            self._save_timer.stop()
            if len(members) == 1:
                self.store.cancel(members[0]["id"])
            else:
                self.store.cancel_group([m["id"] for m in members])
            self.back()
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
