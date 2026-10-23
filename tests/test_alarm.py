from datetime import datetime, timedelta

from agentdock.tools.alarm.core import (Alarm, AlarmStore, due, first_after, next_ring, rang, repeat_text, snoozed,
                                        until_text, when_text)

FRI = datetime(2026, 10, 23, 7, 0)   # a Friday


def at(t: datetime, **kw) -> Alarm:
    return Alarm(armed=t.timestamp(), **kw)


def test_next_time_follows_the_repeat_days():
    assert first_after(Alarm(7, 30), FRI) == FRI.replace(minute=30)
    assert first_after(Alarm(6, 30), FRI) == datetime(2026, 10, 24, 6, 30)                 # passed today: tomorrow
    assert first_after(Alarm(7, 30, days=[0, 1, 2, 3, 4]), FRI + timedelta(hours=1)) == datetime(2026, 10, 26, 7, 30)
    assert first_after(Alarm(9, 0, days=[5, 6]), FRI) == datetime(2026, 10, 24, 9, 0)


def test_rings_once_then_waits_or_turns_off():
    once = at(FRI, hour=7, minute=30)
    daily = at(FRI, hour=7, minute=30, days=list(range(7)))
    assert due(once, FRI + timedelta(minutes=29)) == ''
    ring = FRI + timedelta(minutes=30)
    assert due(once, ring) == 'ring' and due(daily, ring) == 'ring'
    rang(once, ring)
    rang(daily, ring)
    assert not once.enabled and next_ring(once, ring) is None
    assert daily.enabled and next_ring(daily, ring) == datetime(2026, 10, 24, 7, 30)
    assert due(daily, ring + timedelta(minutes=1)) == ''


def test_missed_long_ago_is_skipped_not_rung():
    alarm = at(FRI, hour=7, minute=30, days=list(range(7)))
    assert due(alarm, FRI + timedelta(minutes=35)) == 'ring'          # five minutes late (sleep): still rings
    assert due(alarm, FRI + timedelta(hours=3)) == 'skip'             # hours later: quietly wait for tomorrow
    assert next_ring(alarm, FRI + timedelta(hours=3)) == datetime(2026, 10, 24, 7, 30)


def test_snooze_brings_a_one_time_alarm_back():
    alarm = at(FRI, hour=7, minute=0)
    rang(alarm, FRI)
    snoozed(alarm, FRI)
    assert alarm.enabled and next_ring(alarm, FRI) == FRI + timedelta(minutes=5)
    assert due(alarm, FRI + timedelta(minutes=4)) == '' and due(alarm, FRI + timedelta(minutes=5)) == 'ring'
    rang(alarm, FRI + timedelta(minutes=5))
    assert not alarm.enabled and alarm.snooze is None


def test_words():
    assert repeat_text([]) == '只響一次' and repeat_text(list(range(7))) == '每天'
    assert repeat_text([0, 1, 2, 3, 4]) == '週一到週五' and repeat_text([6, 5]) == '週末'
    assert repeat_text([0, 2]) == '週一、三'
    assert until_text(FRI + timedelta(seconds=20), FRI) == '不到 1 分鐘後'
    assert until_text(FRI + timedelta(hours=9, minutes=12), FRI) == '9 小時 12 分後'
    assert until_text(FRI + timedelta(days=2, hours=1), FRI) == '2 天 1 小時後'
    assert when_text(FRI + timedelta(minutes=30), FRI) == '今天 07:30'
    assert when_text(FRI + timedelta(days=1), FRI) == '明天 07:00'
    assert when_text(FRI + timedelta(days=4), FRI) == '週二 07:00'


def test_store_keeps_alarms(tmp_path):
    store = AlarmStore(tmp_path / 'alarms.json')
    store.put(Alarm(7, 30, label='上班', days=[0, 1, 2, 3, 4]))
    again = AlarmStore(tmp_path / 'alarms.json')
    assert [(a.clock(), a.label, a.days) for a in again.alarms] == [('07:30', '上班', [0, 1, 2, 3, 4])]
    (tmp_path / 'broken.json').write_text('{', encoding='utf-8')
    assert AlarmStore(tmp_path / 'broken.json').alarms == []


def test_view_rings_snoozes_and_turns_off(tmp_path, ui_app):
    from agentdock.tools.alarm import AlarmView
    now = [FRI]
    notes = []
    store = AlarmStore(tmp_path / 'alarms.json')
    store.put(at(FRI, hour=7, minute=1, label='吃藥'))
    view = AlarmView(store, notes.append, clock=lambda: now[0])
    view.timer.stop()
    view.bell.play = lambda *_a, **_k: None
    view.tick()
    assert not view.ringing
    now[0] = FRI + timedelta(minutes=1)
    view.tick()
    window = view.ringing[store.alarms[0].id]
    assert notes == ['鬧鐘 07:01：吃藥'] and not store.alarms[0].enabled
    view.tick()
    assert len(view.ringing) == 1                    # no second window for the same alarm
    window.snooze_button.click()
    assert not view.ringing and store.alarms[0].enabled
    now[0] += timedelta(minutes=5)
    view.tick()
    window = next(iter(view.ringing.values()))
    window.off_button.click()
    assert not view.ringing and not store.alarms[0].enabled and store.alarms[0].snooze is None
    assert AlarmStore(tmp_path / 'alarms.json').alarms[0].enabled is False   # saved


def test_editor_values(ui_app):
    from agentdock.tools.alarm import AlarmEditor, Bell
    dialog = AlarmEditor(None, Alarm(6, 45, label='晨跑', days=[5, 6], sound='-'), Bell())
    assert dialog.values() == {'hour': 6, 'minute': 45, 'label': '晨跑', 'days': [5, 6], 'sound': '-'}
    dialog.days[0].click()
    assert dialog.values()['days'] == [0, 5, 6]


def test_alarm_is_a_tool():
    from agentdock.tools import discover
    assert '鬧鐘' in [t.title for t in discover()]
