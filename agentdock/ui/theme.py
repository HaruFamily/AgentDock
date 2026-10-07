"""Two looks for the whole app: a default clean look and optional local customization.

`apply(name)` switches the module-level colour names (painted widgets read them at paint time) and returns
the Qt stylesheet; `t(key)` gives the wording for the current look (cute vs plain).
"""
from __future__ import annotations

THEMES: dict[str, dict] = {'clean': {'label': '極簡乾淨（黑綠）',
           'ACCENT': '#a3ff12',
           'ACCENT_DEEP': '#b8ff50',
           'ACCENT_SOFT': '#203019',
           'LAVENDER': '#171d16',
           'ALERT': '#ffca57',
           'INK': '#e6eee1',
           'MUTED': '#a0ad98',
           'LINE': '#35432e',
           'CANVAS': '#090c09',
           'CARD': '#131913',
           'DANGER': '#ff7b87',
           'OK': '#a3ff12',
           'HOVER': '#1b2717',
           'DIM': '#65715e',
           'ERR_BG': '#331b22',
           'WARN_BG': '#302817',
           'WARN_INK': '#ffca57',
           'GOLD': '#a3ff12',
           'HEADER_A': '#131913',
           'HEADER_B': '#131913',
           'RADIUS': 12,
           'PILL': 7,
           'CUTE': False,
           'FONT': '"Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC", "Segoe UI", '
                   'sans-serif'}}

NAME = "clean"
# module-level names other modules read (updated by apply)
ACCENT = ACCENT_DEEP = ACCENT_SOFT = LAVENDER = ALERT = INK = MUTED = LINE = CANVAS = CARD = DANGER = OK = ""
HOVER = DIM = ERR_BG = WARN_BG = WARN_INK = GOLD = HEADER_A = HEADER_B = FONT_FAMILY = ""
CUTE = True
STYLE = ""

WORDS = {
    # key: (cute look, clean look) — both plain and easy to read; the cute look only adds small symbols
    "submit": ("送出", "提交"),
    "submit_all": ("全部送出", "全部提交"),
    "cancel_q": ("取消問題", "取消問題"),
    "draft": ("草稿會自動保存", "草稿會自動保存"),
    "tab_quota": ("額度", "額度"),
    "tab_qa": ("AgentChat", "AgentChat"),
    "tab_settings": ("設定", "設定"),
    "qa_waiting": ("{source} 的問題", "{source} 的問題"),
    "qa_none": ("沒有待回答的問題", "沒有待回答的問題"),
    "quota_ok": ("", ""),
    "quota_warn": ("用量偏高", "用量偏高"),
    "quota_bad": ("快用完了", "快用完了"),
    "reset_in": ("{t} 後重置", "{t} 後重置"),
    "updating": ("更新中…", "更新中…"),
    "updated": ("{t} 更新", "{t} 更新"),
    "no_quota": ("按 ⟳ 取得額度", "按 ⟳ 取得額度"),
    "bubble_more": ("展開完整回答", "展開完整回答"),
    "sent": ("已送出", "已送出"),
}


PRIVATE = None


def load_private(directory):
    """Load optional executable theme code from this device's ignored data directory."""
    global PRIVATE
    import importlib.util
    THEMES.pop("princess", None)
    PRIVATE = None
    path = directory / "private_theme.py"
    if not path.is_file():
        return False
    try:
        spec = importlib.util.spec_from_file_location("agentdock_private_theme", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import platform
        if getattr(module, "DEVICE", platform.node()) != platform.node():
            return False
        config = dict(module.CONFIG)
        # Validate before exposing it in the appearance menu.
        _build(config)
        for hook in ("draw_frame", "draw_icon", "draw_bar"):
            if not callable(getattr(module, hook, None)):
                raise ValueError("Missing renderer")
        THEMES["princess"] = config
        PRIVATE = module
        return True
    except Exception:
        import logging
        logging.exception("Could not load local appearance")
        return False


def apply(name: str) -> str:
    global NAME, STYLE, CUTE, FONT_FAMILY
    c = THEMES.get(name) or THEMES["clean"]
    NAME = name if name in THEMES else "clean"
    g = globals()
    for k, v in c.items():
        if k.isupper():
            g[k] = v
    FONT_FAMILY = c["FONT"]
    CUTE = c["CUTE"]
    STYLE = _build(c)
    return STYLE


def t(key: str, **kw) -> str:
    cute, plain = WORDS[key]
    return (cute if CUTE else plain).format(**kw)


def _build(c: dict) -> str:
    ACCENT, ACCENT_DEEP, ACCENT_SOFT, LAVENDER, ALERT, INK, MUTED, LINE, CANVAS, CARD, DANGER, OK, HOVER, DIM, ERR_BG, WARN_BG, WARN_INK, FONT = (
        c[k] for k in ("ACCENT", "ACCENT_DEEP", "ACCENT_SOFT", "LAVENDER", "ALERT", "INK", "MUTED", "LINE", "CANVAS", "CARD", "DANGER",
                       "OK", "HOVER", "DIM", "ERR_BG", "WARN_BG", "WARN_INK", "FONT"))
    HEADER_A, HEADER_B, RADIUS, PILL = c["HEADER_A"], c["HEADER_B"], c["RADIUS"], c["PILL"]
    primary_start = ACCENT
    primary_end = ACCENT_DEEP if c["CUTE"] else ACCENT
    primary_ink = "white" if c["CUTE"] else CANVAS
    primary_border = f"1px solid {ACCENT_DEEP}" if c["CUTE"] else "none"
    option_bg = CARD if c["CUTE"] else "transparent"
    option_border = LINE if c["CUTE"] else "transparent"
    option_radius = 12 if c["CUTE"] else 8
    option_selected = ACCENT_SOFT if c["CUTE"] else CARD
    return f"""
* {{ font-family: {FONT}; font-size: 13px; color: {INK}; }}
#PanelFrame {{ background: {CANVAS}; border: 2px solid {LINE}; border-radius: 18px; }}
#Header {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {HEADER_A}, stop:1 {HEADER_B}); border-top-left-radius: 16px; border-top-right-radius: 16px; border-bottom: 1px solid {LINE}; }}
#Title {{ font-size: 15px; font-weight: 700; color: {ACCENT_DEEP}; }}
#Muted, QLabel[muted="true"] {{ color: {MUTED}; font-size: 12px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 7px 12px; margin: 0 2px; border-bottom: 2px solid transparent; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {ACCENT_DEEP}; border-bottom: 2px solid {ACCENT}; font-weight: 700; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: {PILL}px; padding: 6px 14px; color: {INK}; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DIM}; }}
QPushButton[primary="true"] {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {primary_start}, stop:1 {primary_end}); color: {primary_ink}; border: {primary_border}; font-weight: 700; }}
QPushButton[primary="true"]:disabled {{ background: {DIM}; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}
QPushButton[flat="true"] {{ border: none; background: transparent; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ background: {ACCENT_SOFT}; }}
QPushButton[seg="true"] {{ border-radius: 14px; padding: 4px 14px; }}
QPushButton[seg="true"]:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 5px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QFrame[card="true"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; }}
QFrame[card="true"][selected="true"] {{ border: 1px solid {ACCENT}; background: {HOVER}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 12px; margin: 3px 2px; padding: 8px; }}
QListWidget::item:selected {{ border-color: {ACCENT}; background: {HOVER}; color: {INK}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QLabel#Chip {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#WarnChip {{ background: {WARN_BG}; color: {WARN_INK}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#ErrChip {{ background: {ERR_BG}; color: {DANGER}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#OkChip {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#Question {{ font-size: 16px; font-weight: 700; color: {ACCENT_DEEP}; }}
QLabel#Notice {{ background: {ERR_BG}; color: {DANGER}; border-radius: 7px; padding: 6px 9px; }}
QGroupBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 14px; margin-top: 18px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {ACCENT_DEEP}; font-weight: 700; }}
QPushButton[glyph="true"] {{ border: none; background: transparent; color: {MUTED}; font-size: 16px; padding: 0 6px; min-width: 22px; min-height: 22px; border-radius: 11px; }}
QPushButton[glyph="true"]:hover {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QPushButton[glyph="true"][danger="true"] {{ color: {DIM}; }}
QPushButton[glyph="true"][danger="true"]:hover {{ background: {ERR_BG}; color: {DANGER}; }}
QPushButton[glyph="true"]:disabled {{ color: {DIM}; }}
QPushButton[glyph="true"][small="true"] {{ font-size: 12px; color: {ACCENT}; }}
QLabel#Badge {{ color: {MUTED}; font-size: 12px; }}
QLabel#Badge[alert="true"] {{ color: {ALERT}; font-weight: 700; }}
QLabel#GroupLabel {{ color: {MUTED}; font-size: 11px; padding: 6px 4px 1px 4px; }}
QFrame#DropMarker {{ background: {ACCENT}; border: none; }}
QPushButton[textTab="true"] {{ border: none; background: transparent; padding: 4px 0; color: {MUTED}; font-size: 13px; border-radius: 0; border-bottom: 2px solid transparent; }}
QPushButton[textTab="true"]:checked {{ color: {ACCENT_DEEP}; font-weight: 700; border-bottom: 2px solid {ACCENT}; }}
QLabel#FieldLabel {{ color: {MUTED}; font-size: 12px; font-weight: 600; padding-top: 4px; }}
QLabel#Waiting {{ color: {MUTED}; font-size: 12px; }}
QLabel#Waiting[live="true"] {{ color: {OK}; }}
QWidget#OptionRow {{ background: {option_bg}; border: 1px solid {option_border}; border-radius: {option_radius}px; }}
QWidget#OptionRow:hover {{ background: {HOVER}; }}
QWidget#OptionRow[selected="true"] {{ background: {option_selected}; border: 1px solid {ACCENT}; }}
QPlainTextEdit#Grow {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 3px 6px; }}
QPlainTextEdit#Grow:focus {{ border-color: {ACCENT}; }}
QLabel#Thumb {{ border: 1px solid {LINE}; border-radius: 8px; padding: 2px; background: {CARD}; }}
QPushButton#RestartTag {{ background: {WARN_BG}; color: {WARN_INK}; border: none; border-radius: 8px; padding: 1px 7px; font-size: 11px; }}
QPushButton#RestartTag:hover {{ background: {WARN_BG}; }}
QLabel#Meta {{ color: {MUTED}; font-size: 12px; }}
QLabel#Dot {{ color: {ALERT}; font-size: 10px; }}
QLabel#Arrow {{ color: {MUTED}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 13px; font-weight: 700; color: {ACCENT_DEEP}; letter-spacing: 1px; }}
QLabel#GroupTitle {{ font-size: 14px; font-weight: 600; }}
QFrame#Rule {{ background: {LINE}; border: none; }}
QWidget#Row {{ border-radius: 10px; }}
QWidget#Row:hover {{ background: {HOVER}; }}
QLabel#RowName {{ font-size: 13px; }}
QLabel#RowNameDim {{ font-size: 13px; color: {DIM}; }}
QLabel#Toast {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; padding: 7px 14px; font-size: 12px; }}
QLabel#Toast[error="true"] {{ background: {ERR_BG}; color: {DANGER}; }}
QFrame#ActionBar {{ background: {CARD}; border-top: 1px solid {LINE}; border-bottom-left-radius: 16px; border-bottom-right-radius: 16px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {LINE}; background: {CARD}; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; height: 0; }}
QMenu {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 5px 18px 5px 14px; border-radius: 7px; color: {INK}; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT_DEEP}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}
QToolTip {{ background: {CARD}; color: {INK}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px 8px; }}
QProgressBar {{ background: {ACCENT_SOFT}; border: none; border-radius: 4px; }}
"""


apply("clean")
