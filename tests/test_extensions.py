import json
import os

import pytest

from agentdock import userpath
from agentdock.agents import AgentManager
from agentdock.extensions import PRESETS, ExtensionManager, agent_dir, merge_summary
from agentdock.library import Library
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_tool_runner(monkeypatch, tmp_path):
    """Fake only tool execution; keep PATH lookup, version cache and file replacement real."""
    import subprocess

    def run(args, **kwargs):
        path = Path(args[0])
        if path.resolve().is_relative_to(tmp_path.resolve()) and path.name.lower() in ("rtk", "rtk.exe"):
            assert args[1:] == ["--version"]
            assert kwargs["timeout"] == 10
            return subprocess.CompletedProcess(args, 0, stdout=path.read_text(), stderr="")
        raise AssertionError(f"Unexpected external tool in isolated test: {path}")

    monkeypatch.setattr(subprocess, "run", run)
    return "rtk.exe" if os.name == "nt" else "rtk"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    (home / ".codex" / "config.toml").write_text("", encoding="utf-8")
    (home / ".config" / "opencode" / "opencode.json").write_text("{}", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agentdock" / "assets" / "opencode").mkdir(parents=True)
    (repo / "agentdock" / "assets" / "opencode" / "rtk.ts").write_text((ROOT / "agentdock/assets/opencode/rtk.ts").read_text("utf-8"), "utf-8")
    L = Library(repo, tmp_path / "data")
    L.save_extension(PRESETS[0])
    m = AgentManager(tmp_path / "data")
    p = {
        "cc": m.add("Claude Code", "claude-code", str(home / ".claude.json")),
        "codex": m.add("Codex", "codex", str(home / ".codex" / "config.toml")),
        "oc": m.add("OpenCode", "opencode", str(home / ".config" / "opencode" / "opencode.json")),
        "cd": m.add("Claude Desktop", "claude-desktop", str(tmp_path / "cd" / "claude_desktop_config.json")),
    }
    return L, m, p, home


def test_library_keeps_extensions_and_validates(env):
    L, _, _, _ = env
    L.save({"key": "s", "type": "command", "command": "x"})
    assert L.get_extension("rtk") and L.get("s")  # writing MCP servers keeps extensions (and back)
    with pytest.raises(ValueError):
        L.save({"key": "rtk", "type": "command", "command": "x"})  # names are unique across both
    with pytest.raises(ValueError):
        L.save_extension({"key": "bad", "type": "github", "repo": "a/b", "hooks": {"claude-desktop": {}}})
    with pytest.raises(ValueError):
        L.save_extension({"key": "bad", "type": "github", "repo": "a/b", "hooks": {"opencode": {"plugin": "../x.ts", "source": "s"}}})
    L.remove("s")
    assert L.get_extension("rtk")


def test_install_and_uninstall_hooks_and_plugin(env):
    L, m, p, home = env
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    other = {"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}
    settings.write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [other]}}), encoding="utf-8")
    X = ExtensionManager(L)
    rtk = L.get_extension("rtk")
    assert agent_dir(p["cc"]) == home / ".claude"
    assert [X.status(rtk, p[k]) for k in ("cc", "codex", "oc", "cd")] == [False, False, False, None]

    ops = [{"profile": p[k].id, "ext": "rtk", "op": "install"} for k in ("cc", "codex", "oc")]
    plan = X.prepare(ops, m.list())
    assert len(plan.files) == 3 and len(plan.summary) == 3
    with pytest.raises(ValueError):
        X.prepare([{"profile": p["cd"].id, "ext": "rtk", "op": "install"}], m.list())
    results = X.apply(plan)
    assert sum(1 for r in results if r["backup"]) == 1  # only settings.json existed before
    data = json.loads(settings.read_text("utf-8"))
    assert data["model"] == "x" and data["hooks"]["PreToolUse"][0] == other
    assert data["hooks"]["PreToolUse"][1] == {"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}
    codex = json.loads((home / ".codex" / "hooks.json").read_text("utf-8"))
    assert codex["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "rtk hook codex"
    plugin = home / ".config" / "opencode" / "plugins" / "rtk.ts"
    assert "rtk rewrite" in plugin.read_text("utf-8")
    assert [X.status(rtk, p[k]) for k in ("cc", "codex", "oc")] == [True, True, True]
    assert not X.prepare([ops[0]], m.list()).files  # already installed: nothing to write

    plan = X.prepare([{**o, "op": "uninstall"} for o in ops], m.list())
    X.apply(plan)
    assert json.loads(settings.read_text("utf-8")) == {"model": "x", "hooks": {"PreToolUse": [other]}}
    assert "hooks" not in json.loads((home / ".codex" / "hooks.json").read_text("utf-8"))
    assert not plugin.exists()


def test_detects_hook_written_by_rtk_init_and_stale_preview(env):
    L, m, p, home = env
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": r'"C:\Tools\rtk.exe" hook claude'}]}]}}), encoding="utf-8")
    X = ExtensionManager(L)
    assert X.status(L.get_extension("rtk"), p["cc"]) is True
    plan = X.prepare([{"profile": p["cc"].id, "ext": "rtk", "op": "uninstall"}], m.list())
    settings.write_text("{}", encoding="utf-8")  # changed after preview
    with pytest.raises(ValueError):
        X.apply(plan)


def test_merge_summary():
    a = [{"agent": "A", "path": "x", "changes": ["1"], "reformats": False}]
    b = [{"agent": "A", "path": "y", "changes": ["2"], "reformats": True}, {"agent": "B", "path": "z", "changes": ["3"], "reformats": False}]
    out = merge_summary(a, b)
    assert out[0] == {"agent": "A", "path": "x、y", "changes": ["1", "2"], "reformats": True} and out[1]["agent"] == "B"
    assert a[0]["changes"] == ["1"]


def test_path_problems_and_link(env, tmp_path, monkeypatch):
    L, _, _, _ = env
    rtk = L.get_extension("rtk")
    monkeypatch.setattr(userpath, "current_path", lambda: str(tmp_path / "nowhere"))
    assert L.ext_problems(rtk) == ["需下載"]
    target = L.bin_dir("rtk") / "v1"
    target.mkdir(parents=True)
    exe = target / ("rtk.exe" if os.name == "nt" else "rtk")
    exe.write_bytes(b"MZ")
    exe.chmod(0o755)
    (L.bin_dir("rtk") / ".installed.json").write_text(json.dumps({"exe": f"v1/{exe.name}", "tag": "v1"}), "utf-8")
    if os.name != "nt":
        L.save_extension({**rtk, "exe": "rtk"}, replace="rtk")
        rtk = L.get_extension("rtk")
    assert L.ext_problems(rtk) == ["未加入 PATH"]
    linked = L.publish(rtk, add_path=False)
    assert linked.read_bytes() == b"MZ" and linked.parent == userpath.bin_dir(L.data)
    monkeypatch.setattr(userpath, "current_path", lambda: str(linked.parent))
    assert L.ext_problems(rtk) == [] and L.ext_found(rtk)
    exe.write_bytes(b"MZ2")
    assert L.refresh_link(rtk) and linked.read_bytes() == b"MZ2"


def test_agent_page_shows_extensions(env, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from agentdock.ui.agents_view import AgentsView
    from agentdock.ui.widgets import Row
    L, m, p, home = env
    monkeypatch.setattr(userpath, "current_path", lambda: "")
    view = AgentsView(m, L)
    assert [e["key"] for e in view._addable_extensions(m.get(p["cc"].id))] == ["rtk"]
    assert view._addable_extensions(m.get(p["cd"].id)) == []
    view._set_ext_op(p["cc"].id, "rtk", "install")
    assert view.apply_btn.isVisibleTo(view)
    assert any(r.name.text() == "rtk" for r in view.body.findChildren(Row))
    X = ExtensionManager(L)
    X.apply(X.prepare(view.ext_ops, m.list()))
    view._clear_ops()
    assert X.status(L.get_extension("rtk"), p["cc"]) and view._addable_extensions(m.get(p["cc"].id)) == []


def test_version_of_system_installed_tool(env, tmp_path, monkeypatch, fake_tool_runner):
    from agentdock.library import _newer
    assert _newer("v0.52.0", "0.51.0") and not _newer("v0.51.0", "0.51.0") and not _newer("v0.9", "0.10.1")
    L, _, _, _ = env
    bindir = tmp_path / "sysbin"
    bindir.mkdir()
    tool = bindir / fake_tool_runner
    tool.write_text("rtk 0.51.0\n")
    tool.chmod(0o755)
    monkeypatch.setattr(userpath, "current_path", lambda: str(bindir))
    rtk = L.get_extension("rtk")
    assert L.current_version(rtk) == "0.51.0" and not L.managed_by_agentdock(rtk)
    (L.data / "update-check.json").write_text(json.dumps({"rtk": {"at": 0, "latest": "v0.52.0"}}), "utf-8")
    assert L.cached_update(rtk) == "v0.52.0"
    tool.write_text("rtk 0.52.0 updated\n")  # size changes too, so cache invalidation is deterministic
    assert L.current_version(rtk) == "0.52.0" and L.cached_update(rtk) is None


def test_extension_dialog_form_roundtrip(env):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from agentdock.ui.agents_view import ExtensionDialog
    L, _, _, _ = env
    rtk = L.get_extension("rtk")
    dlg = ExtensionDialog(None, L, rtk)
    dlg.hooks["codex"]["on"].setChecked(False)          # drop Codex
    dlg.hooks["claude-code"]["command"].setText("rtk hook claude --quiet")
    dlg._save()
    saved = L.get_extension("rtk")
    assert "codex" not in saved["hooks"] and saved["hooks"]["claude-code"]["command"] == "rtk hook claude --quiet"
    assert saved["repo"] == "rtk-ai/rtk" and saved["hooks"]["opencode"] == rtk["hooks"]["opencode"]
    new = ExtensionDialog(None, L)
    new._load({**PRESETS[0], "key": "rtk2"})
    new._save()
    assert {k: v for k, v in L.get_extension("rtk2").items() if k != "key"} == {k: v for k, v in PRESETS[0].items() if k != "key"}


def test_update_replaces_system_copy_and_refreshes_plugin(env, tmp_path, monkeypatch, fake_tool_runner):
    import io, zipfile
    import agentdock.library as libmod
    L, m, p, home = env
    rtk_def = L.get_extension("rtk")
    L.save_extension({**rtk_def, "exe": fake_tool_runner}, replace="rtk")
    rtk = L.get_extension("rtk")
    sysbin = tmp_path / "sysbin"
    sysbin.mkdir()
    tool = sysbin / fake_tool_runner
    tool.write_text("rtk 0.51.0\n")
    tool.chmod(0o755)
    monkeypatch.setattr(userpath, "current_path", lambda: str(sysbin))
    X = ExtensionManager(L)
    X.apply(X.prepare([{"profile": p["oc"].id, "ext": "rtk", "op": "install"}], m.list()))
    assert X.in_sync(rtk, p["oc"])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(fake_tool_runner, "rtk 0.52.0 updated\n")
    release = {"tag_name": "v0.52.0", "assets": [{"name": "rtk-x86_64-pc-windows-msvc.zip", "browser_download_url": "https://x/z"}]}
    def fake_get(url, progress=None):
        if "api.github.com" in url:
            return json.dumps(release).encode()
        if url.endswith("/v0.52.0/hooks/opencode/rtk.ts"):
            return b"// plugin for v0.52.0\n"
        return buf.getvalue()
    monkeypatch.setattr(libmod, "_get", fake_get)
    monkeypatch.setattr(libmod, "_download", lambda url, progress: fake_get(url))
    L.install(rtk)
    assert L.deploy_target(rtk) == tool
    assert L.deploy(rtk) == tool
    assert L.current_version(rtk) == "0.52.0" and L.managed_by_agentdock(rtk)
    assert not X.in_sync(rtk, p["oc"])                       # new release ships a new plugin
    ops = X.refresh_ops(rtk, m.list())
    assert ops == [{"profile": p["oc"].id, "ext": "rtk", "op": "install"}]
    X.apply(X.prepare(ops, m.list()))
    assert (home / ".config" / "opencode" / "plugins" / "rtk.ts").read_text() == "// plugin for v0.52.0\n"
    assert X.refresh_ops(rtk, m.list()) == []


def test_extension_library_sorting(env):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from agentdock.ui.agents_view import AgentsView
    from agentdock.ui.widgets import Row
    L, m, _p, _home = env
    L.save_extension({"key": "aaa-tool", "type": "command", "command": "aaa", "hooks": {"codex": {"event": "PreToolUse", "matcher": "Bash", "command": "aaa hook"}}})
    view = AgentsView(m, L)
    names = lambda: [r.name.text() for r in view.lay.itemAt(2).widget().findChildren(Row)]
    assert names() == ["aaa-tool", "rtk"]                     # by name
    view._set_sort("extensions", "type")
    assert names() == ["rtk", "aaa-tool"]                     # GitHub Release before 已安裝的指令
    assert view.ui_state["sort"]["extensions"] == "type"
