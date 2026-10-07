import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QRadioButton, QLineEdit, QPushButton, QLabel
from agentdock.qa.store import QuestionStore
from agentdock.ui import theme
from agentdock.ui.qa_view import QaView

app = QApplication.instance() or QApplication([])


def test_mcp_edit_refreshes_pending_and_offers_apply(tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QDialog, QMessageBox
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView, McpDialog, PreviewDialog
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"mcp": {"sheets": {"type": "local", "command": ["old"], "enabled": True}}}))
    original = cfg.read_text()
    manager = AgentManager(tmp_path / "data")
    library = Library(tmp_path / "repo", tmp_path / "data")
    manager.add("OpenCode", "opencode", str(cfg))
    view = AgentsView(manager, library)
    commands = iter(["first", "second"])
    def edit(dialog):
        dialog.w["command"].setText(next(commands))
        dialog._save()
        return QDialog.DialogCode.Accepted
    notices = []
    monkeypatch.setattr(McpDialog, "exec", edit)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: notices.append(box.text()))
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: None)
    view._edit_mcp(library.get("sheets"))
    view._edit_mcp(library.get("sheets"))
    assert len(view.ops) == 1
    assert view.ops[0]["entry"]["command"] == ["second"]
    assert cfg.read_text() == original  # choosing later never writes client settings
    assert all("OpenCode" in notice for notice in notices)
    assert any("1 個代理待同步" in w.text() for w in view.findChildren(QLabel))
    view.ops.clear()
    view.refresh()  # the mismatch remains discoverable after abandoning the queue
    assert len(view.needs) == 1
    view.queue_updates("sheets")
    monkeypatch.setattr(PreviewDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    view._apply()
    assert cfg.read_text() == original
    monkeypatch.setattr(PreviewDialog, "exec", lambda dialog: QDialog.DialogCode.Accepted)
    view._apply()
    assert json.loads(cfg.read_text())["mcp"]["sheets"]["command"] == ["second"]
    assert list(tmp_path.glob("*.bak"))
    assert not view.ops
    view.close()


def test_mcp_edit_shows_paths_but_preserves_hidden_secrets(tmp_path):
    from agentdock.library import Library
    from agentdock.ui.agents_view import McpDialog
    library = Library(tmp_path / "repo", tmp_path / "data")
    path = str(tmp_path / "credentials.json")
    entry = library.save({"key": "sheets", "type": "command", "command": "run",
                          "secrets": ["CREDENTIALS_PATH", "API_KEY"]},
                         {"CREDENTIALS_PATH": path, "API_KEY": "private-value"})
    dialog = McpDialog(None, library, entry)
    assert path in dialog.w["secrets"].toPlainText()
    assert "private-value" not in dialog.w["secrets"].toPlainText()
    assert "已設定" in dialog.secret_status.text()
    dialog._save()
    assert library.secrets()["API_KEY"] == "private-value"
    dialog.close()


@pytest.mark.parametrize("mode", ["single", "multiple"])
def test_option_entire_row_clicks_without_toggling_notes(tmp_path, mode):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton
    store = QuestionStore(tmp_path)
    q = store.create("a" * 64, "C", {"request_key": "row", "work_id": "w", "work_title": "W",
        "question": "Pick", "images": [], "mode": mode, "wait_seconds": 60,
        "options": [{"id": "x", "label": "X", "description": "Description", "images": []},
                    {"id": "y", "label": "Y", "description": "", "images": []}]})
    view = QaView(store)
    view.resize(600, 800)
    view.open_question(q["id"])
    view.show()
    app.processEvents()
    rows = view.blocks[q["id"]]["option_rows"]
    row = rows["x"]["row"]
    choice = next(b for b in row.findChildren(QAbstractButton) if b.text() == "X")
    QTest.mouseClick(choice, Qt.MouseButton.LeftButton, pos=QPoint(choice.width() - 4, choice.height() // 2))
    assert view.drafts[q["id"]]["selected"] == ["x"]
    if mode == "multiple":
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(row.width() - 2, row.height() - 2))
        assert view.drafts[q["id"]]["selected"] == []
    else:
        QTest.mouseClick(rows["y"]["row"], Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
        assert view.drafts[q["id"]]["selected"] == ["y"]
    before = list(view.drafts[q["id"]]["selected"])
    rows["x"]["add"].click()
    QTest.mouseClick(rows["x"]["note"].viewport(), Qt.MouseButton.LeftButton)
    assert view.drafts[q["id"]]["selected"] == before
    assert store.get(q["id"])["status"] == "pending"
    choice.setEnabled(False)
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
    assert view.drafts[q["id"]]["selected"] == before
    view.close()


def test_persistent_icon_toggles_card(tmp_path):
    from agentdock.ui.app import Dock, LauncherIcon
    from agentdock.ui.card import FloatingCard
    dock = Dock.__new__(Dock)
    dock.ball = FloatingCard()
    dock.icon = LauncherIcon()
    dock.ui = {}
    dock.ui_file = tmp_path / "ui.json"
    dock.icon.set_mode("heart")
    dock.icon.move(600, 500)
    dock.icon.mode_changed.connect(dock._icon_clicked)
    dock.ball.mode_changed.connect(dock._mode_changed)
    dock.ball.show()
    dock._sync_icon()
    assert dock.icon.isVisible() and dock.ball.mode == "card"
    dock.icon.set_mode("card")  # the launcher click requests a toggle without resizing
    assert dock.ball.mode == "heart" and dock.ball.isVisible()
    assert dock.ball.pos() == dock.icon.pos()
    dock.ball.set_mode("card")
    assert dock.icon.isVisible() and dock.ball.isVisible()
    assert not dock.ball.geometry().intersects(dock.icon.geometry())
    dock.ball.close()
    dock.icon.close()


def test_startup_centers_icon_on_right_even_with_saved_position(tmp_path):
    from agentdock.ui.app import Dock, LauncherIcon
    from agentdock.ui.card import FloatingCard
    for saved in ({}, {"card_mode": "card", "icon_pos": [500, 200]}, {"ball": [50, 80]}):
        dock = Dock.__new__(Dock)
        dock.ball = FloatingCard()
        dock.icon = LauncherIcon()
        dock.icon.set_mode("heart")
        dock.ball.set_mode("heart")
        dock.ui = saved.copy()
        dock.ui_file = tmp_path / "ui.json"
        dock.ball.mode_changed.connect(dock._mode_changed)
        dock.ball.page_changed.connect(lambda _page: dock._save_ui())
        dock._restore_ui()
        dock.ball.show()
        dock._sync_icon()
        assert dock.ball.mode == "heart" and dock.ball.isVisible()
        assert not dock.icon.isVisible()
        assert dock.ball.pos() == dock.icon.pos()
        from agentdock.ui.card import MARGIN
        area = app.primaryScreen().availableGeometry()
        assert dock.ball.x() + dock.ball.width() - MARGIN == area.right() + 1
        assert dock.ball.y() == area.top() + (area.height() - dock.ball.height()) // 2
        dock.ball.close()
        dock.icon.close()


def test_answer_through_widgets(tmp_path):
    store = QuestionStore(tmp_path)
    q = store.create("a" * 64, "Codex", {"request_key": "k", "work_id": "w", "work_title": "W", "question": "Pick", "images": [],
                     "mode": "single", "options": [{"id": "x", "label": "X", "description": "", "images": []},
                                                   {"id": "y", "label": "Y", "description": "", "images": []}], "wait_seconds": 60})
    view = QaView(store)
    counts = []
    view.pending_changed.connect(counts.append)
    view.refresh()
    assert counts[-1] == 1
    view.show_first_pending()
    assert view.active == q["id"]
    radios = view.detail.findChildren(QRadioButton)
    radios[1].setChecked(True)
    rows = view.blocks[q["id"]]["option_rows"]
    assert not rows["y"]["note"].isVisibleTo(view.detail)   # selecting does not open a note box
    view._open_note(store.get(q["id"]), "x")      # "＋備註" on an unselected option
    assert rows["x"]["note"].isVisibleTo(view.detail)
    view._close_note_if_empty(store.get(q["id"]), "x")
    app.processEvents()
    assert not rows["x"]["note"].isVisibleTo(view.detail)   # left empty -> folds back
    view._open_note(store.get(q["id"]), "y")
    view.blocks[q["id"]]["option_rows"]["y"]["note"].setPlainText("理由\n第二行")
    view.blocks[q["id"]]["text"].setPlainText("補充")
    submit = [b for b in view.detail.findChildren(QPushButton) if b.text() == theme.t("submit")][0]
    submit.click()
    done = store.get(q["id"])
    assert done["status"] == "answered"
    assert done["answer"] == {"selected": ["y"], "text": "補充", "notes": {"y": "理由\n第二行"}, "attachment_ids": []}
    assert view.stack.currentIndex() == 0 and counts[-1] == 0


def test_new_question_does_not_steal_open_question(tmp_path):
    store = QuestionStore(tmp_path)
    mk = lambda k: store.create("a" * 64, "C", {"request_key": k, "work_id": "w", "work_title": "W", "question": k, "images": [], "mode": "text", "options": [], "wait_seconds": 60})
    first = mk("one")
    view = QaView(store)
    view.open_question(first["id"])
    view.blocks[first["id"]]["text"].setPlainText("typing…")
    mk("two")
    view.refresh()
    view.show_first_pending()
    assert view.active == first["id"] and view.blocks[first["id"]]["text"].toPlainText() == "typing…"


def test_mcp_dialog_and_agent_page(tmp_path, monkeypatch):
    import json
    import agentdock.library as libmod
    runner = str(tmp_path / "runtime" / "uv" / "uvx.exe")
    monkeypatch.setattr(libmod, "local_uvx", lambda: runner)
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView, McpDialog
    L = Library(tmp_path / "repo", tmp_path / "data")
    dlg = McpDialog(None, L)
    dlg._preset_cmm()
    dlg._save()
    assert L.get("codebase-memory-mcp")["asset"].startswith("^codebase-memory-mcp-windows")
    dlg2 = McpDialog(None, L)
    dlg2.type.setCurrentIndex(dlg2.type.findData("uvx"))
    dlg2.w["key"].setText("sheets"); dlg2.w["package"].setText("mcp-google-sheets@latest")
    dlg2.w["secrets"].setPlainText("API_TOKEN=abc")
    dlg2._save()
    assert L.get("sheets")["secrets"] == ["API_TOKEN"] and L.secrets()["API_TOKEN"] == "abc"
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"x": {"command": "a"}}}), encoding="utf-8")
    m = AgentManager(tmp_path / "data")
    p = m.add("C", "claude-code", str(cfg))
    view = AgentsView(m, L)
    view._add_to_agent({"id": p.id, "kind": "claude-code"}, L.get("sheets"))
    view._set_op(p.id, "x", "disable")
    assert len(view.ops) == 2 and view.bar.isVisibleTo(view)
    m.apply(m.prepare(view.ops))
    data = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert data["sheets"] == {"command": runner, "args": ["mcp-google-sheets@latest"], "env": {"API_TOKEN": "abc"}} and "x" not in data


def test_agent_page_collapse_remove_adopts_and_edit(tmp_path):
    import json
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"own": {"command": "a", "args": ["b"]}}}), encoding="utf-8")
    L = Library(tmp_path / "repo", tmp_path / "data")
    m = AgentManager(tmp_path / "data")
    p = m.add("C", "claude-code", str(cfg))
    state, saved = {}, []
    view = AgentsView(m, L, ui_state=state, save_state=lambda: saved.append(1))
    view._remember(f"agent:{p.id}", True)
    view.refresh()
    assert state["collapsed"][f"agent:{p.id}"] is True and saved
    view._remove_server(p.id, "own", {"command": "a", "args": ["b"]})
    assert L.get("own")["command"] == "a"              # adopted before removal: nothing lost
    m.apply(m.prepare(view.ops))
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"] == {}
    m.update(p.id, "Claude Code", "claude-code", str(cfg))
    assert m.get(p.id).name == "Claude Code"


def test_system_entries_untouched_and_unknown_adopted(tmp_path):
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.node_repl]\ncommand = "node"\n[mcp_servers.mine]\ncommand = "x"\n', encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), Library(tmp_path / "repo", tmp_path / "data")
    m.add("Codex", "codex", str(cfg))
    view = AgentsView(m, L)
    assert L.get("mine") and not L.get("node_repl")       # adopted / system skipped
    assert not view.needs                                  # adopted entry is in sync


def test_deleting_from_library_removes_from_agents(tmp_path, monkeypatch):
    import json
    from PySide6.QtWidgets import QMessageBox
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    a, b = tmp_path / "a.json", tmp_path / "b.toml"
    a.write_text(json.dumps({"mcpServers": {"godot-mcp-pro": {"command": "node", "args": ["g.js"]}}}), encoding="utf-8")
    b.write_text('[mcp_servers.godot-mcp-pro]\ncommand = "node"\nargs = ["g.js"]\n', encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), Library(tmp_path / "repo", tmp_path / "data")
    m.add("Claude", "claude-code", str(a))
    m.add("Codex", "codex", str(b))
    view = AgentsView(m, L)
    assert L.get("godot-mcp-pro")                 # auto-adopted
    view._delete_mcp("godot-mcp-pro")
    view.refresh()
    assert not L.get("godot-mcp-pro")             # not re-adopted while removal is pending
    assert sorted(o["op"] for o in view.ops) == ["remove", "remove"]
    m.apply(m.prepare(view.ops))
    view.ops.clear()
    view.refresh()
    assert json.loads(a.read_text(encoding="utf-8"))["mcpServers"] == {} and "godot" not in b.read_text(encoding="utf-8")
    assert not L.get("godot-mcp-pro")


@pytest.mark.parametrize("old_name", ["agentchat", "agentdock_qa", "agentdock-qa"])
def test_old_qai_name_migrates_and_sorting(tmp_path, old_name):
    from agentdock.agents import AgentManager
    from agentdock.library import Library, QAI_KEY
    from agentdock.ui.agents_view import AgentsView
    import tomlkit
    cfg = tmp_path / "config.toml"
    cfg.write_text(('[mcp_servers.zeta]\ncommand = "z"\n[mcp_servers.agentdock_qa]\ncommand = "old.exe"\n'
                   '[mcp_servers.agentdock_qa.env]\nAIL_DATA_DIR = "x"\n[mcp_servers.alpha]\ncommand = "a"\n').replace("agentdock_qa", old_name), encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), Library(tmp_path / "repo", tmp_path / "data")
    p = m.add("Codex", "codex", str(cfg))
    b = m.add("Another", "codex", str(tmp_path / "other.toml"))
    view = AgentsView(m, L)
    view._update_all()
    m.apply(m.prepare(view.ops))
    servers = tomlkit.parse(cfg.read_text(encoding="utf-8")).unwrap()["mcp_servers"]
    assert QAI_KEY == "inbox" and QAI_KEY in servers and old_name not in servers
    view.ops.clear()
    view._reorder([b.id, p.id])
    assert [x.name for x in m.list()] == ["Another", "Codex"]


def test_group_page_submits_all(tmp_path):
    from PySide6.QtWidgets import QPushButton
    store = QuestionStore(tmp_path)
    base = {"request_key": "g", "work_id": "w", "work_title": "W", "question": "兩題一起"}
    items = [{"question": "Q1", "images": [], "mode": "text", "options": []},
             {"question": "Q2", "images": [], "mode": "multiple",
              "options": [{"id": "a", "label": "A", "description": "", "images": []}, {"id": "b", "label": "B", "description": "", "images": []}]}]
    g = store.create_group("a" * 64, "C", base, items)
    view = QaView(store)
    counts = []
    view.pending_changed.connect(counts.append)
    view.refresh()
    assert counts[-1] == 1                         # one unit, not two
    view.show_first_pending()
    assert set(view.blocks) == {g[0]["id"], g[1]["id"]}
    view.blocks[g[0]["id"]]["text"].setPlainText("first")
    submit = [b for b in view.detail.findChildren(QPushButton) if b.text() == theme.t("submit_all")][0]
    submit.click()                                 # Q2 empty -> rejected, nothing committed
    assert all(m["status"] == "pending" for m in store.group_members(g[0]["id"]))
    view._open_note(store.get(g[1]["id"]), "b")
    view.blocks[g[1]["id"]]["option_rows"]["b"]["note"].setPlainText("why")
    view._toggle(store.get(g[1]["id"]), "b", True)
    submit.click()
    done = store.group_members(g[0]["id"])
    assert [m["status"] for m in done] == ["answered", "answered"] and done[1]["answer"]["notes"] == {"b": "why"}


def test_agent_page_keeps_scroll_position(tmp_path):
    import json
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    L = Library(tmp_path / "repo", tmp_path / "data")
    m = AgentManager(tmp_path / "data")
    for i in range(6):
        cfg = tmp_path / f"c{i}.json"
        cfg.write_text(json.dumps({"mcpServers": {f"s{j}": {"command": "x"} for j in range(8)}}), encoding="utf-8")
        p = m.add(f"A{i}", "claude-code", str(cfg))
    view = AgentsView(m, L)
    view.resize(400, 300)
    view.show()
    app.processEvents()
    bar = view.scroll.verticalScrollBar()
    assert bar.maximum() > 200
    bar.setValue(bar.maximum() - 50)
    before = bar.value()
    settle = lambda: [app.processEvents() for _ in range(5)]
    view._set_op(p.id, "s3", "remove")
    settle()
    assert bar.value() == before
    view._add_to_agent({"id": p.id, "kind": "claude-code"}, L.get("s1") or L.entries()[0])
    view.refresh()
    settle()
    assert bar.value() == before


def test_queued_addition_shows_in_sorted_place(tmp_path):
    import json
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    from agentdock.ui.widgets import Row
    L = Library(tmp_path / "repo", tmp_path / "data")
    L.save({"key": "m-mid", "type": "command", "command": "x"})
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"mcpServers": {"a-first": {"command": "x"}, "z-last": {"command": "x"}}}), encoding="utf-8")
    p = AgentManager(tmp_path / "data").add("A", "claude-code", str(cfg))
    view = AgentsView(AgentManager(tmp_path / "data"), L)
    view._add_to_agent({"id": p.id, "kind": "claude-code"}, L.get("m-mid"))
    view.refresh()
    fold = view.lay.itemAt(0).widget()
    names = [r.name.text() for r in fold.findChildren(Row) if r.name.text() in ("a-first", "m-mid", "z-last")]
    assert names[:3] == ["a-first", "m-mid", "z-last"]


def test_mcp_removed_outside_agentdock_is_flagged(tmp_path):
    import json
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}, "other": {"command": "y"}}}), encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), Library(tmp_path / "repo", tmp_path / "data")
    p = m.add("Claude", "claude-code", str(cfg))
    state, summaries = {}, []
    view = AgentsView(m, L, ui_state=state, save_state=lambda: None)
    view.summary_changed.connect(lambda text, alert: summaries.append((text, alert)))
    assert state["known_mcp"][p.id] == ["mine", "other"] and not view.missing
    # a client update rewrites the file without "mine"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "y"}}}), encoding="utf-8")
    view.refresh()
    view._emit_summary(1)
    assert view.missing == {p.id: ["mine"]}
    assert any(n["server"] == "mine" and n["op"] == "add" for n in view.needs)
    assert "1 個 MCP 被移除" in summaries[-1][0] and summaries[-1][1]
    # 補回 = queue an add; after applying, nothing is missing any more
    view._update_all()
    m.apply(m.prepare(view.ops))
    view.ops.clear()
    view.refresh()
    assert "mine" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"] and not view.missing
    # removed again outside, then dismissed with ×: no longer reported
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "y"}}}), encoding="utf-8")
    view.refresh()
    view._forget_missing(p.id, "mine")
    assert not view.missing and state["known_mcp"][p.id] == ["other"]

@pytest.mark.parametrize('kind', ['codex', 'opencode', 'claude-code', 'claude-desktop'])
@pytest.mark.parametrize('enabled', [True, False])
def test_inbox_migration_keeps_enabled_state(tmp_path, kind, enabled):
    from agentdock.agents import AgentManager
    from agentdock.library import Library
    from agentdock.ui.agents_view import AgentsView
    app = QApplication.instance() or QApplication([])
    m = AgentManager(tmp_path / 'data')
    library = Library(tmp_path / 'repo', tmp_path / 'data')
    profile = m.add('Agent', kind, str(tmp_path / ('config.toml' if kind == 'codex' else 'config.json')))
    entry = library.render(library.get('inbox'), kind, profile)
    m.apply(m.prepare([{'profile': profile.id, 'server': 'agentchat', 'op': 'add', 'entry': entry,
                       'enabled': enabled}]))
    view = AgentsView(m, library)
    view._update_all()
    plan = m.prepare(view.ops)
    assert any('agentchat' in change for item in plan.summary for change in item['changes'])
    assert {r['name'] for r in m.inspect()[0]['servers']} == {'agentchat'}
    m.apply(plan)
    rows = m.inspect()[0]['servers']
    assert len(rows) == 1 and rows[0]['name'] == 'inbox'
    assert rows[0]['enabled'] is enabled
    view.close()
