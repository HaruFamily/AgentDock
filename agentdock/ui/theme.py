from __future__ import annotations

ACCENT = "#2f5fd0"
ACCENT_SOFT = "#e7eefc"
ALERT = "#e08a1e"
INK = "#1d2635"
MUTED = "#6b7688"
LINE = "#dfe5ee"
CANVAS = "#f5f7fb"
CARD = "#ffffff"
DANGER = "#c4423a"
OK = "#1f8a6e"

FONT_FAMILY = '"Microsoft JhengHei UI", "Microsoft JhengHei", "Noto Sans CJK TC", "Segoe UI", sans-serif'

STYLE = f"""
* {{ font-family: {FONT_FAMILY}; font-size: 13px; color: {INK}; }}
#PanelFrame {{ background: {CANVAS}; border: 1px solid {LINE}; border-radius: 14px; }}
#Header {{ background: {CARD}; border-top-left-radius: 14px; border-top-right-radius: 14px; border-bottom: 1px solid {LINE}; }}
#Title {{ font-size: 15px; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {MUTED}; font-size: 12px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 7px 12px; margin: 0 2px; border-bottom: 2px solid transparent; color: {MUTED}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QPushButton {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #a9b1bf; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: white; border: none; }}
QPushButton[primary="true"]:disabled {{ background: #9fb3e6; }}
QPushButton[danger="true"] {{ color: {DANGER}; }}
QPushButton[flat="true"] {{ border: none; background: transparent; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ background: {ACCENT_SOFT}; }}
QPushButton[seg="true"] {{ border-radius: 14px; padding: 4px 14px; }}
QPushButton[seg="true"]:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QLineEdit, QPlainTextEdit, QComboBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 5px 7px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QFrame[card="true"] {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame[card="true"][selected="true"] {{ border: 1px solid {ACCENT}; background: #f7f9ff; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 9px; margin: 3px 2px; padding: 8px; }}
QListWidget::item:selected {{ border-color: {ACCENT}; background: #f7f9ff; color: {INK}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QLabel#Chip {{ background: {ACCENT_SOFT}; color: {ACCENT}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#WarnChip {{ background: #fdf0de; color: #9a5a00; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#ErrChip {{ background: #fbe7e5; color: {DANGER}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#OkChip {{ background: #e2f4ee; color: {OK}; border-radius: 9px; padding: 2px 8px; font-size: 11px; }}
QLabel#Question {{ font-size: 16px; font-weight: 600; }}
QLabel#Notice {{ background: #fbe7e5; color: {DANGER}; border-radius: 7px; padding: 6px 9px; }}
QGroupBox {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 10px; margin-top: 18px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {INK}; font-weight: 600; }}
QPushButton[glyph="true"] {{ border: none; background: transparent; color: {MUTED}; font-size: 16px; padding: 0 6px; min-width: 22px; min-height: 22px; border-radius: 6px; }}
QPushButton[glyph="true"]:hover {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QPushButton[glyph="true"][danger="true"] {{ color: #b8bfcb; }}
QPushButton[glyph="true"][danger="true"]:hover {{ background: #fbe7e5; color: {DANGER}; }}
QPushButton[glyph="true"]:disabled {{ color: #d0d5dd; }}
QPushButton[glyph="true"][small="true"] {{ font-size: 12px; color: {ACCENT}; }}
QLabel#Badge {{ color: {MUTED}; font-size: 12px; }}
QLabel#Badge[alert="true"] {{ color: {ALERT}; font-weight: 700; }}
QLabel#GroupLabel {{ color: {MUTED}; font-size: 11px; padding: 6px 4px 1px 4px; }}
QFrame#DropMarker {{ background: {ACCENT}; border: none; }}
QPushButton[textTab="true"] {{ border: none; background: transparent; padding: 4px 0; color: {MUTED}; font-size: 13px; border-radius: 0; border-bottom: 2px solid transparent; }}
QPushButton[textTab="true"]:checked {{ color: {INK}; font-weight: 600; border-bottom: 2px solid {ACCENT}; }}
QLabel#FieldLabel {{ color: {MUTED}; font-size: 12px; font-weight: 600; padding-top: 4px; }}
QLabel#Waiting {{ color: {MUTED}; font-size: 12px; }}
QLabel#Waiting[live="true"] {{ color: {OK}; }}
QWidget#OptionRow {{ border: 1px solid transparent; border-radius: 8px; }}
QWidget#OptionRow:hover {{ background: #eef2f9; }}
QWidget#OptionRow[selected="true"] {{ background: {CARD}; border: 1px solid {ACCENT}; }}
QPlainTextEdit#Grow {{ background: {CARD}; border: 1px solid {LINE}; border-radius: 7px; padding: 3px 6px; }}
QPlainTextEdit#Grow:focus {{ border-color: {ACCENT}; }}
QLabel#Thumb {{ border: 1px solid {LINE}; border-radius: 6px; padding: 2px; background: {CARD}; }}
QPushButton#RestartTag {{ background: #fdf0de; color: #9a5a00; border: none; border-radius: 8px; padding: 1px 7px; font-size: 11px; }}
QPushButton#RestartTag:hover {{ background: #fbe2c0; }}
QLabel#Meta {{ color: {MUTED}; font-size: 12px; }}
QLabel#Dot {{ color: {ALERT}; font-size: 10px; }}
QLabel#Arrow {{ color: {MUTED}; font-size: 13px; }}
QLabel#SectionTitle {{ font-size: 13px; font-weight: 700; color: {MUTED}; letter-spacing: 1px; }}
QLabel#GroupTitle {{ font-size: 14px; font-weight: 600; }}
QFrame#Rule {{ background: {LINE}; border: none; }}
QWidget#Row {{ border-radius: 6px; }}
QWidget#Row:hover {{ background: #eef2f9; }}
QLabel#RowName {{ font-size: 13px; }}
QLabel#RowNameDim {{ font-size: 13px; color: #9aa3b2; }}
QLabel#Toast {{ background: {ACCENT_SOFT}; color: {ACCENT}; padding: 7px 14px; font-size: 12px; }}
QLabel#Toast[error="true"] {{ background: #fbe7e5; color: {DANGER}; }}
QFrame#ActionBar {{ background: {CARD}; border-top: 1px solid {LINE}; border-bottom-left-radius: 14px; border-bottom-right-radius: 14px; }}
"""
