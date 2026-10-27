"""The conversation window: projects on the left, the whole conversation on the right.

The floating card stays for notices and quick replies; this window is where conversations are read
and continued. Approvals, answers, sends and stops only happen on a click, as in the Inbox.
"""
from __future__ import annotations


import difflib
import html
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontDatabase, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from agentdock.dispatch.adapter import EFFORT_LABELS
from agentdock.dispatch.projects import UNSORTED
from agentdock.dispatch.store import owner_for, work_id
from agentdock.dispatch.transcript import Transcript, tool_summary
from agentdock.ui import theme
from agentdock.ui.dispatch_view import FormBox, status_text, text_box
from agentdock.ui.qa_view import button, label
from agentdock.ui import web_transcript
from agentdock.ui.question_text import QuestionText
from agentdock.ui.web_transcript import tool_name
from agentdock.ui.widgets import ElidedLabel, icon_button
from agentdock.ui.chat_common import AGENTS, Bridge, RAIL, SIDEBAR, _style, context_text, hidden, icon, paint_decor, resume_command, split_code, theme_color, when  # noqa: F401
from agentdock.ui.chat_messages import CodeBlock, MessageWidget, ToolRow, diff_html, diff_of  # noqa: F401
from agentdock.ui.chat_sidebar import Rail, Sidebar  # noqa: F401
from agentdock.ui.chat_composer import SettingsBar  # noqa: F401
from agentdock.ui.chat_conversation import ConversationView  # noqa: F401
from agentdock.ui.chat_new import NewConversationView  # noqa: F401
from agentdock.ui.chat_dialogs import DeleteDialog, ImportDialog, ProjectDialog, ProjectView, RenameDialog, SearchPalette, TitleBar  # noqa: F401


class ChatWindow(QWidget):
    closed = Signal()
    page_changed = Signal(str)   # 'chat', or a page added with add_page

    def __init__(self, store, dispatcher, projects, targets, notify=lambda _m: None,
                 open_page=lambda _name: None, ui_state=None, save_state=lambda: None):
        super().__init__()
        self.store, self.dispatcher, self.projects = store, dispatcher, projects
        self.targets, self.notify, self.open_page = targets, notify, open_page
        self.ui_state = ui_state if ui_state is not None else {}
        self.save_state = save_state
        self.current_key = ''
        self.sent_files: dict[str, str] = {}   # attachment name → path, this session
        self.extra_pages: dict[str, QWidget] = {}
        self.setObjectName('ChatWindow')
        self.setWindowTitle('AgentDock')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setMinimumSize(QSize(900, 600))
        self.setAcceptDrops(True)
        self.bridge = Bridge()
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        self.titlebar.hide()
        self.outer.addWidget(self.titlebar)
        layout = QHBoxLayout()
        layout.setContentsMargins(RAIL, 0, 0, 0)   # the rail floats over this margin
        layout.setSpacing(0)
        self.outer.addLayout(layout, 1)
        self.frameless = False
        self.setMouseTracking(True)
        self.sidebar = Sidebar(self)
        self.pages = QStackedWidget()
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setObjectName('ChatSplit')
        self.split.setHandleWidth(4)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.sidebar)
        self.split.addWidget(self.pages)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        width = self.ui_state.get('sidebar_width')
        width = width if isinstance(width, int) and SIDEBAR[0] <= width <= SIDEBAR[2] else SIDEBAR[1]
        self.split.setSizes([width, 10000])
        self.split.splitterMoved.connect(lambda _p, _i: self.ui_state.__setitem__('sidebar_width', self.sidebar.width()))
        layout.addWidget(self.split, 1)
        self.new_view = NewConversationView(self)
        self.conversation = ConversationView(self)
        self.project_view = ProjectView(self)
        for page in (self.new_view, self.conversation, self.project_view):
            self.pages.addWidget(page)
        self.rail = Rail(self)
        self.rail.picked.connect(self._rail_picked)
        self.bridge.history.connect(self.conversation.history_loaded)
        self.bridge.history_failed.connect(self.conversation.history_failed)
        self.bridge.live.connect(self.conversation.live_event)
        self.bridge.started.connect(self.open_conversation)
        self.bridge.deleted.connect(self._deleted)
        self.bridge.replaced.connect(self._replaced)
        self.bridge.reload.connect(lambda key: key == self.conversation.key and self.conversation.reload())
        self.bridge.options.connect(self._options_arrived)
        self.bridge.git.connect(self.conversation.git_arrived)
        self.git_windows: dict[str, QDialog] = {}
        attach_dir = Path(store.dispatch.file).parent / 'attachments' / 'chat'
        self.new_view.composer.attach_dir = self.conversation.composer.attach_dir = attach_dir
        dispatcher.listeners.append(self.bridge.live.emit)
        self._refresh = QTimer(self, singleShot=True, interval=120)
        self._refresh.timeout.connect(self.refresh)
        self.refresh_theme()
        geometry = self.ui_state.get('chat_geometry')
        if isinstance(geometry, list) and len(geometry) == 4:
            self.setGeometry(*geometry)
        else:
            self.resize(1180, 780)
        self._restored = False
        self.show_new()
        self.palette_ = SearchPalette(self)
        for keys, action in (('Ctrl+N', self.new_like_current), ('Ctrl+K', self.palette_.open),
                             ('Ctrl+B', self.toggle_sidebar),
                             ('Ctrl+F', self.find_in_conversation),
                             ('Escape', self.escape), ('Alt+Up', lambda: self.step_conversation(-1)),
                             ('Alt+Down', lambda: self.step_conversation(1))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(action)

    # -- navigation --------------------------------------------------------------
    def add_page(self, name: str, widget: QWidget) -> None:
        """Tools / settings live in the window; without one, the rail asks open_page instead."""
        self.extra_pages[name] = widget
        self.pages.addWidget(widget)

    def _chat_mode(self) -> None:
        if self.rail.current != 'chat':
            self.rail.set_current('chat')
            self.page_changed.emit('chat')
        self._show_list(not self.ui_state.get('sidebar_hidden'))

    def _show_list(self, shown: bool) -> None:
        self.sidebar.setVisible(shown)
        self.rail.buttons['new'].setVisible(not shown and self.rail.current == 'chat')

    def toggle_sidebar(self) -> None:
        """Ctrl+B, or 聊天 on the rail while chatting: hide or show the conversation list."""
        if self.pages.currentWidget() in self.extra_pages.values():
            self.show_page('chat')
            return
        hidden = not self.sidebar.isHidden()
        self.ui_state['sidebar_hidden'] = hidden
        self.save_state()
        self._show_list(not hidden)

    def show_new(self, project: str | None = None, agent: str | None = None, folder: str | None = None) -> None:
        self._chat_mode()
        self.current_key = ''
        if folder:
            self.new_view.set_folder(folder)
        self.new_view.prepare(project)
        if agent:
            self.new_view.pick_agent(agent)
        self.pages.setCurrentWidget(self.new_view)
        self.sidebar.refresh()

    def open_conversation(self, key: str) -> None:
        if self.store.dispatch.get(key) is None:
            return
        self._chat_mode()
        self.current_key = key
        self.ui_state['last_conversation'] = key
        self.pages.setCurrentWidget(self.conversation)
        self.conversation.open(key)
        self.sidebar.refresh()

    def show_project(self, pid: str) -> None:
        self._chat_mode()
        self.current_key = ''
        self.project_view.show_project(pid)
        self.pages.setCurrentWidget(self.project_view)
        self.sidebar.refresh()

    def show_page(self, name: str) -> None:
        if name == 'chat':
            if self.pages.currentWidget() in self.extra_pages.values():
                if self.current_key and self.store.dispatch.get(self.current_key):
                    self.open_conversation(self.current_key)
                else:
                    self.show_new()
            return
        if name not in self.extra_pages:
            self.open_page(name)
            return
        self.rail.set_current(name)
        self._show_list(False)
        self.rail.buttons['new'].hide()
        self.pages.setCurrentWidget(self.extra_pages[name])
        self.page_changed.emit(name)

    def _rail_picked(self, name: str) -> None:
        if name == 'new':
            self.show_new()
        elif name == 'chat' and self.pages.currentWidget() not in self.extra_pages.values():
            self.toggle_sidebar()
        else:
            self.show_page(name)

    # -- actions -----------------------------------------------------------------
    def add_project(self, show: bool = True) -> dict | None:
        dialog = ProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        try:
            project = self.projects.add(dialog.name.text() or os.path.basename(dialog.root.text()), dialog.root.text())
        except ValueError as e:
            self.notify(str(e))
            return None
        if show:
            self.show_project(project['id'])
        return project

    def move_conversation(self, key: str, project: str) -> None:
        self.store.dispatch.set_project(key, project)
        self.refresh()

    def rename_conversation(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        dialog = RenameDialog(self, row['title'])
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.name.text().strip():
            self.store.dispatch.rename(key, dialog.name.text())
            self.refresh()

    def delete_conversation(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        if row['state'] == 'running':
            self.notify('這個對話還在執行，請先停止。')
            return
        dialog = DeleteDialog(self, row['title'], row['source'])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.remote.isChecked():
            # Removed from the list only after the client has deleted it.
            self.dispatcher.delete(key, True, done=lambda: self.bridge.deleted.emit(key))
        else:
            self._deleted(key)

    def _deleted(self, key: str) -> None:
        row = self.store.dispatch.get(key)
        if row is None:
            return
        self.store.remove_conversation(row['owner'], work_id(row))
        if self.ui_state.get('last_conversation') == key:
            self.ui_state.pop('last_conversation', None)
        if self.current_key == key:
            self.conversation.key = ''
            self.show_new()
        self.refresh()

    def _replaced(self, old: str, new: str) -> None:
        """An edited first message started the conversation over in a new session; the old one leaves the list."""
        row = self.store.dispatch.get(old)
        if row is not None:
            self.store.remove_conversation(row['owner'], work_id(row))
        if self.ui_state.get('last_conversation') == old:
            self.ui_state['last_conversation'] = new
        if self.current_key == old:
            self.open_conversation(new)
        self.refresh()

    def import_conversations(self, project: str | None = None) -> None:
        row = self.store.dispatch.get(self.current_key) if self.current_key else None
        agent = (row or {}).get('client') or ((self.projects.get(project) or {}).get('agent') if project else None) \
            or self.ui_state.get('last_agent') or AGENTS[0][0]
        ImportDialog(self, project, agent).exec()

    def git_status(self, key: str, directory: str) -> None:
        import threading

        from agentdock import gitops

        def run():
            root = gitops.repo_root(directory)
            summary = ''
            if root:
                try:
                    summary = gitops.status(root).summary
                except gitops.GitError:
                    root = ''
            self.bridge.git.emit(key, root or '', summary)
        threading.Thread(target=run, name='AgentDock git status', daemon=True).start()

    def open_git(self, directory: str, report: bool = False) -> None:
        from agentdock import gitops
        from agentdock.ui.git_view import GitDialog
        root = gitops.repo_root(directory)
        if not root:
            if report:
                self.notify('這個資料夾不是 Git 儲存庫。')
            return
        dialog = self.git_windows.get(root)
        if dialog is None:
            dialog = GitDialog(self, root)
            dialog.finished.connect(lambda _r, r=root: (self.git_windows.pop(r, None), self.conversation.check_git()))
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.git_windows[root] = dialog
        else:
            dialog.reload()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def resume_in_terminal(self, key: str) -> None:
        """Continue the session in the client's own terminal UI, explicitly requested by the user."""
        row = self.store.dispatch.get(key)
        command = resume_command(row) if row else None
        if not command:
            self.notify(f"找不到 {row['source'] if row else ''} 的執行檔。")
            return
        folder = row.get('directory') if os.path.isdir(row.get('directory') or '') else None
        try:
            subprocess.Popen(command, cwd=folder, creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        except OSError as e:
            self.notify(f'無法開啟終端機：{e}')

    # -- keyboard ------------------------------------------------------------------
    def new_like_current(self) -> None:
        """Ctrl+N: a new conversation where the open one is (same project or folder, same agent)."""
        row = self.store.dispatch.get(self.current_key) if self.pages.currentWidget() is self.conversation else None
        if row is None:
            self.show_new()
        else:
            pid = self.projects.project_of(row)
            self.show_new(pid, agent=row['client'], folder=None if pid else row.get('directory'))
        self.new_view.composer.text.setFocus()

    def request_options(self, client: str, key: str, directory: str) -> None:
        if client:
            self.dispatcher.options(client, key or None, directory,
                                    lambda options: self.bridge.options.emit(client, key or '', options))

    def _options_arrived(self, client: str, key: str, options: dict) -> None:
        if key:
            self.conversation.options_arrived(key, options)
        else:
            self.new_view.options_arrived(client, options)

    def find_in_conversation(self) -> None:
        if self.pages.currentWidget() is self.conversation and self.current_key:
            self.conversation.find.open_bar()

    def focus_search(self) -> None:
        if self.sidebar.isHidden():
            self.show_page('chat')
            if self.sidebar.isHidden():
                self.toggle_sidebar()
        self.sidebar.search.setFocus()
        self.sidebar.search.selectAll()

    def escape(self) -> None:
        if self.palette_.isVisible():
            self.palette_.hide()
            return
        focus = QApplication.focusWidget()
        if self.sidebar.search.text() and (focus is None or focus is self.sidebar.search):
            self.sidebar.search.clear()
            return
        if self.conversation.find.isVisible():
            self.conversation.find.close_bar()
            return
        row = self.store.dispatch.get(self.current_key) if self.pages.currentWidget() is self.conversation else None
        if row and row['state'] == 'running':
            self.dispatcher.interrupt(self.current_key)

    def step_conversation(self, step: int) -> None:
        keys = self.sidebar.keys()
        if not keys:
            return
        if self.current_key in keys:
            index = max(0, min(len(keys) - 1, keys.index(self.current_key) + step))
        else:
            index = 0 if step > 0 else len(keys) - 1
        self.open_conversation(keys[index])

    def chat_dir(self) -> Path:
        """The empty folder 只聊天 conversations share (in AgentDock's data, never in Git)."""
        return Path(self.store.dispatch.file).parent / 'chat'

    def is_chat_dir(self, folder: str) -> bool:
        return bool(folder) and os.path.normcase(os.path.normpath(folder)) == \
            os.path.normcase(os.path.normpath(str(self.chat_dir())))

    def target_for(self, kind: str) -> dict:
        # Without a registered profile the owner still matches what the client's hooks would use.
        return next((t for t in self.targets() if t['client'] == kind),
                    {'label': dict(AGENTS).get(kind, kind), 'client': kind, 'owner': owner_for(None, kind)})

    # -- updates -----------------------------------------------------------------
    def schedule_refresh(self) -> None:
        if not self._refresh.isActive():
            self._refresh.start()

    def refresh(self) -> None:
        self.sidebar.refresh()
        if self.pages.currentWidget() is self.conversation:
            self.conversation.update_row()
        elif self.pages.currentWidget() is self.project_view:
            self.project_view.refresh()
        elif self.pages.currentWidget() is self.new_view:
            self.new_view.refresh_recent()

    def refresh_theme(self) -> None:
        self.setStyleSheet(_style())
        for composer in (self.new_view.composer, self.conversation.composer):
            composer.restyle()
        for chip in (self.new_view.place, self.new_view.agent_chip):
            chip.restyle()
        self.sidebar.restyle()
        self._frame_mode()
        self.conversation.restyle()
        self.new_view.mascot.restyle()
        from agentdock.ui import cute
        cute.tint_caption(self)
        self.update()
        self.conversation.more_button.setIcon(icon('more', theme.MUTED or '#888888', 18))
        self.rail.set_current(self.rail.current)
        self.sidebar.refresh()
        for widget in self.extra_pages.values():
            if hasattr(widget, 'restyle'):
                widget.restyle()

    def remember(self) -> None:
        g = self.geometry()
        self.ui_state['chat_geometry'] = [g.x(), g.y(), g.width(), g.height()]
        self.save_state()

    # -- Qt -----------------------------------------------------------------------
    def _drop_target(self):
        page = self.pages.currentWidget()
        if page is self.new_view:
            return self.new_view.composer
        if page is self.conversation and self.current_key:
            return self.conversation.composer
        return None

    def dragEnterEvent(self, event):  # noqa: N802
        """Files dropped anywhere on a conversation go to its message box."""
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if self._drop_target() is not None and urls and all(u.isLocalFile() for u in urls):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):  # noqa: N802
        self.dragEnterEvent(event)

    def dropEvent(self, event):  # noqa: N802
        target = self._drop_target()
        if target is not None:
            target.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])
            target.text.setFocus()
            event.acceptProposedAction()

    # -- the look's own frame ---------------------------------------------------------
    BORDER = 9

    def _border(self) -> int:
        return self.BORDER if self.frameless and not (self.isMaximized() or self.isFullScreen()) else 0

    def _frame_mode(self) -> None:
        """Frameless with the look's title bar and rounded, laced edge when it draws one; else the system's."""
        frameless = theme.decorated('titlebar')
        if frameless != self.frameless:
            shown = self.isVisible()
            # Changing window flags re-creates the window as a normal one: keep maximized / full screen.
            state = self.windowState()
            geometry = self.normalGeometry() if state & (Qt.WindowState.WindowMaximized
                                                         | Qt.WindowState.WindowFullScreen) else self.geometry()
            self.frameless = frameless
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, frameless)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, frameless)
            # Turning transparency on also set "no system background"; turning it off does not undo that,
            # and the plain window then shows black wherever nothing paints.
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, frameless)
            if shown:
                self.setGeometry(geometry)
                self.setWindowState(state & ~Qt.WindowState.WindowMinimized)
                self.show()
        self.titlebar.setVisible(frameless)
        b = self._border()
        self.outer.setContentsMargins(b, b, b, b)
        self._place_rail()
        self._shape()
        self.update()

    def _shape(self) -> None:
        """The frameless window's rounded corners, cut out so Windows cannot paint them black."""
        if self._border():
            from agentdock.ui import cute
            cute.round_mask(self, 22)
        else:
            self.clearMask()

    def _place_rail(self) -> None:
        b = self._border()
        top = b + (self.titlebar.height() if self.frameless else 0)
        self.rail.setGeometry(b, top, self.rail.width(), self.height() - top - b)
        self.rail.raise_()

    def _edges(self, pos):
        b = self._border()
        if not b:
            return Qt.Edge(0)
        grip = b + 2
        edges = Qt.Edge(0)
        if pos.x() <= grip:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= self.width() - grip:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= grip:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= self.height() - grip:
            edges |= Qt.Edge.BottomEdge
        return edges

    def mouseMoveEvent(self, event):  # noqa: N802
        edges = self._edges(event.position().toPoint())
        shapes = {Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor, Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
                  Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor, Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
                  Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
                  Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                  Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
                  Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor}
        shape = shapes.get(edges)
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(shape)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        edges = self._edges(event.position().toPoint())
        if event.button() == Qt.MouseButton.LeftButton and edges and self.windowHandle() is not None:
            self.windowHandle().startSystemResize(edges)
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if theme.decorated('canvas'):
            paint_decor(self, 'canvas', event, self._border())
        elif self.frameless:   # a transparent window must still get a background
            painter = QPainter(self)
            painter.fillRect(self.rect(), theme_color('CANVAS'))
            painter.end()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._place_rail()
        self._shape()
        palette = getattr(self, 'palette_', None)
        if palette is not None and palette.isVisible():
            palette.open()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        from agentdock.ui import cute
        cute.tint_caption(self)
        # Back to the conversation that was open last time; only when the window is first shown, so a
        # start in the tray never loads anything.
        if not self._restored:
            self._restored = True
            last = self.ui_state.get('last_conversation')
            if last and self.pages.currentWidget() is self.new_view and self.store.dispatch.get(last):
                self.open_conversation(last)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if not hasattr(self, 'titlebar'):
            return   # still being built
        if event.type() == QEvent.Type.WindowStateChange and self.frameless:
            self._frame_mode()
        if event.type() == QEvent.Type.WindowTitleChange:
            self.titlebar.update()
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.conversation.update_row()

    def closeEvent(self, event):  # noqa: N802
        self.conversation.composer.save_draft()
        self.remember()
        self.hide()
        event.ignore()   # keep the window and its drafts; the app owns its lifetime
        self.closed.emit()
