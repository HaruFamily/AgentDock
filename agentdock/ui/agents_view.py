"""Agent page: MCP library on top, Agent cards below. Changes queue up and are written in one preview/apply."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from agentdock import clients
from agentdock.agents import KINDS, AgentManager, Plan, default_path
from agentdock.library import DEFAULT_ASSET, QAI_KEY, TYPES, Library
from agentdock.ui.qa_view import button, chip, label
from agentdock.ui.widgets import ElidedLabel, Row, dot, icon_button, muted


def _top(flags: Qt.WindowType = Qt.WindowType.Dialog) -> Qt.WindowType:
    return flags | Qt.WindowType.WindowStaysOnTopHint


# ============================================================================ dialogs
class PreviewDialog(QDialog):
    def __init__(self, plan: Plan, parent: QWidget) -> None:
        super().__init__(parent, _top())
        self.setWindowTitle("確認變更")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.addWidget(label("以下設定檔會先備份再寫入：", muted=True))
        for item in plan.summary:
            box = QFrame()
            box.setProperty("card", True)
            bl = QVBoxLayout(box)
            bl.addWidget(label(f"<b>{item['agent']}</b>"))
            bl.addWidget(label(item["path"], muted=True))
            for change in item["changes"]:
                bl.addWidget(label(f"• {change}"))
            if item["reformats"]:
                bl.addWidget(chip("此檔含註解或尾逗號，寫回後會改成標準 JSON（原檔有備份）", "WarnChip"))
            lay.addWidget(box)
        lay.addWidget(label("寫入後請重新載入或重啟該客戶端。", muted=True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("確認套用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class AgentDialog(QDialog):
    """Add (profile=None) or edit an Agent registration."""

    def __init__(self, parent: QWidget, profile: Any = None) -> None:
        super().__init__(parent, _top())
        self.setWindowTitle("編輯 Agent" if profile else "新增 Agent")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.kind = QComboBox()
        for key, text in KINDS.items():
            self.kind.addItem(text, key)
        self.path = QLineEdit()
        browse = button("瀏覽…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form.addRow("名稱", self.name)
        form.addRow("類型", self.kind)
        form.addRow("設定檔", row)
        self.hint = label("", muted=True)
        form.addRow("", self.hint)
        if profile:
            self.name.setText(profile.name)
            self.kind.setCurrentIndex(self.kind.findData(profile.kind))
            self.path.setText(profile.path)
            self._hint()
            self.kind.currentIndexChanged.connect(self._hint)
        else:
            self.kind.currentIndexChanged.connect(self._fill)
            self._fill()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _hint(self) -> None:
        self.hint.setText({"claude-code": "使用者層級 .claude.json，或專案的 .mcp.json。",
                           "claude-desktop": "Microsoft Store 版路徑會自動偵測；可在 Claude 設定按「Edit config」確認。",
                           "opencode": "opencode.json 或 opencode.jsonc。",
                           "codex": "Codex 的 config.toml。"}[self.kind.currentData()])

    def _fill(self) -> None:
        kind = self.kind.currentData()
        self.path.setText(default_path(kind))
        if not self.name.text() or self.name.text() in KINDS.values():
            self.name.setText(KINDS[kind])
        self._hint()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "選擇設定檔", self.path.text())
        if path:
            self.path.setText(path)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _pairs(text: str) -> dict[str, str]:
    out = {}
    for line in _lines(text):
        if "=" not in line:
            raise ValueError(f"請用 KEY=VALUE 格式：{line}")
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class McpDialog(QDialog):
    """Add or edit a library definition."""
    FIELDS = {"remote": ["url"], "uvx": ["options", "package", "args"], "npx": ["options", "package", "args"],
              "github": ["repo", "asset", "tag", "exe", "local", "args"], "download": ["download_url", "exe", "local", "args"],
              "command": ["command", "args"]}

    def __init__(self, parent: QWidget, library: Library, entry: dict[str, Any] | None = None) -> None:
        super().__init__(parent, _top())
        self.library = library
        self.original = entry
        self.setWindowTitle("編輯 MCP" if entry else "新增 MCP")
        self.setMinimumWidth(500)
        outer = QVBoxLayout(self)
        if not entry:
            preset = button("套用範例：codebase-memory-mcp", flat=True)
            preset.clicked.connect(self._preset_cmm)
            outer.addWidget(preset, 0, Qt.AlignmentFlag.AlignLeft)
        self.form = QFormLayout()
        outer.addLayout(self.form)
        self.type = QComboBox()
        for key, text in TYPES.items():
            if key != "builtin":
                self.type.addItem(text, key)
        self.w: dict[str, QWidget] = {
            "key": QLineEdit(), "description": QLineEdit(), "url": QLineEdit(), "package": QLineEdit(),
            "repo": QLineEdit(), "asset": QLineEdit(), "tag": QLineEdit(), "exe": QLineEdit(),
            "download_url": QLineEdit(), "command": QLineEdit(), "local": QPlainTextEdit(), "options": QPlainTextEdit(),
            "args": QPlainTextEdit(),
            "env": QPlainTextEdit(), "secrets": QPlainTextEdit(), "timeout_sec": QSpinBox()}
        self.w["key"].setPlaceholderText("寫進設定檔的名稱，例如 codebase-memory-mcp")
        self.w["url"].setPlaceholderText("https://…")
        self.w["package"].setPlaceholderText("例如 mcp-google-sheets@latest")
        self.w["repo"].setPlaceholderText("owner/repo")
        self.w["asset"].setPlaceholderText(f"檔名規則（預設 {DEFAULT_ASSET}）")
        self.w["tag"].setPlaceholderText("latest")
        self.w["exe"].setPlaceholderText("壓縮檔內的執行檔名，例如 codebase-memory-mcp.exe")
        self.w["command"].setPlaceholderText("可用 {HOME}、{AGENTDOCK}、{BIN} 等佔位符")
        self.w["args"].setPlaceholderText("一行一個參數")
        self.w["env"].setPlaceholderText("KEY=VALUE，一行一個（會進 Git，不要放祕密）")
        self.w["secrets"].setPlaceholderText("API_KEY=值，一行一個（只存在本機 data\\secrets.json）\n編輯時留空值＝保留原本的值")
        self.w["timeout_sec"].setRange(0, 86400)
        self.w["timeout_sec"].setSpecialValueText("預設")
        self.w["local"].setPlaceholderText("已用官方安裝程式裝過時的位置，一行一個，例如\n{LOCALAPPDATA}\\Programs\\xxx\\xxx.exe")
        self.w["options"].setPlaceholderText("套件名稱前的 uvx/npx 選項，一行一個，例如\n--with\nmcp<2")
        for edit in ("args", "env", "secrets", "local", "options"):
            self.w[edit].setFixedHeight(64)
        labels = {"key": "名稱", "description": "說明", "url": "網址", "package": "套件", "repo": "GitHub 專案",
                  "asset": "檔案規則", "tag": "版本", "exe": "執行檔", "download_url": "下載網址", "command": "指令", "local": "本機已安裝", "options": "執行選項",
                  "args": "參數", "env": "環境變數", "secrets": "祕密", "timeout_sec": "逾時（秒）"}
        self.form.addRow("類型", self.type)
        for key, widget in self.w.items():
            self.form.addRow(labels[key], widget)
        self.type.currentIndexChanged.connect(self._sync)
        if entry:
            self._load(entry)
        self._sync()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.error = label("", name="Notice")
        self.error.hide()
        outer.addWidget(self.error)
        outer.addWidget(buttons)

    def _sync(self) -> None:
        shown = set(self.FIELDS[self.type.currentData()]) | {"key", "description", "env", "secrets", "timeout_sec"}
        if self.type.currentData() == "remote":
            shown -= {"env", "secrets", "timeout_sec"}
        for key, widget in self.w.items():
            self.form.setRowVisible(widget, key in shown)

    def _preset_cmm(self) -> None:
        self.type.setCurrentIndex(self.type.findData("github"))
        self.w["key"].setText("codebase-memory-mcp")
        self.w["description"].setText("程式碼知識圖譜（DeusData/codebase-memory-mcp）")
        self.w["repo"].setText("DeusData/codebase-memory-mcp")
        self.w["asset"].setText(r"^codebase-memory-mcp-windows-amd64\.zip$")
        self.w["exe"].setText("codebase-memory-mcp.exe")
        self.w["local"].setPlainText(r"{LOCALAPPDATA}\Programs\codebase-memory-mcp\codebase-memory-mcp.exe")

    def _load(self, e: dict[str, Any]) -> None:
        self.type.setCurrentIndex(self.type.findData(e["type"]))
        for key in ("key", "description", "url", "package", "repo", "asset", "tag", "exe", "download_url", "command"):
            self.w[key].setText(e.get(key, ""))
        self.w["args"].setPlainText("\n".join(e.get("args", [])))
        self.w["local"].setPlainText("\n".join(e.get("local", [])))
        self.w["options"].setPlainText("\n".join(e.get("options", [])))
        self.w["env"].setPlainText("\n".join(f"{k}={v}" for k, v in e.get("env", {}).items()))
        self.w["secrets"].setPlainText("\n".join(f"{k}=" for k in e.get("secrets", [])))
        self.w["timeout_sec"].setValue(int(e.get("timeout_sec", 0)))

    def _save(self) -> None:
        try:
            t = self.type.currentData()
            entry: dict[str, Any] = {"type": t}
            for key in ("key", "description", *[f for f in self.FIELDS[t] if f not in ("args", "local", "options")]):
                entry[key] = self.w[key].text().strip()
            for key in ("args", "local", "options"):
                if key in self.FIELDS[t]:
                    entry[key] = _lines(self.w[key].toPlainText())
            secrets: dict[str, str] = {}
            if t != "remote":
                entry["env"] = _pairs(self.w["env"].toPlainText())
                secrets = _pairs(self.w["secrets"].toPlainText())
                entry["secrets"] = sorted(secrets)
                entry["timeout_sec"] = self.w["timeout_sec"].value() or None
            self.saved = self.library.save(entry, secrets, replace=self.original["key"] if self.original else None)
            self.accept()
        except Exception as e:
            self.error.setText(str(e))
            self.error.show()


# ============================================================================ background worker
class _Worker(QObject):
    progress = Signal(str)
    done = Signal(str, str)  # (key, message; "!" prefix = error)
    updates = Signal(dict)  # {key: latest_tag}


# ============================================================================ building blocks
class _Head(QWidget):
    """Fold header: click toggles; for Agent groups, dragging moves the Agent."""

    def __init__(self, fold: "Fold") -> None:
        super().__init__()
        self.fold = fold
        self._press = None
        self._dragged = False

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is None or not self.fold.drag_id or self._dragged:
            return
        if (e.position().toPoint() - self._press).manhattanLength() < 8:
            return
        self._dragged = True
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(DRAG_MIME, self.fold.drag_id.encode())
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._press)
        drag.exec(Qt.DropAction.MoveAction)
        self._press = None

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._dragged:
            self.fold.set_collapsed(not self.fold.collapsed, emit=True)
        self._press = None


class DropList(QWidget):
    """Vertical list of Agent groups that accepts header drags and reports the new order."""
    reordered = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.marker = QFrame(self)
        self.marker.setObjectName("DropMarker")
        self.marker.setFixedHeight(2)
        self.marker.hide()

    def _folds(self) -> list["Fold"]:
        return [self.lay.itemAt(i).widget() for i in range(self.lay.count())
                if isinstance(self.lay.itemAt(i).widget(), Fold)]

    def _index_at(self, y: int) -> int:
        folds = self._folds()
        for i, fold in enumerate(folds):
            if y < fold.geometry().top() + min(fold.geometry().height(), 32) // 2 + 6:
                return i
        return len(folds)

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(DRAG_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:  # noqa: N802
        if not e.mimeData().hasFormat(DRAG_MIME):
            return
        e.acceptProposedAction()
        folds = self._folds()
        i = self._index_at(int(e.position().y()))
        y = folds[i].geometry().top() if i < len(folds) else (folds[-1].geometry().bottom() if folds else 0)
        self.marker.setGeometry(0, max(0, y - 1), self.width(), 2)
        self.marker.show()
        self.marker.raise_()

    def dragLeaveEvent(self, _e) -> None:  # noqa: N802
        self.marker.hide()

    def dropEvent(self, e) -> None:  # noqa: N802
        self.marker.hide()
        moving = bytes(e.mimeData().data(DRAG_MIME)).decode()
        order = [f.drag_id for f in self._folds()]
        if moving not in order:
            return
        i = self._index_at(int(e.position().y()))
        before = order[:i]
        order.remove(moving)
        order.insert(len([x for x in before if x != moving]), moving)
        e.acceptProposedAction()
        self.reordered.emit(order)


class Fold(QWidget):
    """Header row + collapsible body. Used for the two sections and for each Agent."""
    toggled = Signal(bool)

    def __init__(self, title: str, *, section: bool = False, collapsed: bool = False) -> None:
        super().__init__()
        self.section = section
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        self.drag_id: str | None = None  # set for Agent groups: the header can be dragged to reorder
        head = _Head(self)
        head.setObjectName("SectionHead" if section else "GroupHead")
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.head = QHBoxLayout(head)
        self.head.setContentsMargins(0, 4, 0, 4)
        self.head.setSpacing(6)
        self.arrow = QLabel()
        self.arrow.setObjectName("Arrow")
        self.arrow.setFixedWidth(12)
        self.badge = QLabel("")
        self.badge.setObjectName("Badge")
        self.badge.hide()
        self.title = ElidedLabel(title)
        self.title.setObjectName("SectionTitle" if section else "GroupTitle")
        self.meta = ElidedLabel("")
        self.meta.setObjectName("Meta")
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # shrink with "…", never widen
        self.head.addWidget(self.arrow)
        self.head.addWidget(self.badge)
        self.head.addWidget(self.title)
        self.head.addWidget(self.meta, 1)
        outer.addWidget(head)
        if section:
            line = QFrame()
            line.setObjectName("Rule")
            line.setFixedHeight(1)
            outer.addWidget(line)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0 if section else 18, 2, 0, 6)
        self.body_lay.setSpacing(0)
        outer.addWidget(self.body)
        self.fixed_meta, self.folded_meta = "", ""
        self.badge_text, self.badge_alert = "", False
        self.collapsed = False
        self.set_collapsed(collapsed)

    def add_action(self, widget: QWidget) -> None:
        self.head.addWidget(widget)

    def set_meta(self, fixed: str = "", folded: str = "") -> None:
        """`fixed` always shows; `folded` only while collapsed (it repeats what the body shows)."""
        self.fixed_meta, self.folded_meta = fixed, folded
        self._paint_meta()

    def set_badge(self, text: str, alert: bool = False) -> None:
        """Small "(3)" / "(3*)" in front of the title, shown while folded."""
        self.badge_text, self.badge_alert = text, alert
        self._paint_meta()

    def _paint_meta(self) -> None:
        parts = [self.fixed_meta, self.folded_meta if self.collapsed else ""]
        self.meta.setText("  ·  ".join(p for p in parts if p))
        if hasattr(self, "badge"):
            self.badge.setText(self.badge_text)
            self.badge.setVisible(bool(self.badge_text) and self.collapsed)
            self.badge.setProperty("alert", self.badge_alert)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)

    def set_collapsed(self, value: bool, emit: bool = False) -> None:
        self.collapsed = value
        self.body.setVisible(not value)
        self.arrow.setText("›" if value else "⌄")
        self._paint_meta()
        if emit:
            self.toggled.emit(value)


TYPE_ORDER = ["builtin", "github", "download", "uvx", "npx", "remote", "command"]
TYPE_GROUP = {**TYPES, "command": "自訂指令 · 不自動更新", "_system": "系統", "_other": "其他"}
AGENT_SORTS = {"custom": "自訂（拖曳名稱排序）", "name": "名稱", "kind": "類型"}
MCP_SORTS = {"name": "名稱", "type": "類型"}


def _type_rank(t: str) -> int:
    return TYPE_ORDER.index(t) if t in TYPE_ORDER else len(TYPE_ORDER) + (1 if t == "_system" else 0)


# ============================================================================ main view
class AgentsView(QWidget):
    def __init__(self, manager: AgentManager, library: Library, on_changed: Callable[[], None] = lambda: None,
                 ui_state: dict[str, Any] | None = None, save_state: Callable[[], None] = lambda: None) -> None:
        super().__init__()
        self.manager = manager
        self.library = library
        self.on_changed = on_changed
        self.ui_state = ui_state if ui_state is not None else {}
        self.save_state = save_state
        self.ops: list[dict[str, Any]] = []
        self.busy: set[str] = set()
        self.needs: list[dict[str, Any]] = []      # agent entries out of sync with the library
        self.new_versions: list[dict[str, Any]] = []  # library entries with a newer GitHub release
        self._worker = _Worker()
        self._worker.progress.connect(lambda text: self.info(text, sticky=True))
        self._worker.done.connect(self._download_done)
        self._worker.updates.connect(lambda _u: self.refresh())
        self._notice_timer = QTimer(self, singleShot=True, interval=8000)
        self._restart_timer = QTimer(self, interval=30000)
        self._restart_timer.timeout.connect(self.check_restarts)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.notice = QLabel("")
        self.notice.setObjectName("Toast")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self._notice_timer.timeout.connect(self.notice.hide)
        root.addWidget(self.notice)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(16, 10, 14, 14)
        self.lay.setSpacing(14)
        scroll.setWidget(self.body)
        root.addWidget(scroll, 1)
        self.bar = QFrame()
        self.bar.setObjectName("ActionBar")
        bl = QHBoxLayout(self.bar)
        bl.setContentsMargins(14, 8, 12, 8)
        self.bar_label = QLabel("")
        self.bar_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.clear_btn = button("清除", flat=True)
        self.clear_btn.clicked.connect(self._clear_ops)
        self.update_btn = button("全部更新")
        self.update_btn.clicked.connect(lambda: self.guard(self._update_all))
        self.apply_btn = button("預覽並套用", primary=True)
        self.apply_btn.clicked.connect(lambda: self.guard(self._apply))
        bl.addWidget(self.bar_label, 1)
        bl.addWidget(self.clear_btn)
        bl.addWidget(self.update_btn)
        bl.addWidget(self.apply_btn)
        root.addWidget(self.bar)
        self.refresh()

    # ------------------------------------------------------------------ helpers
    def info(self, text: str, error: bool = False, sticky: bool = False) -> None:
        self.notice.setText(text)
        self.notice.setProperty("error", error)
        self.notice.style().unpolish(self.notice)
        self.notice.style().polish(self.notice)
        self.notice.show()
        if sticky:
            self._notice_timer.stop()
        else:
            self._notice_timer.start(12000 if error else 8000)

    def guard(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as e:
            self.info(str(e), error=True)
        self.refresh()
        self.on_changed()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _fold(self, key: str, title: str, *, section: bool = False, default: bool = False) -> Fold:
        fold = Fold(title, section=section, collapsed=self.ui_state.get("collapsed", {}).get(key, default))
        fold.toggled.connect(lambda v, k=key: self._remember(k, v))
        return fold

    def _sort(self, which: str) -> str:
        default = {"agents": "custom", "agent_mcp": "name", "library": "name"}[which]
        return self.ui_state.get("sort", {}).get(which, default)

    def _set_sort(self, which: str, value: str) -> None:
        self.ui_state.setdefault("sort", {})[which] = value
        self.save_state()
        self.refresh()

    def _sort_button(self, items: list[tuple[str, dict[str, str]]]) -> QPushButton:
        """⇅ menu. items: [(setting key, {value: label}), ...] — one checkable group per setting."""
        btn = icon_button("⇅", "排序")
        menu = QMenu(btn)
        titles = {"agents": "Agent 排序", "agent_mcp": "Agent 內的 MCP 排序", "library": "排序"}
        for n, (which, options) in enumerate(items):
            if n:
                menu.addSeparator()
            if len(items) > 1:
                menu.addSection(titles[which])
            for value, text in options.items():
                act = menu.addAction(text, lambda w=which, v=value: self._set_sort(w, v))
                act.setCheckable(True)
                act.setChecked(self._sort(which) == value)
        btn.clicked.connect(lambda: menu.exec(btn.mapToGlobal(btn.rect().bottomLeft())))
        btn._menu = menu  # keep alive
        return btn

    def _reorder(self, ids: list[str]) -> None:
        self.manager.reorder(ids)
        if self._sort("agents") != "custom":
            self.ui_state.setdefault("sort", {})["agents"] = "custom"
            self.save_state()
            self.info("已改為自訂排序。")
        self.refresh()

    def _remember(self, key: str, value: bool) -> None:
        self.ui_state.setdefault("collapsed", {})[key] = value
        self.save_state()

    # ------------------------------------------------------------------ pending ops
    def _op_for(self, pid: str, server: str) -> dict[str, Any] | None:
        return next((o for o in self.ops if o["profile"] == pid and o["server"] == server), None)

    def _set_op(self, pid: str, server: str, op: str | None, entry: dict[str, Any] | None = None) -> None:
        self.ops = [o for o in self.ops if not (o["profile"] == pid and o["server"] == server)]
        if op:
            self.ops.append({"profile": pid, "server": server, "op": op, **({"entry": entry} if entry is not None else {})})
        self.refresh()

    def _clear_ops(self) -> None:
        self.ops.clear()
        self.refresh()

    def _apply(self) -> None:
        plan = self.manager.prepare(self.ops)
        if not plan.files:
            self.info("沒有需要變更的設定。")
            return
        if PreviewDialog(plan, self).exec() != QDialog.DialogCode.Accepted:
            return
        results = self.manager.apply(plan)
        self.ops.clear()
        for entry in self.library.entries():
            self.library.cleanup(entry)
        now = time.time()
        restart = self.ui_state.setdefault("restart", {})
        for f in plan.files:
            restart[f.profile.id] = now
        self.save_state()
        backups = [r["backup"] for r in results if r["backup"]]
        self.info("已套用。標示「需重啟」的 Agent 重開後生效。" + (f"（已備份 {len(backups)} 個設定檔）" if backups else ""))
        self.check_restarts()

    # ------------------------------------------------------------------ restart tracking
    def check_restarts(self) -> None:
        """Clear "需重啟" once the client no longer has a process that predates our write.
        Runs when the panel opens and every 30 s while it stays open with marks pending — never in the background."""
        restart = self.ui_state.get("restart", {})
        if not restart or not self.isVisible():
            self._restart_timer.stop()
            return
        if not self._restart_timer.isActive():
            self._restart_timer.start()
        profiles = {p.id: p for p in self.manager.list()}
        for pid in [p for p in restart if p not in profiles]:
            restart.pop(pid)
        if not clients.available():
            return
        kinds: dict[str, set[str]] = {}
        for pid in restart:
            kinds.setdefault(profiles[pid].kind, set()).add(pid)
        try:
            stale = clients.still_old(kinds, restart)
        except Exception:
            return
        done = [pid for pid in restart if pid not in stale]
        if done:
            for pid in done:
                restart.pop(pid)
            self.save_state()
            self.refresh()

    def _dismiss_restart(self, pid: str) -> None:
        self.ui_state.get("restart", {}).pop(pid, None)
        self.save_state()
        self.refresh()

    def _update_all(self) -> None:
        for entry in self.new_versions:
            self._download(entry, update=True)
        for need in self.needs:
            if not self._op_for(need["profile"], need["server"]):
                self.ops.append(need)
        if self.new_versions:
            self.info("正在下載新版，完成後會自動排入待套用。", sticky=True)

    # ------------------------------------------------------------------ data
    def _adopt_unknown(self, rows: list[dict[str, Any]]) -> None:
        """Everything an agent already uses becomes a library entry, so "not in library" never shows up."""
        for row in rows:
            for server in row["servers"]:
                if server["system"] or server["qai"] or self.library.get(server["name"]) \
                        or (self._op_for(row["id"], server["name"]) or {}).get("op") == "remove":
                    continue
                try:
                    entry, secrets = self.library.from_config(server["name"], server["config"] or {})
                    self.library.save(entry, secrets)
                except Exception:
                    pass  # unusual entries simply stay unmanaged

    # ------------------------------------------------------------------ render
    def refresh(self) -> None:
        self._clear_layout(self.lay)
        rows = self.manager.inspect()
        self._adopt_unknown(rows)
        self.library.upgrade_commands()
        entries = self.library.entries()
        self.needs, self.new_versions = [], []

        agents = self._fold("section:agents", "Agent", section=True)
        add_agent = icon_button("＋", "新增 Agent")
        add_agent.clicked.connect(self._new_agent)
        agents.add_action(self._sort_button([("agents", AGENT_SORTS), ("agent_mcp", MCP_SORTS)]))
        agents.add_action(add_agent)
        self._agent_alerts = 0
        if not rows:
            agents.body_lay.addWidget(muted("尚未登錄 Agent，按右邊 ＋ 新增。"))
        mode = self._sort("agents")
        if mode == "name":
            rows = sorted(rows, key=lambda r: r["name"].lower())
        elif mode == "kind":
            kinds = list(KINDS)
            rows = sorted(rows, key=lambda r: (kinds.index(r["kind"]), r["name"].lower()))
        drop = DropList()
        drop.reordered.connect(lambda ids: self.guard(lambda: self._reorder(ids)))
        for row in rows:
            drop.lay.addWidget(self._agent_group(row))
        agents.body_lay.addWidget(drop)
        agents.set_badge(f"({len(rows)}{'*' if self._agent_alerts else ''})", alert=bool(self._agent_alerts))
        self.lay.addWidget(agents)
        if not self.ui_state.get("restart"):
            self._restart_timer.stop()

        lib = self._fold("section:library", "MCP 庫", section=True)
        add_mcp = icon_button("＋", "新增 MCP")
        add_mcp.clicked.connect(self._new_mcp)
        lib.add_action(self._sort_button([("library", MCP_SORTS)]))
        lib.add_action(add_mcp)
        by_type = self._sort("library") == "type"
        self._lib_by_type = by_type
        self._lib_alerts = 0
        entries = sorted(entries, key=lambda e: (e["type"] != "builtin", _type_rank(e["type"]) if by_type else 0,
                                                 (e.get("name") or e["key"]).lower()))
        group = None
        for entry in entries:
            if by_type and entry["type"] != "builtin" and entry["type"] != group:
                group = entry["type"]
                lib.body_lay.addWidget(self._group_label(TYPE_GROUP[group]))
            lib.body_lay.addWidget(self._library_row(entry))
        lib.set_badge(f"({len(entries)}{'*' if self._lib_alerts else ''})", alert=bool(self._lib_alerts))
        self.lay.addWidget(lib)
        self.lay.addStretch()
        self._paint_bar()

    def _paint_bar(self) -> None:
        pending_needs = [n for n in self.needs if not self._op_for(n["profile"], n["server"])]
        updates = len(pending_needs) + len(self.new_versions)
        parts = []
        if updates:
            parts.append(f"{updates} 項需要更新")
        if self.ops:
            parts.append(f"{len(self.ops)} 項變更待套用")
        self.bar.setVisible(bool(parts))
        self.bar_label.setText("  ·  ".join(parts))
        self.update_btn.setVisible(bool(updates))
        self.apply_btn.setVisible(bool(self.ops))
        self.clear_btn.setVisible(bool(self.ops))

    def _agent_group(self, row: dict[str, Any]) -> QWidget:
        pid = row["id"]
        profile = self.manager.get(pid)
        fold = self._fold(f"agent:{pid}", row["name"])
        fold.drag_id = pid
        if pid in self.ui_state.get("restart", {}):
            tag = QPushButton("需重啟")
            tag.setObjectName("RestartTag")
            tag.setCursor(Qt.CursorShape.PointingHandCursor)
            tag.setToolTip(f"設定已更新，重開 {row['name']} 後才會載入。偵測到重開（或已關閉）會自動消失；點一下可手動清除。")
            tag.clicked.connect(lambda _=False: self._dismiss_restart(pid))
            fold.head.insertWidget(3, tag)
        fold.setToolTip(f"{KINDS[row['kind']]}\n{row['path']}")
        plus = icon_button("＋", "從 MCP 庫加入")
        plus.setEnabled(not row["error"])
        plus.clicked.connect(lambda _=False, r=row, b=plus: self._plus_menu(r, b))
        more = icon_button("⋯", "更多")
        menu = QMenu(more)
        menu.addAction("編輯…", lambda p=profile: self._edit_agent(p))
        menu.addAction("移除登錄", lambda: self.guard(lambda: self.manager.remove(pid)))
        more.clicked.connect(lambda _=False, m=menu, b=more: m.exec(b.mapToGlobal(b.rect().bottomLeft())))
        fold.add_action(plus)
        fold.add_action(more)
        kind = KINDS[row["kind"]] if row["name"] != KINDS[row["kind"]] else ""
        if row["error"]:
            fold.body_lay.addWidget(Row("設定檔有錯誤", note=row["error"], alert=row["error"]))
            fold.set_meta(kind)
            fold.set_badge("(!)", alert=True)
            self._agent_alerts += 1
            return fold
        present = set()
        alerts = 0
        by_type = self._sort("agent_mcp") == "type"

        def mcp_type(server: dict[str, Any]) -> str:
            if server["system"]:
                return "_system"
            if server["qai"] or server["name"] == QAI_KEY:
                return "builtin"
            entry = self.library.get(server["name"])
            return entry["type"] if entry else "_other"

        def order(server: dict[str, Any]) -> tuple:
            t = mcp_type(server)
            return (0 if t == "builtin" else 2 if t == "_system" else 1,   # QAI first, system last
                    _type_rank(t) if by_type else 0,
                    0 if server["enabled"] else 1,                          # disabled after active
                    server["name"].lower())

        group = None
        for server in sorted(row["servers"], key=order):
            present.add(server["name"])
            t = mcp_type(server)
            if by_type and t not in ("builtin", "_system") and t != group:
                group = t
                fold.body_lay.addWidget(self._group_label(TYPE_GROUP.get(t, t)))
            line, alert = self._server_row(profile, row, server)
            alerts += alert
            fold.body_lay.addWidget(line)
        for op in self.ops:
            if op["profile"] == pid and op["op"] == "add" and op["server"] not in present:
                line = Row(op["server"], note="將加入")
                undo = icon_button("↶", "復原")
                undo.clicked.connect(lambda _=False, n=op["server"]: self._set_op(pid, n, None))
                line.add(undo)
                fold.body_lay.addWidget(line)
        if not row["servers"] and not any(o["profile"] == pid for o in self.ops):
            fold.body_lay.addWidget(muted("沒有 MCP"))
        fold.set_meta(kind)
        fold.set_badge(f"({len(row['servers'])}{'*' if alerts else ''})", alert=bool(alerts))
        self._agent_alerts += int(bool(alerts))
        if alerts:
            fold.badge.setToolTip(f"{alerts} 個 MCP 需更新")
        return fold

    @staticmethod
    def _group_label(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("GroupLabel")
        return lab

    def _server_row(self, profile: Any, row: dict[str, Any], server: dict[str, Any]) -> tuple[QWidget, int]:
        pid, name = row["id"], server["name"]
        op = self._op_for(pid, name)
        if op and op["op"] == "remove":
            line = Row(name, note="將移除", dim=True, strike=True)
            undo = icon_button("↶", "復原")
            undo.clicked.connect(lambda: self._set_op(pid, name, None))
            line.add(undo)
            return line, 0
        if server["system"]:
            return Row(name, note="系統（由客戶端管理）", dim=True), 0
        entry = self.library.get(name)
        alert, note = "", ""
        if server["qai"] and name != QAI_KEY:
            alert = f"舊名稱的 QAI，會在「全部更新」時換成 {QAI_KEY}"
            self.needs.append({"profile": pid, "server": name, "op": "remove"})
            if not any(s["name"] == QAI_KEY for s in row["servers"]):
                self.needs.append({"profile": pid, "server": QAI_KEY, "op": "add",
                                   "entry": self.library.render(self.library.get(QAI_KEY), row["kind"], profile)})
        elif entry and not (op and op["op"] == "update") \
                and self.library.in_sync(entry, row["kind"], profile, server["config"]) is False:
            alert = "設定和 MCP 庫不同（例如換了版本或路徑），會在「全部更新」時同步"
            self.needs.append({"profile": pid, "server": name, "op": "update",
                               "entry": self.library.render(entry, row["kind"], profile)})
        if op and op["op"] == "update":
            note = "將更新"
        elif op and op["op"] == "enable":
            note = "將啟用"
        elif not server["enabled"]:
            note = "停用"
        line = Row(name, note=note, alert=alert, dim=not server["enabled"] and not op)
        if not server["enabled"] and not op:
            on = icon_button("啟用", "重新啟用這個 MCP")
            on.setProperty("small", True)
            on.clicked.connect(lambda: self._set_op(pid, name, "enable"))
            line.add(on)
        elif op:
            undo = icon_button("↶", "復原")
            undo.clicked.connect(lambda: self._set_op(pid, name, None))
            line.add(undo)
        minus = icon_button("×", "從這個 Agent 移除（MCP 庫中的定義會保留）", danger=True)
        minus.clicked.connect(lambda: self.guard(lambda: self._remove_server(pid, name, server["config"])))
        line.add(minus)
        return line, int(bool(alert))

    def _library_row(self, entry: dict[str, Any]) -> QWidget:
        key, kind = entry["key"], entry["type"]
        installed = self.library.installed_exe(entry) if kind in ("github", "download") else None
        state = self.library.install_state(entry)
        latest = self.library.cached_update(entry) if installed else None
        if latest and key not in self.busy:
            self.new_versions.append(entry)
        problems = self.library.problems(entry)
        if kind == "builtin":
            note = "內建"
        elif key in self.busy:
            note = "處理中…"
        elif installed:
            note = "本機檔案" if state.get("path") else (state.get("tag") or "已下載")
        elif kind in ("uvx", "npx"):  # grouped by type: the group label already says the type
            note = entry["package"] if getattr(self, "_lib_by_type", False) else f"{kind} · {entry['package']}"
        elif kind == "command":
            note = "" if getattr(self, "_lib_by_type", False) else "自訂指令 · 不自動更新"
        else:
            note = "" if getattr(self, "_lib_by_type", False) else TYPES[kind]
        if latest:
            note += f" → {latest}"
        alert = "、".join(problems + ([f"有新版 {latest}"] if latest else []))
        self._lib_alerts += int(bool(alert))
        line = Row(entry.get("name") or key, note=note, alert=alert)
        line.note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if entry.get("description"):
            line.setToolTip(entry["description"])
        if kind == "builtin":
            return line
        more = icon_button("⋯", "更多")
        menu = QMenu(more)
        if kind in ("github", "download") and key not in self.busy:
            if latest:
                menu.addAction(f"更新到 {latest}", lambda e=entry: self._download(e, update=True))
            if not installed:
                menu.addAction("下載", lambda e=entry: self._download(e))
            if installed and kind == "github" and not state.get("path"):
                menu.addAction("檢查更新", lambda e=entry: self._check_updates([e], force=True))
            if installed:
                menu.addAction("重新下載", lambda e=entry: self._download(e))
            menu.addSeparator()
        menu.addAction("編輯…", lambda e=entry: self._edit_mcp(e))
        menu.addAction("刪除…", lambda k=key: self.guard(lambda: self._delete_mcp(k)))
        more.clicked.connect(lambda _=False, m=menu, b=more: m.exec(b.mapToGlobal(b.rect().bottomLeft())))
        if "需下載" in problems and key not in self.busy:
            dl = button("下載", flat=True)
            dl.clicked.connect(lambda _=False, e=entry: self._download(e))
            line.add(dl)
        line.add(more)
        return line

    # ------------------------------------------------------------------ actions
    def _delete_mcp(self, key: str) -> None:
        """Delete from the library AND queue removal from every Agent that uses it."""
        users = [r for r in self.manager.inspect()
                 if any(s["name"] == key and not s["system"] for s in r["servers"])]
        if users:
            names = "、".join(r["name"] for r in users)
            answer = QMessageBox.question(self, "刪除 MCP", f"{key} 正被 {names} 使用。\n刪除後也會從這些 Agent 移除（按「預覽並套用」後寫入）。確定刪除？")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.library.remove(key)
        for r in users:
            self.ops = [o for o in self.ops if not (o["profile"] == r["id"] and o["server"] == key)]
            self.ops.append({"profile": r["id"], "server": key, "op": "remove"})
        for r in self.manager.inspect():  # also drop queued additions of it
            self.ops = [o for o in self.ops if not (o["profile"] == r["id"] and o["server"] == key and o["op"] in ("add", "update"))]
        self.info(f"已從 MCP 庫刪除 {key}" + (f"，{len(users)} 個 Agent 的移除待套用。" if users else "。"))

    def _remove_server(self, pid: str, name: str, config: dict[str, Any]) -> None:
        if not self.library.get(name):
            entry, secrets = self.library.from_config(name, config)
            self.library.save(entry, secrets)
            self.info(f"{name} 已先收進 MCP 庫，之後可再加回來。")
        self._set_op(pid, name, "remove")

    def _plus_menu(self, row: dict[str, Any], anchor: QWidget) -> None:
        present = {s["name"] for s in row["servers"] if (self._op_for(row["id"], s["name"]) or {}).get("op") != "remove"} \
            | {o["server"] for o in self.ops if o["profile"] == row["id"] and o["op"] == "add"}
        menu = QMenu(self)
        options = [e for e in self.library.entries() if e["key"] not in present]
        if not options:
            menu.addAction("MCP 庫的項目都已加入").setEnabled(False)
        for entry in options:
            title = entry.get("name") or entry["key"]
            problems = self.library.problems(entry)
            action = menu.addAction(f"{title}（{'、'.join(problems)}）" if problems else title)
            action.triggered.connect(lambda _=False, e=entry: self.guard(lambda: self._add_to_agent(row, e)))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _add_to_agent(self, row: dict[str, Any], entry: dict[str, Any]) -> None:
        profile = self.manager.get(row["id"])
        rendered = self.library.render(entry, row["kind"], profile)
        existing = self._op_for(row["id"], entry["key"])
        self.ops = [o for o in self.ops if not (o["profile"] == row["id"] and o["server"] == entry["key"])]
        if existing and existing["op"] == "remove":  # re-adding something marked for removal = keep it, refreshed
            self.ops.append({"profile": row["id"], "server": entry["key"], "op": "update", "entry": rendered})
        else:
            self.ops.append({"profile": row["id"], "server": entry["key"], "op": "add", "entry": rendered})

    def queue_updates(self, key: str) -> int:
        """After a library change: queue `update` for every Agent whose entry no longer matches."""
        entry = self.library.get(key)
        if not entry:
            return 0
        n = 0
        for row in self.manager.inspect():
            server = next((s for s in row["servers"] if s["name"] == key), None)
            if not server or self._op_for(row["id"], key):
                continue
            profile = self.manager.get(row["id"])
            if self.library.in_sync(entry, row["kind"], profile, server["config"]) is False:
                self.ops.append({"profile": row["id"], "server": key, "op": "update",
                                 "entry": self.library.render(entry, row["kind"], profile)})
                n += 1
        return n

    def _new_mcp(self) -> None:
        dialog = McpDialog(self, self.library)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            entry = dialog.saved
            self.info(f"已加入 MCP 庫：{entry['key']}")
            self.refresh()

    def _edit_mcp(self, entry: dict[str, Any]) -> None:
        dialog = McpDialog(self, self.library, entry)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            n = self.queue_updates(dialog.saved["key"])
            self.info(f"已更新，{n} 個 Agent 的設定待套用。" if n else "已更新。")
            self.refresh()

    def _new_agent(self) -> None:
        dialog = AgentDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.guard(lambda: self.manager.add(dialog.name.text(), dialog.kind.currentData(), dialog.path.text()))

    def _edit_agent(self, profile: Any) -> None:
        dialog = AgentDialog(self, profile)
        remove = button("移除登錄", danger=True)
        remove.setToolTip("只從 AgentDock 移除，不會改動設定檔")
        dialog.layout().addRow(remove)
        removed = []
        remove.clicked.connect(lambda: (removed.append(True), dialog.accept()))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if removed:
                self.guard(lambda: self.manager.remove(profile.id))
            else:
                self.guard(lambda: self.manager.update(profile.id, dialog.name.text(), dialog.kind.currentData(), dialog.path.text()))

    def _use_local(self, entry: dict[str, Any], path: Any) -> None:
        used = self.library.use_local(entry, path)
        n = self.queue_updates(entry["key"])
        self.info(f"{entry['key']} 使用本機檔案：{used}" + (f"；{n} 個 Agent 的設定待套用。" if n else ""))

    def _pick_local(self, entry: dict[str, Any]) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"選擇 {entry['key']} 的執行檔", "", "執行檔 (*.exe);;所有檔案 (*)")
        if path:
            self.guard(lambda: self._use_local(entry, path))

    def _download(self, entry: dict[str, Any], update: bool = False) -> None:
        key = entry["key"]
        if key in self.busy:
            return
        self.busy.add(key)
        self.info(f"準備{'更新' if update else '下載'} {key}…")
        self.refresh()

        def run() -> None:
            try:
                path = self.library.install(entry, self._worker.progress.emit)
                self._worker.done.emit(key, f"已安裝：{path}")
            except Exception as e:
                self._worker.done.emit(key, f"!下載失敗：{e}")

        threading.Thread(target=run, daemon=True).start()

    def _download_done(self, key: str, message: str) -> None:
        self.busy.discard(key)
        if message.startswith("!"):
            self.refresh()
            self.info(message[1:], error=True)
            return
        n = self.queue_updates(key)
        self.refresh()
        self.info(message + (f"；{n} 個 Agent 會一起改用新版，按「預覽並套用」後重開客戶端。" if n else ""))

    def check_updates(self) -> None:
        """Background, at most once a day per MCP (see Library.check_update)."""
        self._check_updates([e for e in self.library.entries() if e["type"] == "github"])

    def _check_updates(self, entries: list[dict[str, Any]], force: bool = False) -> None:
        def run() -> None:
            found = {}
            for entry in entries:
                try:
                    latest = self.library.check_update(entry, force=force)
                    if latest:
                        found[entry["key"]] = latest
                except Exception:
                    pass  # offline / rate limited: try again next time
            self._worker.updates.emit(found)
            if force:
                self._worker.progress.emit("、".join(f"{k} 有新版 {v}" for k, v in found.items()) or "已是最新版本。")

        threading.Thread(target=run, daemon=True).start()
