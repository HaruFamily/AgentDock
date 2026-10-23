"""鬧鐘: a section of the Tools page. Alarms ring while AgentDock runs (also with the window closed, from the
tray): a window on top with 貪睡 / 關掉 and a looping alarm sound."""
from __future__ import annotations

import sys
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QSizePolicy, QTimeEdit, QVBoxLayout, QWidget)

from agentdock.tools import ToolSpec
from agentdock.tools.alarm.core import (DAYS, Alarm, AlarmStore, due, next_ring, rang, repeat_text, snoozed, sounds,
                                        until_text, when_text)

RING_FOR_MS = 5 * 60 * 1000   # the sound stops after five minutes; the window stays


class Bell(QObject):
    """The alarm sound, looping until stopped (Windows' own alarm sounds; a beep elsewhere)."""

    def __init__(self) -> None:
        super().__init__()
        self.ringing = False
        self._beep = QTimer(self, interval=1500)
        self._beep.timeout.connect(QApplication.beep)
        self._limit = QTimer(self, singleShot=True, interval=RING_FOR_MS)
        self._limit.timeout.connect(self.stop)

    @staticmethod
    def file_for(sound: str) -> str:
        if sound and sound != "-":
            return sound
        found = sounds()
        return found[0][1] if found else ""

    def play(self, sound: str, loop: bool = True) -> None:
        self.stop()
        if sound == "-":
            return
        self.ringing = True
        if sys.platform == "win32":
            import winsound
            path = self.file_for(sound)
            flags = winsound.SND_ASYNC | winsound.SND_NODEFAULT | (winsound.SND_LOOP if loop else 0)
            try:
                if path:
                    winsound.PlaySound(path, winsound.SND_FILENAME | flags)
                else:
                    winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | flags)
            except RuntimeError:
                QApplication.beep()
        elif loop:
            QApplication.beep()
            self._beep.start()
        else:
            QApplication.beep()
        if loop:
            self._limit.start()

    def stop(self) -> None:
        self._beep.stop()
        self._limit.stop()
        if self.ringing and sys.platform == "win32":
            import winsound
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except RuntimeError:
                pass
        self.ringing = False


class RingWindow(QDialog):
    """Rings on top of everything: 貪睡 5 分鐘 or 關掉 (Esc and the close button also turn it off)."""
    snoozed = Signal()

    def __init__(self, alarm: Alarm) -> None:
        super().__init__(None, Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("鬧鐘")
        self.setObjectName("AlarmRing")
        self.alarm_id = alarm.id
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        big = QLabel(alarm.clock())
        big.setObjectName("AlarmBig")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(big)
        if alarm.label:
            name = QLabel(alarm.label)
            name.setObjectName("AlarmLabel")
            name.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name.setWordWrap(True)
            lay.addWidget(name)
        lay.addSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.snooze_button = QPushButton("貪睡 5 分鐘")
        self.snooze_button.clicked.connect(self._snooze)
        self.off_button = QPushButton("關掉")
        self.off_button.setProperty("primary", True)
        self.off_button.setDefault(True)
        self.off_button.clicked.connect(self.reject)
        row.addWidget(self.snooze_button)
        row.addWidget(self.off_button)
        lay.addLayout(row)
        self.setMinimumWidth(320)

    def _snooze(self) -> None:
        self.snoozed.emit()
        self.done(2)


class AlarmEditor(QDialog):
    """New (alarm=None) or existing alarm: time, name, repeat days, sound."""

    def __init__(self, parent: QWidget | None, alarm: Alarm | None, bell: Bell) -> None:
        super().__init__(parent)
        self.setWindowTitle("編輯鬧鐘" if alarm else "新增鬧鐘")
        self.bell = bell
        self.deleted = False
        form = QFormLayout(self)
        form.setSpacing(10)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        self.time.setObjectName("AlarmTimeEdit")
        self.time.setButtonSymbols(QTimeEdit.ButtonSymbols.NoButtons)
        self.time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        now = datetime.now()
        self.time.setTime(QTime(alarm.hour, alarm.minute) if alarm else QTime(now.hour, now.minute).addSecs(3600))
        form.addRow("時間", self.time)
        self.label = QLineEdit(alarm.label if alarm else "")
        form.addRow("名稱", self.label)
        days = QHBoxLayout()
        days.setSpacing(4)
        self.days: list[QPushButton] = []
        for i, d in enumerate(DAYS):
            b = QPushButton(d)
            b.setCheckable(True)
            b.setProperty("dayChip", True)
            b.setChecked(bool(alarm and i in alarm.days))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            days.addWidget(b)
            self.days.append(b)
        days.addStretch(1)
        form.addRow("重複", days)
        sound_row = QHBoxLayout()
        self.sound = QComboBox()
        for text, path in sounds():
            self.sound.addItem(text, path)
        if self.sound.count() == 0:
            self.sound.addItem("提示音", "")
        self.sound.addItem("不出聲", "-")
        if alarm and alarm.sound:
            i = self.sound.findData(alarm.sound)
            self.sound.setCurrentIndex(i if i >= 0 else 0)
        self.listen = QPushButton("試聽")
        self.listen.setCheckable(True)
        self.listen.toggled.connect(self._listen)
        sound_row.addWidget(self.sound, 1)
        sound_row.addWidget(self.listen)
        form.addRow("聲音", sound_row)
        bottom = QHBoxLayout()
        if alarm:
            delete = QPushButton("刪除")
            delete.setProperty("danger", True)
            delete.clicked.connect(self._delete)
            bottom.addWidget(delete)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        form.addRow(bottom)
        self.finished.connect(lambda _r: self.bell.stop())

    def _listen(self, on: bool) -> None:
        if on:
            self.bell.play(self.sound.currentData() or "", loop=True)
            QTimer.singleShot(4000, self, lambda: self.listen.setChecked(False))
        else:
            self.bell.stop()

    def _delete(self) -> None:
        self.deleted = True
        self.accept()

    def values(self) -> dict:
        t = self.time.time()
        return {"hour": t.hour(), "minute": t.minute(), "label": self.label.text().strip(),
                "days": [i for i, b in enumerate(self.days) if b.isChecked()],
                "sound": "" if self.sound.currentIndex() == 0 and self.sound.currentData() != "-"
                else str(self.sound.currentData() or "")}


class AlarmRow(QWidget):
    clicked = Signal()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class AlarmView(QWidget):
    def __init__(self, store: AlarmStore, notify: Callable[[str], None] = lambda _t: None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        super().__init__()
        from agentdock.ui.settings_page import Card
        self.store, self.notify, self.clock = store, notify, clock
        self.bell = Bell()
        self.ringing: dict[str, RingWindow] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = Card()
        outer.addWidget(self.card)
        head = QHBoxLayout()
        self.add_button = QPushButton("＋")
        self.add_button.setProperty("glyph", True)
        self.add_button.setToolTip("新增鬧鐘")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(lambda: self.edit(None))
        head.addWidget(self.add_button)
        head.addStretch(1)
        self.card.lay.addLayout(head)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(2)
        self.card.lay.addLayout(self.rows)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self._minute = -1
        self.render()

    # -- list
    def render(self) -> None:
        from agentdock.ui.settings_page import Switch
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        now = self.clock()
        alarms = self.store.sorted()
        if not alarms:
            empty = QLabel("還沒有鬧鐘")
            empty.setObjectName("Meta")
            self.rows.addWidget(empty)
        for alarm in alarms:
            row = AlarmRow()
            row.setObjectName("AlarmRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.clicked.connect(lambda a=alarm: self.edit(a))
            lay = QHBoxLayout(row)
            lay.setContentsMargins(10, 6, 6, 6)
            text = QVBoxLayout()
            text.setSpacing(0)
            clock = QLabel(alarm.clock())
            clock.setObjectName("AlarmTime" if alarm.enabled else "AlarmTimeOff")
            text.addWidget(clock)
            parts = [alarm.label] if alarm.label else []
            parts.append(repeat_text(alarm.days))
            t = next_ring(alarm, now)
            if t:
                parts.append(("貪睡到 " if alarm.snooze else "") + f"{when_text(t, now)}（{until_text(t, now)}）")
            note = QLabel(" · ".join(parts))
            note.setObjectName("Meta")
            note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            text.addWidget(note)
            lay.addLayout(text, 1)
            switch = Switch(checkable=True)
            switch.setChecked(alarm.enabled)
            switch.toggled.connect(lambda on, a=alarm: self.set_enabled(a.id, on))
            lay.addWidget(switch)
            more = QPushButton("⋯")
            more.setProperty("glyph", True)
            more.setToolTip("更多")
            menu = QMenu(more)
            menu.addAction("編輯…", lambda a=alarm: self.edit(a))
            menu.addAction("刪除", lambda a=alarm: self.delete(a.id))
            more.clicked.connect(lambda _=False, m=menu, b=more: m.exec(b.mapToGlobal(b.rect().bottomLeft())))
            more._menu = menu
            lay.addWidget(more)
            self.rows.addWidget(row)

    def edit(self, alarm: Alarm | None) -> None:
        dialog = AlarmEditor(self.window(), alarm, self.bell)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.deleted and alarm:
            self.delete(alarm.id)
            return
        values = dialog.values()
        target = Alarm(**values) if alarm is None else alarm
        if alarm is not None:
            for k, v in values.items():
                setattr(target, k, v)
        target.enabled, target.snooze, target.armed = True, None, self.clock().timestamp()
        self.store.put(target)
        self.render()

    def set_enabled(self, aid: str, on: bool) -> None:
        alarm = self.store.get(aid)
        if alarm is None:
            return
        alarm.enabled, alarm.snooze, alarm.armed = on, None, self.clock().timestamp()
        self.store.put(alarm)
        self.render()

    def delete(self, aid: str) -> None:
        window = self.ringing.pop(aid, None)
        if window is not None:
            window.close()
        self.store.remove(aid)
        self.render()

    # -- ringing
    def tick(self) -> None:
        now = self.clock()
        changed = False
        for alarm in list(self.store.alarms):
            state = due(alarm, now)
            if state == "skip":
                rang(alarm, now)
                changed = True
            elif state == "ring" and alarm.id not in self.ringing:
                self.ring(alarm)
                rang(alarm, now)   # rings once; 貪睡 brings it back in five minutes
                changed = True
        if changed:
            self.store.save()
            self.render()
        elif now.minute != self._minute:
            self.render()   # "還有 …" stays current
        self._minute = now.minute

    def ring(self, alarm: Alarm) -> None:
        window = RingWindow(alarm)
        self.ringing[alarm.id] = window
        window.snoozed.connect(lambda a=alarm.id: self._snooze(a))
        window.finished.connect(lambda result, a=alarm.id: self._ended(a, result))
        self.bell.play(alarm.sound)
        self.notify(f"鬧鐘 {alarm.clock()}" + (f"：{alarm.label}" if alarm.label else ""))
        window.show()
        window.raise_()
        window.activateWindow()

    def _snooze(self, aid: str) -> None:
        alarm = self.store.get(aid)
        if alarm is not None:
            snoozed(alarm, self.clock())
            self.store.save()

    def _ended(self, aid: str, _result: int) -> None:
        self.ringing.pop(aid, None)
        if not self.ringing:
            self.bell.stop()
        self.render()


def create(ctx):
    return AlarmView(AlarmStore(ctx.data_dir / "alarms.json"), ctx.notify)


TOOL = ToolSpec(title="鬧鐘", create=create, order=20)
