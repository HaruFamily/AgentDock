from agentdock.ui import theme


def test_local_theme_missing_invalid_and_removed(tmp_path):
    try:
        assert not theme.load_private(tmp_path)
        theme.apply("princess")  # A saved preference from another device.
        assert theme.NAME == "clean"
        assert list(theme.THEMES) == ["clean", "white_orange"]
        path = tmp_path / "private_theme.py"
        path.write_text("raise RuntimeError('broken')", encoding="utf-8")
        assert not theme.load_private(tmp_path)
        assert theme.PRIVATE is None
        config = dict(theme.THEMES["clean"], label="Local test", CUTE=True)
        path.write_text("CONFIG = " + repr(config) + "\n"
                        "def draw_frame(*args): pass\n"
                        "def draw_icon(*args): pass\n"
                        "def draw_bar(*args): pass\n", encoding="utf-8")
        assert theme.load_private(tmp_path)
        theme.apply("princess")
        assert theme.NAME == "princess"
        assert theme.THEMES["princess"]["label"] == "Local test"
        with path.open("a", encoding="utf-8") as f:
            f.write("\nDEVICE = 'a-different-device'\n")
        assert not theme.load_private(tmp_path)
        theme.apply("princess")
        assert theme.NAME == "clean"
        path.unlink()
        assert not theme.load_private(tmp_path)
        theme.apply("princess")
        assert theme.NAME == "clean"
        assert list(theme.THEMES) == ["clean", "white_orange"]
    finally:
        theme.THEMES.pop("princess", None)
        theme.PRIVATE = None
        theme.apply("clean")


def test_shortcuts_use_independent_registration_ids():
    from agentdock.ui.winutil import GlobalHotkey
    summon = GlobalHotkey(lambda: None)
    appearance = GlobalHotkey(lambda: None, hotkey_id=0xAD02)
    assert summon.HOTKEY_ID != appearance.HOTKEY_ID


def test_switch_to_clean_updates_all_surfaces_without_answering(tmp_path):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from agentdock.qa.store import QuestionStore
    from agentdock.ui.app import Dock, LauncherIcon
    from agentdock.ui.card import FloatingCard
    from agentdock.ui.qa_view import QaView
    app = QApplication.instance() or QApplication([])
    dock = Dock.__new__(Dock)
    dock.app = app
    dock.ball, dock.icon = FloatingCard(), LauncherIcon()
    dock.qa = QaView(QuestionStore(tmp_path))
    saved, icons, closed = [], [], []
    dock.ui = {}
    dock._use_font = lambda family: None
    dock._save_ui = lambda: saved.append(True)
    dock.tray = SimpleNamespace(setIcon=icons.append)
    dock.ball.bubble = SimpleNamespace(close=lambda: closed.append("card"))
    dock.icon.bubble = SimpleNamespace(close=lambda: closed.append("icon"))
    dock.apply_theme("clean")
    assert dock.ui["theme"] == "clean" and saved == [True]
    assert len(icons) == 1 and not icons[0].isNull()
    assert closed == ["card", "icon"]
    dock.ball.bubble = dock.icon.bubble = None
    dock.ball.close()
    dock.icon.close()
    dock.qa.close()
