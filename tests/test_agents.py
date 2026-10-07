import json
import tomlkit
import pytest
from agentdock.agents import AgentManager, strip_jsonc, default_path
from agentdock.library import Library


def lib(tmp_path):
    return Library(tmp_path / "repo", tmp_path / "data")


def test_jsonc():
    text, changed = strip_jsonc('{"a": "x // not comment", // c\n "b": [1,2,], /* z */}')
    assert json.loads(text) == {"a": "x // not comment", "b": [1, 2]} and changed


def test_codex_add_qai_preserves_comments_toggle_remove(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('# mine\nmodel = "x"\n\n[mcp_servers.keep]\ncommand = "keep" # inline\n', encoding="utf-8")
    m, L = AgentManager(tmp_path / "data"), lib(tmp_path)
    p = m.add("Codex", "codex", str(cfg))
    qai = L.get("inbox")
    res = m.apply(m.prepare([{"profile": p.id, "server": "inbox", "op": "add", "entry": L.render(qai, "codex", p)}]))
    assert res[0]["backup"]
    text = cfg.read_text(encoding="utf-8")
    assert "# mine" in text and "# inline" in text
    doc = tomlkit.parse(text).unwrap()
    assert doc["mcp_servers"]["inbox"]["args"] == ["-m", "agentdock.mcp_server"]
    assert doc["mcp_servers"]["inbox"]["tool_timeout_sec"] == 1860
    rows = {r["name"]: r for r in m.inspect()[0]["servers"]}
    assert rows["inbox"]["qai"] and L.in_sync(qai, "codex", p, rows["inbox"]["config"])
    with pytest.raises(ValueError):
        m.prepare([{"profile": p.id, "server": "inbox", "op": "add", "entry": {}}])
    m.apply(m.prepare([{"profile": p.id, "server": "keep", "op": "disable"}]))
    assert {r["name"]: r for r in m.inspect()[0]["servers"]}["keep"]["enabled"] is False
    m.apply(m.prepare([{"profile": p.id, "server": "keep", "op": "remove"}]))
    assert "keep" not in tomlkit.parse(cfg.read_text(encoding="utf-8")).unwrap()["mcp_servers"]


def test_claude_parks_restores_and_update_keeps_state(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"preferences": {"x": 1}, "mcpServers": {"google-sheets": {"command": "uvx", "args": ["a"]}}}), encoding="utf-8")
    m = AgentManager(tmp_path / "data")
    p = m.add("Claude", "claude-desktop", str(cfg))
    m.apply(m.prepare([{"profile": p.id, "server": "google-sheets", "op": "disable"}]))
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {} and data["preferences"] == {"x": 1}
    with pytest.raises(ValueError):
        m.remove(p.id)
    m.apply(m.prepare([{"profile": p.id, "server": "google-sheets", "op": "update", "entry": {"command": "uvx", "args": ["b"]}}]))
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"] == {}  # still disabled
    m.apply(m.prepare([{"profile": p.id, "server": "google-sheets", "op": "enable"}]))
    assert json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["google-sheets"]["args"] == ["b"]


def test_claude_desktop_rejects_remote_and_stale_preview(tmp_path):
    cfg = tmp_path / "c.json"
    cfg.write_text("{}", encoding="utf-8")
    m = AgentManager(tmp_path / "data")
    p = m.add("C", "claude-desktop", str(cfg))
    with pytest.raises(ValueError):
        m.prepare([{"profile": p.id, "server": "r", "op": "add", "entry": {"url": "https://x"}}])
    plan = m.prepare([{"profile": p.id, "server": "s", "op": "add", "entry": {"command": "x"}}])
    cfg.write_text('{"a":1}', encoding="utf-8")
    with pytest.raises(ValueError):
        m.apply(plan)


def test_opencode_jsonc_reformat_flag(tmp_path):
    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text('{\n // c\n "mcp": {}\n}', encoding="utf-8")
    m = AgentManager(tmp_path / "data")
    p = m.add("OC", "opencode", str(cfg))
    plan = m.prepare([{"profile": p.id, "server": "docs", "op": "add", "entry": {"type": "remote", "url": "https://e.com", "enabled": True}}])
    assert plan.summary[0]["reformats"]


def test_store_claude_default(tmp_path, monkeypatch):
    pkg = tmp_path / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude"
    pkg.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_path("claude-desktop") == str(pkg / "claude_desktop_config.json")


def test_opencode_default_prefers_existing_jsonc(tmp_path, monkeypatch):
    from agentdock.agents import default_path
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    folder = tmp_path / ".config" / "opencode"
    folder.mkdir(parents=True)
    assert default_path("opencode").endswith("opencode.json")
    (folder / "opencode.jsonc").write_text("{}", encoding="utf-8")
    assert default_path("opencode").endswith("opencode.jsonc")
    (folder / "opencode.json").write_text("{}", encoding="utf-8")
    assert default_path("opencode").endswith("opencode.json")


def test_prune_backups_keeps_newest(tmp_path):
    import os
    from agentdock.files import prune_backups
    cfg = tmp_path / "c.json"
    cfg.write_text("{}")
    for i in range(5):
        b = tmp_path / f"c.json.agentdock-{i}-x.bak"
        b.write_text(str(i))
        os.utime(b, ns=(i * 10**9, i * 10**9))
    (tmp_path / "c.json.bak-mine").write_text("keep")
    prune_backups(cfg, keep=3)
    assert sorted(p.name for p in tmp_path.glob("c.json.agentdock-*.bak")) == [f"c.json.agentdock-{i}-x.bak" for i in (2, 3, 4)]
    assert (tmp_path / "c.json.bak-mine").exists()
