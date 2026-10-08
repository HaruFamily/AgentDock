"""Passive, priority-aware launcher notices; never submit or approve anything."""
from dataclasses import dataclass

from PySide6.QtCore import QObject

from agentdock.ui import theme


KINDS = {
    'approval': ('待授權', 'NOTIFICATION_ACTION'),
    'question': ('待回答', 'NOTIFICATION_ACTION'),
    'failed': ('失敗', 'NOTIFICATION_ERROR'),
    'completed': ('已完成', 'NOTIFICATION_SUCCESS'),
    'cancelled': ('已取消', 'NOTIFICATION_INFO'),
    'native': ('活動更新', 'NOTIFICATION_INFO'),
}


@dataclass(frozen=True)
class Notice:
    counts: tuple = ()
    keys: frozenset = frozenset()
    action_keys: frozenset = frozenset()

    @property
    def kind(self):
        return self.counts[0][0] if self.counts else ''

    @property
    def text(self):
        return ' · '.join(f'{KINDS[k][0]} {n}' for k, n in self.counts)

    @property
    def color(self):
        token = KINDS[self.kind][1] if self.kind else 'NOTIFICATION_INFO'
        return theme.THEMES[theme.NAME].get(token, theme.ACCENT)

    @property
    def total(self):
        return sum(n for _, n in self.counts)


def notice_for(tasks):
    counts, keys, actions = {}, set(), set()
    for task in tasks:
        for q in task['pending']:
            key = ('question', task['owner'], q['id'])
            keys.add(key)
            actions.add(key)
            counts['question'] = counts.get('question', 0) + 1
        latest = task['latest']
        kind = latest['kind']
        if task['state'] == 'approval':
            # Native observation IDs change on unrelated activity. Use permission IDs.
            for request in latest.get('approvals', {}) or {'pending': ''}:
                key = ('approval', task['owner'], task['work_id'], request)
                keys.add(key)
                actions.add(key)
                counts['approval'] = counts.get('approval', 0) + 1
        elif kind != 'question' and not latest['read'] and task['state'] not in ('unknown', 'running'):
            key = (kind, task['owner'], latest['id'])
            keys.add(key)
            kind = kind if kind in KINDS else 'native'
            counts[kind] = counts.get(kind, 0) + 1
    return Notice(tuple((k, counts[k]) for k in KINDS if counts.get(k)), frozenset(keys), frozenset(actions))


class NotificationController(QObject):
    def __init__(self, icon, settings, parent=None):
        super().__init__(parent)
        self.icon, self.settings = icon, settings
        self.notice = Notice()
        self.initialized = False
        self.suspended = False

    def update_notice(self, notice):
        fresh = self.initialized and bool(notice.keys - self.notice.keys)
        sound = self.initialized and bool(notice.action_keys - self.notice.action_keys)
        self.notice = notice
        self.initialized = True
        if sound and not self.suspended and self.settings.get('notification_sound', False):
            self.play_sound()
        symbol = {'approval': '!', 'question': '?', 'failed': '×', 'completed': '✓', 'cancelled': '−', 'native': '·'}.get(notice.kind, '')
        self.icon.set_notification(notice.color, notice.total, fresh and not self.suspended, symbol)
        self.icon.setAccessibleDescription(notice.text)

    @staticmethod
    def play_sound():
        try:
            import winsound
            winsound.PlaySound('SystemNotification', winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except (ImportError, RuntimeError):
            pass

    def set_suspended(self, on):
        self.suspended = on
        if on:
            self.icon.stop_notification_animation()

    def render(self):
        # Re-read the active theme, without replaying sounds or animation.
        self.icon.set_notification(self.notice.color, self.notice.total, symbol=self.icon.notification_symbol)

    def close(self):
        self.icon.stop_notification_animation()
