import base64
import pytest
from agentdock.qa.store import QuestionStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20
URL = "data:image/png;base64," + base64.b64encode(PNG).decode()
O = "a" * 64


def ask(**kw):
    base = {"request_key": "k1", "work_id": "w", "work_title": "Work", "question": "Q?", "images": [], "mode": "single",
            "options": [{"id": "a", "label": "A", "description": "", "images": []}, {"id": "b", "label": "B", "description": "", "images": []}], "wait_seconds": 60}
    base.update(kw)
    return base


def test_create_idempotent_and_conflict(tmp_path):
    s = QuestionStore(tmp_path)
    q1 = s.create(O, "Codex", ask(images=[{"name": "x.png", "data_url": URL, "caption": "c"}]))
    assert s.create(O, "Codex", ask(images=[{"name": "x.png", "data_url": URL, "caption": "c"}]))["id"] == q1["id"]
    with pytest.raises(ValueError):
        s.create(O, "Codex", ask(question="other"))
    assert s.create("b" * 64, "Codex", ask())["id"] != q1["id"]
    meta, data = s.asset(q1["id"], q1["images"][0]["id"])
    assert data == PNG and meta["mime"] == "image/png"


def test_answer_rules_and_persistence(tmp_path):
    s = QuestionStore(tmp_path)
    q = s.create(O, "Codex", ask())
    with pytest.raises(ValueError):
        s.answer(q["id"], {"selected": ["a", "b"], "text": "", "notes": {}, "attachment_ids": []})
    with pytest.raises(ValueError):
        s.answer(q["id"], {"selected": [], "text": " ", "notes": {}, "attachment_ids": []})
    s.save_draft(q["id"], {"selected": ["a"], "text": "draft", "notes": {"a": "n"}, "attachment_ids": []})
    up = s.add_upload(q["id"], "f.txt", b"hello")
    assert up["mime"] == "text/plain"
    done = s.answer(q["id"], {"selected": ["a"], "text": "", "notes": {"a": "n"}, "attachment_ids": [up["id"]]})
    assert done["status"] == "answered"
    with pytest.raises(ValueError):
        s.cancel(q["id"])
    again = QuestionStore(tmp_path).get(q["id"])
    assert again["answer"]["selected"] == ["a"]
    assert s.asset(q["id"], up["id"], O, answers_only=True)[1] == b"hello"
    with pytest.raises(KeyError):
        s.get(q["id"], "c" * 64)


def test_cancel_is_not_consent(tmp_path):
    s = QuestionStore(tmp_path)
    q = s.create(O, "Codex", ask(mode="text", options=[]))
    c = s.cancel(q["id"])
    assert c["status"] == "cancelled" and "answer" not in c


def test_group_answer_is_all_or_nothing(tmp_path):
    s = QuestionStore(tmp_path)
    base = {"request_key": "g", "work_id": "w", "work_title": "W", "question": "intro"}
    items = [{"question": "Q1", "images": [], "mode": "text", "options": []},
             {"question": "Q2", "images": [], "mode": "single",
              "options": [{"id": "a", "label": "A", "description": "", "images": []}, {"id": "b", "label": "B", "description": "", "images": []}]}]
    g = s.create_group(O, "C", base, items)
    assert [q["group"]["index"] for q in g] == [0, 1] and g[0]["group"]["id"] == g[1]["group"]["id"]
    assert [q["id"] for q in s.create_group(O, "C", base, items)] == [q["id"] for q in g]   # idempotent
    empty = {"selected": [], "text": "", "notes": {}, "attachment_ids": []}
    with pytest.raises(ValueError, match="第 2 題"):
        s.answer_group({g[0]["id"]: {**empty, "text": "x"}, g[1]["id"]: empty})
    assert all(q["status"] == "pending" for q in s.group_members(g[0]["id"]))    # nothing committed
    s.answer_group({g[0]["id"]: {**empty, "text": "x"}, g[1]["id"]: {**empty, "selected": ["b"]}})
    assert [q["status"] for q in s.group_members(g[1]["id"])] == ["answered", "answered"]
