import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QRadioButton, QLineEdit, QPushButton
from agentdock.qa.store import QuestionStore
from agentdock.ui.qa_view import QaView

app = QApplication.instance() or QApplication([])


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
    submit = [b for b in view.detail.findChildren(QPushButton) if b.text() == "提交"][0]
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


def test_mcp_dialog_and_agent_page(tmp_path):
    import json
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
    assert data["sheets"] == {"command": "uvx", "args": ["mcp-google-sheets@latest"], "env": {"API_TOKEN": "abc"}} and "x" not in data


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


def test_old_qai_name_migrates_and_sorting(tmp_path):
    from agentdock.agents import AgentManager
    from agentdock.library import Library, QAI_KEY
    from agentdock.ui.agents_view import AgentsView
    import tomlkit
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.zeta]\ncommand = "z"\n[mcp_servers.agentdock_qa]\ncommand = "old.exe"\n'
                   '[mcp_servers.agentdock_qa.env]\nAIL_DATA_DIR = "x"\n[mcp_servers.alpha]\ncommand = "a"\n', encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), Library(tmp_path / "repo", tmp_path / "data")
    p = m.add("Codex", "codex", str(cfg))
    b = m.add("Another", "codex", str(tmp_path / "other.toml"))
    view = AgentsView(m, L)
    view._update_all()
    m.apply(m.prepare(view.ops))
    servers = tomlkit.parse(cfg.read_text(encoding="utf-8")).unwrap()["mcp_servers"]
    assert QAI_KEY in servers and "agentdock_qa" not in servers
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
    submit = [b for b in view.detail.findChildren(QPushButton) if b.text() == "全部提交"][0]
    submit.click()                                 # Q2 empty -> rejected, nothing committed
    assert all(m["status"] == "pending" for m in store.group_members(g[0]["id"]))
    view._open_note(store.get(g[1]["id"]), "b")
    view.blocks[g[1]["id"]]["option_rows"]["b"]["note"].setPlainText("why")
    view._toggle(store.get(g[1]["id"]), "b", True)
    submit.click()
    done = store.group_members(g[0]["id"])
    assert [m["status"] for m in done] == ["answered", "answered"] and done[1]["answer"]["notes"] == {"b": "why"}
