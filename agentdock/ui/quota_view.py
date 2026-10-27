"""Quota: a full list for the Tools page and an optional small always-on-top float."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from agentdock.ui import theme
from agentdock.ui.card import Bar, card_style


class QuotaView(QWidget):
    """Remaining quota per provider and window. `compact`: one line per window, no errors or plans."""

    def __init__(self, model, compact: bool = False, columns: int = 1, show_head: bool = True):
        super().__init__()
        self.model, self.compact, self.columns = model, compact, columns
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        head = QHBoxLayout()
        self.head = head
        self.updated = QLabel('')
        self.updated.setObjectName('CardMeta')
        head.addWidget(self.updated, 1)
        self.refresh_button = QPushButton('⟳')
        self.refresh_button.setObjectName('CardIcon')
        self.refresh_button.setToolTip('立即更新額度')
        self.refresh_button.clicked.connect(lambda: self.model and self.model.collect())
        head.addWidget(self.refresh_button)
        if show_head:
            layout.addLayout(head)
        self.body = QWidget()
        if columns > 1:
            # Cards in a grid; each provider is one card.
            self.rows = QGridLayout(self.body)
            self.rows.setContentsMargins(0, 0, 0, 0)
            self.rows.setHorizontalSpacing(14)
            self.rows.setVerticalSpacing(14)
            for c in range(columns):
                self.rows.setColumnStretch(c, 1)
        else:
            self.rows = QVBoxLayout(self.body)
            self.rows.setContentsMargins(0, 0, 0, 0)
            self.rows.setSpacing(2)
        if compact or columns > 1:
            layout.addWidget(self.body)   # the page around a card grid scrolls instead
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
        results = q.visible_results()
        for n, r in enumerate(results):
            if self.columns > 1:
                self.rows.addWidget(self._provider(r, gauge), n // self.columns, n % self.columns,
                                    Qt.AlignmentFlag.AlignTop)
            else:
                self.rows.addWidget(self._provider(r, gauge))
        if self.columns > 1:
            self.rows.setRowStretch((len(results) + self.columns - 1) // self.columns, 1)
        elif not self.compact:
            self.rows.addStretch()

    def _provider(self, r, gauge) -> QWidget:
        box = QFrame()
        v = QVBoxLayout(box)
        if self.columns > 1:
            box.setObjectName('QuotaCard')
            box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            v.setContentsMargins(12, 10, 12, 12)
            v.setSpacing(4)
        else:
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
                kind.setFixedWidth(theme.px(22))
                pct = QLabel(f'{100 - used:.0f}%')
                pct.setObjectName('CardPct')
                pct.setFixedWidth(theme.px(40))
                pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                left = QLabel(gauge.fmt_countdown(w.resets_at))
                left.setObjectName('CardMeta')
                left.setFixedWidth(theme.px(54))
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
        self.layout_ = QVBoxLayout(self)
        self.view = QuotaView(model, compact=True)
        self.layout_.addWidget(self.view)
        self._drag: QPoint | None = None
        # Over the window being worked in it steps back (see-through) until the pointer comes to it.
        self._watch = QTimer(self, interval=500)
        self._watch.timeout.connect(self._step_back)
        self.restyle()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._watch.start()

    def hideEvent(self, event):  # noqa: N802
        super().hideEvent(event)
        self._watch.stop()

    def _step_back(self) -> None:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication
        active = QApplication.activeWindow()
        covering = (active is not None and active is not self
                    and active.frameGeometry().intersects(self.frameGeometry())
                    and not self.frameGeometry().contains(QCursor.pos()) and self._drag is None)
        opacity = 0.35 if covering else 1.0
        if abs(self.windowOpacity() - opacity) > 0.01:
            self.setWindowOpacity(opacity)

    def laced(self) -> bool:
        return theme.decorated('settings_card')

    def restyle(self) -> None:
        # The sweet look trims it like the settings cards; the frame's own box then steps aside.
        self.setStyleSheet('#PanelFrame { background: transparent; border: none; }' if self.laced() else '')
        self.layout_.setContentsMargins(*((24, 20, 24, 22) if self.laced() else (10, 6, 10, 8)))
        self.setFixedWidth(theme.px(280 if self.laced() else 260))
        self.view.restyle()
        self.adjustSize()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        if self.laced():
            from PySide6.QtCore import QRectF
            from PySide6.QtGui import QColor, QPainter, QPainterPath
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(self.rect()), 22, 22)
            painter.fillPath(shape, QColor(theme.CANVAS or '#ffffff'))   # nothing left unpainted shows black
            theme.decorate('settings_card', painter, QRectF(self.rect()).adjusted(0, -3, 0, 0), 22.0)
            painter.end()

    def resizeEvent(self, event):  # noqa: N802
        # Its own window: without this the corners outside the rounded frame stay as a square box.
        from agentdock.ui.cute import round_mask
        round_mask(self, 22 if self.laced() else 18)
        super().resizeEvent(event)

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        centre = QHBoxLayout(body)
        centre.setContentsMargins(32, 24, 32, 24)
        centre.addStretch(1)
        column = QWidget()
        column.setMaximumWidth(960)
        centre.addWidget(column, 100)
        centre.addStretch(1)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.quota = QuotaView(model, columns=2, show_head=False)
        head = QHBoxLayout()
        head.setSpacing(10)
        title = QLabel('額度')
        title.setObjectName('PageTitle')
        head.addWidget(title)
        head.addWidget(self.quota.updated, 0, Qt.AlignmentFlag.AlignBottom)
        head.addWidget(self.quota.refresh_button, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.float_switch = QCheckBox('額度浮動視窗')
        self.float_switch.setChecked(float_on)
        self.float_switch.toggled.connect(self.float_toggled.emit)
        head.addWidget(self.float_switch, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(head)
        layout.addWidget(self.quota)
        for name, widget in extra:
            label = QLabel(name)
            label.setObjectName('PageTitle')
            layout.addSpacing(10)
            layout.addWidget(label)
            layout.addWidget(widget)
        layout.addStretch(1)
        scroll.setWidget(body)
        self.restyle()

    def restyle(self) -> None:
        # The update time and ⟳ moved here from the quota view; they keep its look.
        self.setStyleSheet(card_style())
        self.quota.restyle()
