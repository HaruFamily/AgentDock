"""Quota: a full list for the Tools page and an optional small always-on-top float."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from agentdock.ui import theme
from agentdock.ui.card import Bar, card_style


class QuotaView(QWidget):
    """Remaining quota per provider and window. `compact`: one line per window, no errors or plans."""

    def __init__(self, model, compact: bool = False):
        super().__init__()
        self.model, self.compact = model, compact
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head = QHBoxLayout()
        self.updated = QLabel('')
        self.updated.setObjectName('CardMeta')
        head.addWidget(self.updated, 1)
        self.refresh_button = QPushButton('⟳')
        self.refresh_button.setObjectName('CardIcon')
        self.refresh_button.setToolTip('立即更新額度')
        self.refresh_button.clicked.connect(lambda: self.model and self.model.collect())
        head.addWidget(self.refresh_button)
        layout.addLayout(head)
        self.body = QWidget()
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        if compact:
            layout.addWidget(self.body)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.body)
            layout.addWidget(scroll, 1)
        if model is not None:
            model.changed.connect(self.render)
        self.restyle()

    def restyle(self) -> None:
        self.setStyleSheet(card_style())
        self.render()

    def render(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        q = self.model
        if q is None:
            self.updated.setText(theme.t('no_quota'))
            return
        from agentdock.tools.tokengauge import gauge
        when = time.strftime('%H:%M', time.localtime(q.last)) if q.last else ''
        self.updated.setText(theme.t('updating') if q.busy else (theme.t('updated', t=when) if when else theme.t('no_quota')))
        for r in q.visible_results():
            self.rows.addWidget(self._provider(r, gauge))
        if not self.compact:
            self.rows.addStretch()

    def _provider(self, r, gauge) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 2 if self.compact else 6)
        v.setSpacing(1)
        head = QHBoxLayout()
        head.setContentsMargins(4, 2, 4, 1)
        name = QLabel(r.name)
        name.setObjectName('CardName')
        head.addWidget(name)
        if not self.compact:
            for plan in sorted({a.plan for a in r.accounts if a.plan})[:2]:
                chip = QLabel(plan)
                chip.setObjectName('CardPlan')
                head.addWidget(chip)
        head.addStretch()
        v.addLayout(head)
        if r.error and not self.compact:
            err = QLabel(('（上次的數字）' if r.stale else '') + r.error)
            err.setObjectName('CardErr')
            err.setWordWrap(True)
            v.addWidget(err)
        for a in r.accounts:
            if len(r.accounts) > 1 and a.label and not self.compact:
                sub = QLabel(a.label)
                sub.setObjectName('CardMeta')
                v.addWidget(sub)
            for w in a.windows:
                used = max(0.0, min(100.0, float(w.used)))
                row = QHBoxLayout()
                row.setContentsMargins(6, 0, 4, 0)
                row.setSpacing(6)
                kind = QLabel(gauge.KIND_LABEL.get(w.kind, w.kind))
                kind.setObjectName('CardKind')
                kind.setFixedWidth(22)
                pct = QLabel(f'{100 - used:.0f}%')
                pct.setObjectName('CardPct')
                pct.setFixedWidth(38)
                pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                left = QLabel(gauge.fmt_countdown(w.resets_at))
                left.setObjectName('CardMeta')
                left.setFixedWidth(50)
                left.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(kind)
                row.addWidget(Bar(100 - used, used), 1)
                row.addWidget(pct)
                row.addWidget(left)
                holder = QWidget()
                holder.setLayout(row)
                v.addWidget(holder)
        return box


class QuotaFloat(QFrame):
    """The only floating window left: small, always on top, dragged anywhere, never takes focus."""
    moved = Signal(QPoint)

    def __init__(self, model):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName('PanelFrame')
        self.setWindowTitle('AgentDock 額度')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        self.view = QuotaView(model, compact=True)
        layout.addWidget(self.view)
        self.setFixedWidth(260)
        self._drag: QPoint | None = None

    def restyle(self) -> None:
        self.view.restyle()
        self.adjustSize()

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.moved.emit(self.pos())


class ToolsPage(QWidget):
    """The window's Tools page: quota with the float switch, then any extra tool pages."""
    float_toggled = Signal(bool)

    def __init__(self, model, float_on: bool, extra: list[tuple[str, QWidget]]):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(12)
        head = QHBoxLayout()
        title = QLabel('額度')
        title.setObjectName('PageTitle')
        head.addWidget(title, 1)
        self.float_switch = QCheckBox('額度浮動視窗')
        self.float_switch.setChecked(float_on)
        self.float_switch.toggled.connect(self.float_toggled.emit)
        head.addWidget(self.float_switch)
        layout.addLayout(head)
        self.quota = QuotaView(model)
        self.quota.setMaximumWidth(760)
        layout.addWidget(self.quota, 1)
        for name, widget in extra:
            label = QLabel(name)
            label.setObjectName('PageTitle')
            layout.addWidget(label)
            layout.addWidget(widget, 1)

    def restyle(self) -> None:
        self.quota.restyle()
