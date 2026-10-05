import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QProgressBar

from agentdock.tools import discover
from agentdock.tools.tokengauge import QuotaModel, gauge, migrate

app = QApplication.instance() or QApplication([])


def test_gauge_parsers_selftest():
    gauge.selftest()


def test_latest_roundtrip(tmp_path):
    gauge.configure(tmp_path)
    gauge.save_latest(gauge.demo_results(), 123.0)
    at, results = gauge.load_latest()
    assert at == 123.0 and [r.key for r in results] == ["claude", "codex", "grok"]
    assert results[1].accounts[1].label == "公司" and results[1].accounts[1].windows[0].used == 93


def test_migrate_copies_settings_and_stops_old_autostart(tmp_path, monkeypatch):
    appdata = tmp_path / "Roaming"
    old = appdata / "TokenGauge"
    (old / "codex_accounts").mkdir(parents=True)
    (old / "config.json").write_text(json.dumps({"theme": "dark"}), "utf-8")
    (old / "codex_accounts" / "acc1.json").write_text("{}", "utf-8")
    startup = appdata / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup.mkdir(parents=True)
    (startup / "TokenGauge.cmd").write_text('@start "" "C:\\py\\pythonw.exe" "D:\\Tools\\tokengauge.pyw"\r\n', "utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    new = tmp_path / "data" / "tokengauge"
    notes = migrate(new)
    assert len(notes) == 2 and json.loads((new / "config.json").read_text("utf-8"))["theme"] == "dark"
    assert (new / "codex_accounts" / "acc1.json").exists() and not (startup / "TokenGauge.cmd").exists()
    assert (old / "config.json").exists()  # the original is left alone
    assert migrate(new) == []


def _wait(model):
    import time
    for _ in range(100):
        app.processEvents()
        if not model.busy:
            return
        time.sleep(0.03)


def test_quota_model_and_floating_card(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(gauge, "collect_all", lambda cfg: gauge.demo_results())
    from PySide6.QtWidgets import QWidget
    from agentdock.ui import theme
    from agentdock.ui.card import Bar, Clickable, FloatingCard
    model = QuotaModel(tmp_path / "data")
    card = FloatingCard()
    card.set_qa(QWidget())
    card.set_settings(QWidget())
    card.set_quota(model)          # expanded card = active = collects when there is no data yet
    _wait(model)
    assert set(model.results) == {"claude", "codex", "grok"} and gauge.load_latest()[1]
    app.processEvents()
    assert len(card.quota_body.findChildren(Bar)) == 7            # every window (5H and W) of every account
    # two sizes: card <-> icon; the icon rests (no quota requests)
    card.set_mode("heart")
    assert not model.active and card.width() < 100
    card.set_mode("card")
    assert model.active
    card.resize(500, 450)                                          # resizable; the size is remembered
    card._remember_size()
    card.set_mode("heart"); card.set_mode("card")
    assert card.width() == 500 and card.height() == 450
    # a simple single choice while it is an icon -> bubble; answering it emits the choice
    card.set_mode("heart")
    q = {"id": "q1", "source": "Codex", "question": "要用哪個方案？", "mode": "single",
         "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
    got = []
    card.bubble_answer.connect(lambda qid, oid: got.append((qid, oid)))
    card.question_arrived(q)
    assert card.bubble is not None
    card.bubble.answered.emit("q1", "b")
    assert got == [("q1", "b")]
    # while resting for a full-screen app nothing pops up
    card.set_mode("card"); card.rest(True)
    card.question_arrived({**q, "id": "q2"})
    assert card.bubble is None and card.mode == "heart"
    card.rest(False)
    assert card.mode == "card"
    # a text question in card mode switches to 問答 and goes back after it is answered
    card.set_page("settings", by_user=True)
    card.question_arrived({**q, "mode": "text", "options": []})
    assert card.page == "qa" and card.return_page == "settings"
    card.set_agent_summary("4 個 Agent · 2 項可更新", True)
    assert card.tab_buttons["settings"].text().endswith("•")
    # choices from the old TokenGauge menu (hide a provider, show used) are undone: the card shows what is left for all
    cfg = gauge.load_config()
    cfg.update(show="used", providers={"claude": True, "codex": True, "grok": False})
    gauge.save_config(cfg)
    again = QuotaModel(tmp_path / "data")
    assert again.config()["show"] == "remaining"
    assert [r.key for r in again.visible_results()] == ["claude", "codex", "grok"]
    again.timer.stop()
    card.set_bg_opacity(0.1)                                         # background opacity is clamped so text stays readable
    assert card.bg_opacity == 0.3
    card.set_bg_opacity(0.7)
    card.grab()                                                    # paints with the translucent background
    assert card.bg_opacity == 0.7
    theme.apply("clean")
    card.restyle()
    assert card.tab_buttons["quota"].text() == "額度"
    theme.apply("princess")
    assert not any(t.title == "額度" for t in discover())  # quota is on the card, not a tab
