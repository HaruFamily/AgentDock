"""Alarm clock: when each alarm rings next, how to say it, and where alarms are kept (data/alarms.json).

No Qt here. An alarm rings at its first time after `armed` (when it was set, last rang or was dismissed); a
time missed by more than LATE (the computer slept, AgentDock was closed) is skipped instead of ringing late.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

DAYS = "一二三四五六日"          # Monday = 0, like datetime.weekday()
LATE = timedelta(minutes=10)
SNOOZE = timedelta(minutes=5)


@dataclass
class Alarm:
    hour: int
    minute: int
    label: str = ""
    days: list[int] = field(default_factory=list)   # empty: rings once
    enabled: bool = True
    sound: str = ""                                 # a file, "" = the default alarm sound, "-" = silent
    armed: float = field(default_factory=time.time)
    snooze: float | None = None                     # ring again at this time
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @staticmethod
    def from_dict(d: dict) -> "Alarm":
        known = {k: d[k] for k in Alarm.__dataclass_fields__ if k in d}
        alarm = Alarm(**known)
        alarm.hour, alarm.minute = int(alarm.hour) % 24, int(alarm.minute) % 60
        alarm.days = sorted({int(x) for x in alarm.days if 0 <= int(x) <= 6})
        return alarm

    def clock(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def first_after(alarm: Alarm, after: datetime) -> datetime:
    """The first ring time strictly after `after` (snooze not counted)."""
    base = after.replace(hour=alarm.hour, minute=alarm.minute, second=0, microsecond=0)
    for d in range(8):
        t = base + timedelta(days=d)
        if t > after and (not alarm.days or t.weekday() in alarm.days):
            return t
    return base + timedelta(days=8)   # not reached: every week has each weekday


def next_ring(alarm: Alarm, now: datetime | None = None) -> datetime | None:
    if not alarm.enabled:
        return None
    if alarm.snooze:
        return datetime.fromtimestamp(alarm.snooze)
    return first_after(alarm, max(datetime.fromtimestamp(alarm.armed), (now or datetime.now()) - LATE))


def due(alarm: Alarm, now: datetime) -> str:
    """'ring', 'skip' (missed long ago: re-arm quietly) or '' (not yet)."""
    if not alarm.enabled:
        return ""
    if alarm.snooze:
        t = datetime.fromtimestamp(alarm.snooze)
        return "" if t > now else ("ring" if now - t <= LATE else "skip")
    t = first_after(alarm, datetime.fromtimestamp(alarm.armed))
    if t > now:
        return ""
    return "ring" if now - t <= LATE else "skip"


def rang(alarm: Alarm, now: datetime) -> None:
    """After ringing (dismissed) or a skipped time: wait for the next one; a one-time alarm turns off."""
    alarm.snooze = None
    alarm.armed = now.timestamp()
    if not alarm.days:
        alarm.enabled = False


def snoozed(alarm: Alarm, now: datetime) -> None:
    alarm.enabled = True   # a one-time alarm comes back for the snooze
    alarm.snooze = (now + SNOOZE).timestamp()
    alarm.armed = now.timestamp()


def repeat_text(days: list[int]) -> str:
    days = sorted(set(days))
    if not days:
        return "只響一次"
    if len(days) == 7:
        return "每天"
    if days == [0, 1, 2, 3, 4]:
        return "週一到週五"
    if days == [5, 6]:
        return "週末"
    return "週" + "、".join(DAYS[d] for d in days)


def until_text(t: datetime, now: datetime) -> str:
    seconds = int((t - now).total_seconds())
    if seconds < 60:
        return "不到 1 分鐘後"
    minutes = (seconds + 59) // 60
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    parts = ([f"{days} 天"] if days else []) + ([f"{hours} 小時"] if hours else []) + \
        ([f"{minutes} 分"] if minutes and not days else [])
    return " ".join(parts) + "後"


def when_text(t: datetime, now: datetime) -> str:
    """'今天 07:30' / '明天 07:30' / '週三 07:30'."""
    days = (t.date() - now.date()).days
    head = "今天" if days == 0 else "明天" if days == 1 else "後天" if days == 2 else f"週{DAYS[t.weekday()]}"
    return f"{head} {t:%H:%M}"


class AlarmStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.alarms: list[Alarm] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.alarms = [Alarm.from_dict(d) for d in data.get("alarms", []) if isinstance(d, dict)]
        except (OSError, ValueError, TypeError):
            self.alarms = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"alarms": [asdict(a) for a in self.alarms]}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def sorted(self) -> list[Alarm]:
        return sorted(self.alarms, key=lambda a: (a.hour, a.minute, a.label))

    def get(self, aid: str) -> Alarm | None:
        return next((a for a in self.alarms if a.id == aid), None)

    def put(self, alarm: Alarm) -> None:
        self.alarms = [a for a in self.alarms if a.id != alarm.id] + [alarm]
        self.save()

    def remove(self, aid: str) -> None:
        self.alarms = [a for a in self.alarms if a.id != aid]
        self.save()


def sounds() -> list[tuple[str, str]]:
    """(label, file) of the alarm sounds Windows ships (C:\\Windows\\Media\\Alarm01.wav …)."""
    media = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"
    out = []
    for i in range(1, 11):
        f = media / f"Alarm{i:02d}.wav"
        if f.is_file():
            out.append((f"鬧鐘 {i}", str(f)))
    return out
