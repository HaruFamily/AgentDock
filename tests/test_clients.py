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


def _pe(path, subsystem):
    head = bytearray(512)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")
    head[0x80:0x84] = b"PE\0\0"
    head[0x80 + 92:0x80 + 94] = subsystem.to_bytes(2, "little")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(head))


def test_gui_command_avoids_console_pythonw(tmp_path, monkeypatch):
    import agentdock.paths as paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    base = tmp_path / "base"
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text(f"home = {base}\nversion_info = 3.14\n", encoding="utf-8")
    _pe(tmp_path / ".venv" / "Scripts" / "pythonw.exe", 3)   # console launcher (the cmd window case)
    _pe(base / "pythonw.exe", 2)
    assert paths.gui_command("--background") == [str(base / "pythonw.exe"), str(tmp_path / "AgentDock.pyw"), "--background"]
    _pe(tmp_path / ".venv" / "Scripts" / "pythonw.exe", 2)   # proper GUI launcher
    assert paths.gui_command() == [str(tmp_path / ".venv" / "Scripts" / "pythonw.exe"), "-m", "agentdock"]


def test_portable_runtime_is_preferred(tmp_path, monkeypatch):
    import agentdock.paths as paths
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    (tmp_path / ".venv").mkdir()
    assert paths.venv_dir() == tmp_path / ".venv" and paths.local_uvx() is None
    (tmp_path / "runtime" / "venv").mkdir(parents=True)
    (tmp_path / "runtime" / "venv" / "pyvenv.cfg").write_text("home = x\n")
    (tmp_path / "runtime" / "uv").mkdir()
    (tmp_path / "runtime" / "uv" / "uvx.exe").write_bytes(b"MZ")
    assert paths.venv_dir() == tmp_path / "runtime" / "venv"
    assert paths.local_uvx() == str(tmp_path / "runtime" / "uv" / "uvx.exe")


def test_probe_handshake(tmp_path):
    import sys
    from agentdock import probe
    server = tmp_path / "srv.py"
    server.write_text(
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m.get('method') == 'initialize':\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {}}), flush=True)\n"
        "    elif m.get('method') == 'tools/list':\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'tools': [{'name': 'a'}]}}), flush=True)\n")
    ok = probe.probe({"command": sys.executable, "args": [str(server)]}, timeout=20)
    assert ok["ok"] and ok["tools"] == 1
    bad = probe.probe({"command": [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"]}, timeout=20)
    assert bad["ok"] is False and "boom" in bad["summary"]
    assert probe.probe({"url": "https://x"})["ok"] is None
