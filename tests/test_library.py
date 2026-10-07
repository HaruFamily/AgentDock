import io, json, zipfile
import pytest
import agentdock.library as libmod
from agentdock.library import Library
from agentdock.agents import Profile

P = Profile(id="11111111-1111-1111-1111-111111111111", name="Codex", kind="codex", path="x")


def test_local_uvx_uses_only_existing_portable_runner(tmp_path, monkeypatch):
    from agentdock import paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    assert paths.local_uvx() is None
    runner = tmp_path / "runtime" / "uv" / "uvx.exe"
    runner.parent.mkdir(parents=True)
    runner.write_bytes(b"test runner")
    assert paths.local_uvx() == str(runner)


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
        L.remove("agentchat")


@pytest.mark.parametrize("portable", [False, True])
def test_import_existing_config(tmp_path, monkeypatch, portable):
    runner = str(tmp_path / "runtime" / "uv" / "uvx.exe") if portable else None
    monkeypatch.setattr(libmod, "local_uvx", lambda: runner)
    L = Library(tmp_path / "repo", tmp_path / "data")
    entry, secrets = L.from_config("google-sheets", {"command": "uvx", "args": ["--with", "mcp<2", "mcp-google-sheets@latest"],
                                                     "env": {"SERVICE_ACCOUNT_PATH": "C:/x.json", "GOOGLE_TOKEN": "t"}})
    assert entry["type"] == "uvx" and entry["package"] == "mcp-google-sheets@latest" and entry["options"] == ["--with", "mcp<2"]
    # the token is a secret; the absolute path is per computer, so it stays out of Git as well
    assert entry["secrets"] == ["GOOGLE_TOKEN", "SERVICE_ACCOUNT_PATH"] and "env" not in entry
    assert secrets == {"GOOGLE_TOKEN": "t", "SERVICE_ACCOUNT_PATH": "C:/x.json"}
    L.save(entry, secrets)
    rendered = L.render(L.get("google-sheets"), "claude-desktop", P)
    assert rendered["command"] == (runner or "uvx") and rendered["args"] == ["--with", "mcp<2", "mcp-google-sheets@latest"]
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
    qai = L.get("agentchat")
    assert L.render(qai, "claude-desktop", P)["env"]["AGENTDOCK_MAX_WAIT"] == "50"
    assert "AGENTDOCK_MAX_WAIT" not in L.render(qai, "codex", P)["env"]


def test_local_paths_stay_out_of_git_and_secrets_travel(tmp_path):
    import json
    import pytest
    from agentdock import secretbox
    from agentdock.library import Library
    repo, data = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    (repo / "mcp-library.json").write_text(json.dumps({"schema": 1, "servers": [
        {"key": "sheets", "type": "uvx", "package": "mcp-google-sheets@latest",
         "env": {"SERVICE_ACCOUNT_PATH": "D:\\Keys\\sa.json", "MODE": "x"}}]}), encoding="utf-8")
    L = Library(repo, data)
    assert L.localize_paths() == 1
    stored = json.loads((repo / "mcp-library.json").read_text(encoding="utf-8"))["servers"][0]
    assert "D:\\\\Keys" not in json.dumps(stored) and stored["secrets"] == ["SERVICE_ACCOUNT_PATH"]
    assert stored["env"] == {"MODE": "x"} and L.secrets()["SERVICE_ACCOUNT_PATH"] == "D:\\Keys\\sa.json"
    assert L.render(L.get("sheets"), "codex", None)["env"]["SERVICE_ACCOUNT_PATH"] == "D:\\Keys\\sa.json"
    # a second computer: the value is recovered from an Agent config that already has it, never overwritten
    other = Library(repo, tmp_path / "data2")
    assert other.recover_secrets(other.get("sheets"), {"env": {"SERVICE_ACCOUNT_PATH": "E:\\sa.json"}}) == ["SERVICE_ACCOUNT_PATH"]
    assert other.recover_secrets(other.get("sheets"), {"env": {"SERVICE_ACCOUNT_PATH": "F:\\x"}}) == []
    # typed into 環境變數 in the dialog: moved to secrets as well
    L.save({"key": "tool", "type": "command", "command": "x.exe", "env": {"CFG": "C:\\tool\\cfg.ini"}})
    assert L.get("tool")["secrets"] == ["CFG"] and "env" not in L.get("tool")
    # password-protected bundle
    L.set_secrets({"API_KEY": "k1"})
    sealed = secretbox.seal(L.secrets(), "pw")
    # Random base64 ciphertext can contain the two-character substring k1.
    assert json.dumps("k1") not in sealed and "sa.json" not in sealed
    with pytest.raises(ValueError):
        secretbox.open_sealed(sealed, "wrong")
    values = secretbox.open_sealed(sealed, "pw")
    add, added, kept = secretbox.merge(other.secrets(), values)
    assert added == ["API_KEY", "CFG"] and kept == ["SERVICE_ACCOUNT_PATH"]   # E:\sa.json on the second computer stays
