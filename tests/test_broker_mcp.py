import asyncio, json, os, sys, threading, time
from pathlib import Path
import pytest
from agentdock.broker import Broker
from agentdock.qa.store import QuestionStore

ROOT = Path(__file__).resolve().parent.parent


def test_broker_rejects_and_flows(tmp_path):
    store = QuestionStore(tmp_path)
    b = Broker(tmp_path, store)
    good = {"authorization": f"Bearer {b.token}", "host": "127.0.0.1:5", "x-agentdock-owner": "a" * 64}
    assert b.handle("GET", "/health", {**good, "origin": "http://evil"}, b"")[0] == 403
    assert b.handle("GET", "/health", {**good, "authorization": "Bearer x"}, b"")[0] == 403
    assert b.handle("GET", "/health", good, b"")[0] == 200
    body = json.dumps({"source": "T", "input": {"request_key": "r", "work_id": "w", "work_title": "W", "question": "Q", "mode": "text"}}).encode()
    code, data = b.handle("POST", "/questions", good, body)
    assert code == 200
    code, reply = b.handle("GET", f"/questions/{data['id']}", good, b"")
    assert reply["status"] == "pending"
    assert b.handle("GET", f"/questions/{data['id']}", {**good, "x-agentdock-owner": "b" * 64}, b"")[0] == 400


def test_mcp_end_to_end(tmp_path):
    """Real stdio MCP server process <-> broker; the 'user' answers through the store."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    broker.start()
    env = {**os.environ, "AGENTDOCK_DATA_DIR": str(tmp_path), "AGENTDOCK_NO_LAUNCH": "1", "AGENTDOCK_SOURCE": "Tester",
           "AGENTDOCK_CLIENT_ID": "test", "PYTHONPATH": str(ROOT)}

    def answer_later():
        for _ in range(100):
            pending = [q for q in store.list() if q["status"] == "pending"]
            if pending:
                q = pending[0]
                up = store.add_upload(q["id"], "n.txt", b"note")
                store.answer(q["id"], {"selected": ["y"], "text": "ok", "notes": {"y": "why"}, "attachment_ids": [up["id"]]})
                return
            time.sleep(0.1)
    threading.Thread(target=answer_later, daemon=True).start()

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentdock.mcp_server"], env=env, cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                assert {"ask_user", "get_user_answer", "read_answer_attachment"} <= tools
                res = await s.call_tool("ask_user", {"request_key": "r1", "work_id": "w", "work_title": "W", "question": "Go?",
                                                     "mode": "single", "options": [{"id": "y", "label": "Yes"}, {"id": "n", "label": "No"}], "wait_seconds": 30})
                out = json.loads(res.content[0].text)
                assert not res.isError and out["selected_options"] == [{"id": "y", "label": "Yes"}]
                assert out["option_notes"] == {"y": "why"} and out["text"] == "ok"
                att = out["attachments"][0]
                assert att["path"].endswith("n.txt") and open(att["path"], encoding="utf-8").read() == "note"
                assert any(getattr(c, "text", "").endswith("\nnote") for c in res.content)   # inlined as text
                got = await s.call_tool("read_answer_attachment", {"request_id": out["request_id"], "attachment_id": att["id"]})
                assert got.content[0].text.endswith("\nnote")
                q = store.get(out["request_id"])
                assert q["source"] == "Tester"
                # several questions at once
                def answer_group():
                    for _ in range(100):
                        pend = [q for q in store.list() if q["status"] == "pending" and q.get("group")]
                        if len(pend) == 2:
                            e = {"selected": [], "notes": {}, "attachment_ids": []}
                            store.answer_group({pend[0]["id"]: {**e, "text": "one"}, pend[1]["id"]: {**e, "selected": ["b"], "text": ""}})
                            return
                        time.sleep(0.1)
                threading.Thread(target=answer_group, daemon=True).start()
                multi = await s.call_tool("ask_user", {"request_key": "m1", "work_id": "w", "work_title": "W", "question": "兩題",
                                                       "questions": [{"question": "Q1"}, {"question": "Q2", "mode": "single",
                                                                     "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}],
                                                       "wait_seconds": 30})
                mo = json.loads(multi.content[0].text)
                assert not multi.isError and mo["status"] == "answered"
                assert [q["text"] for q in mo["questions"]] == ["one", ""] and mo["questions"][1]["selected_options"][0]["id"] == "b"
                # timeout path never answers
                pend = await s.call_tool("ask_user", {"request_key": "r2", "work_id": "w", "work_title": "W", "question": "Wait", "wait_seconds": 10})
                assert pend.isError and json.loads(pend.content[0].text)["status"] == "pending"
    asyncio.run(run())
    broker.close()


def test_progress_heartbeat(tmp_path, monkeypatch):
    """A client that asks for progress receives heartbeats while the user has not answered yet."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    store = QuestionStore(tmp_path)
    broker = Broker(tmp_path, store)
    broker.start()
    env = {**os.environ, "AGENTDOCK_DATA_DIR": str(tmp_path), "AGENTDOCK_NO_LAUNCH": "1", "AGENTDOCK_CLIENT_ID": "hb",
           "PYTHONPATH": str(ROOT)}
    beats = []

    async def on_progress(progress, total, message):
        beats.append((progress, total, message))

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentdock.mcp_server"], env=env, cwd=str(ROOT))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("ask_user", {"request_key": "hb", "work_id": "w", "work_title": "W",
                                                     "question": "wait", "wait_seconds": 12}, progress_callback=on_progress)
                assert res.isError and json.loads(res.content[0].text)["status"] == "pending"
    asyncio.run(run())
    broker.close()
    assert beats and beats[0][1] == 12 and "等待使用者回答中" in beats[0][2]
