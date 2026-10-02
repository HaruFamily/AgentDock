import agentdock.clients as c


def test_restart_detection(monkeypatch):
    procs = [("Codex.exe", r"C:\Program Files\WindowsApps\OpenAI.Codex\Codex.exe", 100.0),
             ("claude.exe", r"C:\Program Files\WindowsApps\Claude_pzs8sxrjxfjjc\app\claude.exe", 300.0),
             ("claude.exe", r"C:\Users\me\.vscode\extensions\anthropic.claude-code\claude.exe", 50.0)]
    monkeypatch.setattr(c, "_processes", lambda: iter(procs))
    if not c.available():
        return
    kinds = {"codex": {"a"}, "claude-desktop": {"b"}, "claude-code": {"d"}, "opencode": {"e"}}
    since = {"a": 200.0, "b": 200.0, "d": 200.0, "e": 200.0}
    # codex started before the write -> stale; desktop restarted after -> ok;
    # claude code (VS Code) still old -> stale; opencode not running -> ok
    assert c.still_old(kinds, since) == {"a", "d"}
