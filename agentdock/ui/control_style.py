"""Semantic controls shared by application, chat and independent Git windows."""


def control_style(c: dict) -> str:
    ink, muted, line, card, canvas, accent, hover, soft, danger, dim = (
        c[k] for k in ('INK', 'MUTED', 'LINE', 'CARD', 'CANVAS', 'ACCENT', 'HOVER', 'ACCENT_SOFT', 'DANGER', 'DIM'))
    return f"""
QPushButton[flat="true"] {{ background: {card}; border: 1px solid {line}; padding: 4px 10px; }}
QPushButton[flat="true"]:hover {{ background: {hover}; border-color: {accent}; }}
QPushButton:focus {{ border-color: {accent}; }}
QPushButton:checked {{ background: {soft}; border-color: {accent}; }}
QPushButton:disabled {{ color: {dim}; border-color: {line}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton#MiniButton {{ border: 1px solid {line}; background: {card}; color: {ink}; padding: 2px 8px; }}
QPushButton#MiniButton:focus, QPushButton#IconButton:focus, QPushButton#RowMore:focus,
QPushButton#Chip:focus {{ border: 1px solid {accent}; }}
QPushButton#Chip {{ border: 1px solid {line}; background: {card}; color: {ink}; }}
QPushButton#ChatChip {{ border: 1px solid {line}; background: {card}; color: {ink}; }}
QLineEdit[readOnly="true"] {{ background: {canvas}; color: {muted}; border: 1px solid {line}; }}
QLineEdit[readOnly="true"]:focus {{ border: 1px dashed {accent}; }}
QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#SectionLabel, QLabel#FieldLabel {{ color: {muted}; font-size: 12px; font-weight: 600; }}
"""
