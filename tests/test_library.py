import io, json, zipfile
import pytest
import agentdock.library as libmod
from agentdock.library import Library
from agentdock.agents import Profile

P = Profile(id="11111111-1111-1111-1111-111111111111", name="Codex", kind="codex", path="x")


def test_definitions_secrets_and_render(tmp_path, monkeypatch):
    L = Library(tmp_path / "repo", tmp_path / "data")
    with pytest.raises(ValueError):
        L.save({"key": "s", "type": "command", "command": "x", "env": {"API_KEY": "leak"}})
    L.save({"key": "s", "type": "command", "command": "{HOME}/bin/s", "args": ["--root", "{AGENTDOCK}"], "secrets": ["API_KEY"]}, {"API_KEY": "sekret-123"})
    stored = json.loads((tmp_path / "repo" / "mcp-library.json").read_text())
    assert "sekret-123" not in json.dumps(stored)  # secret never in the versioned file
    out = L.render(L.get("s"), "codex", P)
    assert out["env"] == {"API_KEY": "sekret-123"} and out["args"][1] == str(tmp_path / "repo") and out["enabled"]
    oc = L.render(L.get("s"), "opencode", P)
    assert oc["type"] == "local" and oc["environment"]["API_KEY"] == "sekret-123"
    L.save({"key": "web", "type": "remote", "url": "https://e.com/mcp"})
    assert L.render(L.get("web"), "claude-code", P) == {"type": "http", "url": "https://e.com/mcp"}
    with pytest.raises(ValueError):
        L.render(L.get("web"), "claude-desktop", P)
    with pytest.raises(ValueError):
        L.remove("agentdock-qa")


def test_import_existing_config(tmp_path):
    L = Library(tmp_path / "repo", tmp_path / "data")
    entry, secrets = L.from_config("google-sheets", {"command": "uvx", "args": ["--with", "mcp<2", "mcp-google-sheets@latest"],
                                                     "env": {"SERVICE_ACCOUNT_PATH": "C:/x.json", "GOOGLE_TOKEN": "t"}})
    assert entry["type"] == "uvx" and entry["package"] == "mcp-google-sheets@latest" and entry["options"] == ["--with", "mcp<2"]
    assert entry["secrets"] == ["GOOGLE_TOKEN"] and secrets == {"GOOGLE_TOKEN": "t"}
    L.save(entry, secrets)
    rendered = L.render(L.get("google-sheets"), "claude-desktop", P)
    assert rendered["command"] == "uvx" and rendered["args"] == ["--with", "mcp<2", "mcp-google-sheets@latest"]
    entry2, _ = L.from_config("pkg", {"command": "cmd", "args": ["/c", "npx", "-y", "@scope/srv", "--flag"]})
    assert entry2["type"] == "npx" and entry2["package"] == "@scope/srv" and entry2["args"] == ["--flag"]


def test_github_install(tmp_path, monkeypatch):
    L = Library(tmp_path / "repo", tmp_path / "data")
    L.save({"key": "cmm", "type": "github", "repo": "DeusData/codebase-memory-mcp", "exe": "codebase-memory-mcp.exe"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pkg/codebase-memory-mcp.exe", b"MZ")
    release = {"tag_name": "v0.8.1", "assets": [
        {"name": "codebase-memory-mcp-linux-amd64.tar.gz", "browser_download_url": "https://x/linux"},
        {"name": "codebase-memory-mcp-windows-amd64.zip.sha256", "browser_download_url": "https://x/sha"},
        {"name": "codebase-memory-mcp-ui-windows-amd64.zip", "browser_download_url": "https://x/ui"},
        {"name": "codebase-memory-mcp-windows-amd64.mcpb", "browser_download_url": "https://x/mcpb"},
        {"name": "codebase-memory-mcp-windows-amd64.zip.bundle", "browser_download_url": "https://x/bundle"},
        {"name": "codebase-memory-mcp-windows-amd64.zip", "browser_download_url": "https://x/win"}]}
    calls = []
    def fake_get(url, progress=None):
        calls.append(url)
        return json.dumps(release).encode() if "api.github.com" in url else buf.getvalue()
    monkeypatch.setattr(libmod, "_get", fake_get)
    monkeypatch.setattr(libmod, "_download", lambda url, progress: fake_get(url))
    entry = L.get("cmm")
    assert L.problems(entry) == ["需下載"]
    with pytest.raises(ValueError):
        L.render(entry, "codex", P)
    exe = L.install(entry)
    assert calls[-1] == "https://x/win" and exe.name == "codebase-memory-mcp.exe"
    assert L.problems(entry) == [] and L.render(entry, "codex", P)["command"] == str(exe)


def test_use_existing_local_install(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    exe = tmp_path / "local" / "Programs" / "cmm" / "cmm.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    L = Library(tmp_path / "repo", tmp_path / "data")
    L.save({"key": "cmm", "type": "github", "repo": "a/b", "local": ["{LOCALAPPDATA}/Programs/cmm/cmm.exe"]})
    e = L.get("cmm")
    assert L.local_candidate(e) == exe and L.problems(e) == ["需下載"]
    L.use_local(e, exe)
    assert L.problems(e) == [] and L.render(e, "claude-code", P)["command"] == str(exe.resolve())


def test_versioned_update_and_cleanup(tmp_path, monkeypatch):
    L = Library(tmp_path / "repo", tmp_path / "data")
    L.save({"key": "cmm", "type": "github", "repo": "a/b", "exe": "cmm.exe"})
    tag = {"v": "v1"}
    def fake(url, progress=None):
        if "api.github.com" in url:
            return json.dumps({"tag_name": tag["v"], "assets": [{"name": "cmm-windows-amd64.zip", "browser_download_url": "https://x/z"}]}).encode()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("cmm.exe", tag["v"].encode())
        return buf.getvalue()
    monkeypatch.setattr(libmod, "_get", fake)
    monkeypatch.setattr(libmod, "_download", lambda url, progress: fake(url))
    e = L.get("cmm")
    first = L.install(e)
    assert first.parent.name == "v1"
    assert L.check_update(e) is None
    tag["v"] = "v2"
    assert L.check_update(e) is None            # cached for a day
    assert L.check_update(e, force=True) == "v2" and L.cached_update(e) == "v2"
    old_render = L.render(e, "codex", P)["command"]
    second = L.install(e)
    assert second.parent.name == "v2" and L.cached_update(e) is None
    assert L.render(e, "codex", P)["command"] != old_render   # agents see "needs update"
    L.cleanup(e)
    assert not first.parent.exists() and second.exists()


def test_upgrade_old_command_entries(tmp_path):
    L = Library(tmp_path / "repo", tmp_path / "data")
    L.save({"key": "gs", "type": "command", "command": "uvx", "args": ["--with", "mcp<2", "mcp-google-sheets@latest"], "description": "d"})
    L.save({"key": "tool", "type": "command", "command": "{HOME}/x.exe"})
    assert L.upgrade_commands() == 1
    assert L.get("gs")["type"] == "uvx" and L.get("gs")["description"] == "d" and L.get("tool")["type"] == "command"


def test_qai_wait_slice_for_claude_desktop(tmp_path):
    L = Library(tmp_path / "repo", tmp_path / "data")
    qai = L.get("agentdock-qa")
    assert L.render(qai, "claude-desktop", P)["env"]["AGENTDOCK_MAX_WAIT"] == "50"
    assert "AGENTDOCK_MAX_WAIT" not in L.render(qai, "codex", P)["env"]
