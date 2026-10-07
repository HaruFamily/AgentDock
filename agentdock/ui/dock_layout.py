"""Icon, compact tab strip and one reusable, optionally detached content window."""
from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, QTimer, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QRegion, QLinearGradient, QPainterPath
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QHBoxLayout, QBoxLayout, QLabel, QPushButton, QScrollArea, QWidget, QGraphicsOpacityEffect

from agentdock.ui import theme
from agentdock.ui.card import FloatingCard, MARGIN, MIN_W, MIN_H


class ContentWindow(FloatingCard):
    dismissed = Signal()

    def set_quota(self, model):
        self.quota = model
        model.changed.connect(self.render_quota)
        model.set_active(self.isVisible() and self.page == 'quota')
        self.render_quota()

    def set_mode(self, mode, auto=False):
        if mode == 'heart':
            self.dismissed.emit()
        else:
            super().set_mode(mode, auto)

    def closeEvent(self, event):
        if getattr(self, '_quitting', False):
            event.accept()
            return
        event.ignore()
        self.dismissed.emit()


class TabBase(QFrame):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(theme.LINE), 1))
        fill = QLinearGradient(0, 0, 0, self.height())
        fill.setColorAt(0, QColor(theme.HEADER_A))
        fill.setColorAt(1, QColor(theme.CANVAS))
        painter.setBrush(fill)
        rect = getattr(self, 'reveal_rect', QRectF(self.rect()))
        radius = min(18, rect.width() / 2, rect.height() / 2)
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)


class DockLayout(QObject):
    DEFAULTS = {'quota': (360, 340), 'qa': (440, 700), 'settings': (680, 380)}

    def __init__(self, icon, content, ui, save):
        super().__init__(content)
        self.icon, self.content, self.ui, self.save = icon, content, ui, save
        icon.capsule_launcher = True
        icon.dock_layout = self
        self.orientation = ui.get('dock_orientation', 'horizontal')
        if self.orientation not in ('horizontal', 'vertical'):
            self.orientation = 'horizontal'
        self.expanded = False
        self.content_open = bool(ui.get('content_open', False))
        self.page = ui.get('card_page', 'quota')
        if self.page not in content.pages:
            self.page = 'quota'
        self.geometry = dict(ui.get('page_geometry', {}))
        if ui.get('dock_size_revision', 0) < 2:
            for key in ('qa', 'settings'):
                if key in self.geometry:
                    g = dict(self.geometry[key])
                    g['size'] = [g.get('size', self.DEFAULTS[key])[0], self.DEFAULTS[key][1]]
                    self.geometry[key] = g
            ui['dock_size_revision'] = 2
        self.scroll_positions = {}
        self.changing = False
        self.suspended = False
        self.strip = TabBase(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.strip.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.strip.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.strip.setAutoFillBackground(False)
        self.strip.setObjectName('DockTabs')
        self.progress = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(220)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self.animate_frame)
        self.animation.finished.connect(self.animation_finished)
        self.scroll = QScrollArea(self.strip)
        self.tab_opacity = QGraphicsOpacityEffect(self.scroll)
        self.scroll.setGraphicsEffect(self.tab_opacity)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        tabs = QWidget()
        row = QHBoxLayout(tabs)
        self.tab_layout = row
        if self.orientation == 'vertical':
            row.setDirection(QBoxLayout.Direction.TopToBottom)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.buttons = {}
        for key, original in content.tab_buttons.items():
            if key not in content.pages:
                continue
            button = QPushButton(original.text())
            button.setCheckable(True)
            button.setMinimumWidth(72)
            button.clicked.connect(lambda _=False, k=key: self.select(k))
            row.addWidget(button)
            self.buttons[key] = button
        self.scroll.setWidget(tabs)
        self.tabs = tabs
        content.external_tabs = True
        content.header.hide()
        toolbar = QWidget()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        bar.addWidget(self.title, 1)
        self.attach_button = QPushButton('貼回')
        self.attach_button.clicked.connect(self.attach)
        bar.addWidget(self.attach_button)
        content.root.insertWidget(0, toolbar)
        content.drag_moved.connect(self.detach)
        content.moved.connect(self.remember)
        icon.drag_moved.connect(self.place)
        icon.moved.connect(lambda _: self.place())
        content.page_changed.connect(self.page_changed)
        if isinstance(content, ContentWindow):
            content.dismissed.connect(self.collapse_content)
        self.restyle()
        self.restore_page(self.page)

    def set_orientation(self, orientation):
        if orientation not in ('horizontal', 'vertical') or orientation == self.orientation:
            return
        self.animation.stop()
        self.orientation = orientation
        self.ui['dock_orientation'] = orientation
        self.tab_layout.setDirection(QBoxLayout.Direction.TopToBottom if orientation == 'vertical'
                                     else QBoxLayout.Direction.LeftToRight)
        self.tab_layout.activate()
        self.progress = 1.0 if self.expanded else 0.0
        self.place()
        self.strip.setVisible(self.expanded)
        self.persist()

    def restyle(self):
        self.strip.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; } '
                                 f'QPushButton {{ padding: 7px 12px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: {theme.MUTED}; }} '
                                 f'QPushButton:hover {{ color: {theme.INK}; background: {theme.HOVER}; }} '
                                 f'QPushButton:checked {{ color: {theme.ACCENT}; background: {theme.ACCENT_SOFT}; border-bottom: 2px solid {theme.ACCENT}; }} '
                                 f'QPushButton:focus {{ border-color: {theme.ACCENT}; }}')
        self.strip.update()
        self.sync_tabs()

    def animate_frame(self, value):
        self.progress = float(value)
        self.place(place_content=False)

    def animation_finished(self):
        if not self.expanded:
            self.strip.hide()

    def animate(self, opening):
        self.animation.stop()
        start = self.progress
        end = 1.0 if opening else 0.0
        self.strip.show()
        self.icon.raise_()
        self.animation.setDuration(max(1, round(220 * abs(end - start))))
        self.animation.setStartValue(start)
        self.animation.setEndValue(end)
        self.animation.start()

    def sync_tabs(self):
        for key, button in self.buttons.items():
            button.setText(self.content.tab_buttons[key].text())
            button.setChecked(self.content_open and key == self.page)
        self.title.setText(self.content.tab_buttons[self.page].text())
        self.attach_button.setVisible(bool(self.geometry.get(self.page, {}).get('detached')))

    def remember(self, *_):
        if self.changing:
            return
        c = self.content
        old = self.geometry.get(self.page, {})
        page = c.pages[self.page]
        areas = page.findChildren(QAbstractScrollArea)
        if isinstance(page, QAbstractScrollArea):
            areas.append(page)
        self.scroll_positions[self.page] = [(area, area.horizontalScrollBar().value(), area.verticalScrollBar().value())
                                           for area in areas]
        self.geometry[self.page] = {**old, 'size': [c.width() - 2 * MARGIN, c.height() - 2 * MARGIN],
                                    'pos': [c.x(), c.y()]}
        self.persist()

    def restore_scroll(self):
        page = self.page
        positions = list(self.scroll_positions.get(page, []))
        def restore():
            if self.page != page:
                return
            for area, x, y in positions:
                try:
                    area.horizontalScrollBar().setValue(x)
                    area.verticalScrollBar().setValue(y)
                except RuntimeError:  # an answered question's editor may have been removed
                    pass
        QTimer.singleShot(0, self.content, restore)

    def persist(self):
        self.ui['page_geometry'] = self.geometry
        self.ui['content_open'] = self.content_open
        self.ui['card_page'] = self.page
        self.save()

    def restore_page(self, key):
        self.changing = True
        self.content._size_timer.stop()
        self.page = key
        g = self.geometry.get(key, {})
        self.content.card_size = tuple(g.get('size', self.DEFAULTS.get(key, (440, 520))))
        self.content._apply_mode()
        if g.get('detached') and g.get('pos'):
            self.content.move(QPoint(*g['pos']))
            self.content.keep_on_screen()
        self.content._size_timer.stop()
        self.changing = False
        self.sync_tabs()
        self.place()

    def page_changed(self, key):
        if key != self.page:
            self.remember()
            self.restore_page(key)
        self.sync_tabs()

    def select(self, key, force=False):
        if self.expanded and self.content_open and key == self.page and not force:
            self.collapse_content()
            return
        opening = not self.expanded
        self.expanded = True
        self.content_open = True
        self.content.set_page(key, by_user=True)
        self.strip.show()
        self.content.show()
        self.place()
        if opening:
            self.animate(True)
        self.content.raise_()
        self.content.activateWindow()
        self.restore_scroll()
        if self.content.quota:
            self.content.quota.set_active(key == 'quota')
        self.sync_tabs()
        self.persist()

    def toggle(self):
        if self.expanded:
            self.hide()
        else:
            self.show()

    def hide(self):
        self.remember()
        self.expanded = False
        self.animate(False)
        self.content.hide()
        if self.content.quota:
            self.content.quota.set_active(False)

    def show(self):
        self.expanded = True
        self.animate(True)
        self.content.setVisible(self.content_open)
        if self.content.quota:
            self.content.quota.set_active(self.content_open and self.page == 'quota')
        self.place()
        self.sync_tabs()
        self.restore_scroll()

    def collapse_content(self):
        self.remember()
        self.content_open = False
        self.content.hide()
        self.sync_tabs()
        if self.content.quota:
            self.content.quota.set_active(False)
        self.persist()

    def detach(self):
        self.geometry.setdefault(self.page, {})['detached'] = True
        self.sync_tabs()

    def attach(self):
        self.geometry.setdefault(self.page, {})['detached'] = False
        self.place()
        self.remember()
        self.sync_tabs()

    def place(self, *_, place_content=True):
        screen = QApplication.screenAt(self.icon.frameGeometry().center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        icon = self.icon.geometry()
        left, right = icon.left() - area.left(), area.right() - icon.right()
        width = min(self.tabs.sizeHint().width() + 10, max(left, right))
        width = max(1, width)
        height = 56 if width < self.tabs.sizeHint().width() + 10 else 38
        to_left = left >= right
        center = icon.center().x() + 1
        full_width = width + 36
        x = center + 18 - full_width if to_left else center - 18
        y = max(area.top(), min(icon.center().y() + 3 - height // 2, area.bottom() - height + 1))
        animated_width = 36 + round(width * self.progress)
        # Never resize the native translucent window during animation: Windows
        # can expose unpainted black rectangles / border flashes while resizing.
        reveal_x = full_width - animated_width if to_left else 0
        reveal_height = 36 + (height - 36) * self.progress
        self.strip.reveal_rect = QRectF(reveal_x, (height - reveal_height) / 2, animated_width, reveal_height)
        vertical = self.orientation == 'vertical'
        if vertical:
            top = icon.center().y() + 3
            above, below = top - area.top() - 18, area.bottom() - top - 18
            extent = min(self.tabs.sizeHint().height() + 12, max(above, below))
            down = below >= extent or below >= above
            full_width = min(max(90, self.tabs.sizeHint().width() + 16), area.width())
            height = max(36, extent + 36)
            x = max(area.left(), min(center - full_width // 2, area.right() - full_width + 1))
            y = top - 18 if down else top + 18 - height
            reveal_height = 36 + (height - 36) * self.progress
            reveal_width = 36 + (full_width - 36) * self.progress
            rx = (center - x - 18) * (1 - self.progress)
            self.strip.reveal_rect = QRectF(rx, 0 if down else height - reveal_height, reveal_width, reveal_height)
        self.strip.setGeometry(x, y, full_width, height)
        # Native window masks affect both paint and hit-testing. The launcher
        # remains reachable even if Windows raises this animated window over it.
        launcher = self.icon.mapToGlobal(QPoint(self.icon.width() // 2, self.icon.height() // 2 + 2))
        hole = QRect(launcher.x() - x - 23, launcher.y() - y - 23, 46, 46)
        outline = QPainterPath()
        radius = min(18, self.strip.reveal_rect.width() / 2, reveal_height / 2)
        outline.addRoundedRect(self.strip.reveal_rect, radius, radius)
        region = QRegion(outline.toFillPolygon().toPolygon()).subtracted(QRegion(hole, QRegion.RegionType.Ellipse))
        # An empty mask means "no mask" in Qt; keep the fully hidden frame clipped.
        self.strip.setMask(region if not region.isEmpty() else QRegion(-2, -2, 1, 1))
        self.strip.update()
        self.icon.raise_()
        # Keep tab widths and global positions stable; only the parent clips.
        # Fade late on opening / early on closing, before clipping reaches text.
        tab_x = 6 if to_left else 36
        self.scroll.setGeometry(tab_x, 3, max(1, full_width - 42), height - 6)
        if vertical:
            self.scroll.setGeometry(6, 36 if down else 6, full_width - 12, max(1, height - 42))
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if vertical else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if vertical else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tab_opacity.setOpacity(max(0.0, min(1.0, (self.progress - .72) / .28)))
        self.scroll.setVisible(self.progress > .72)
        full_rect = QRect(x, y, full_width, height)
        if not place_content:
            return
        if not self.geometry.get(self.page, {}).get('detached'):
            c = self.content
            below = area.bottom() + 1 - (y + height + 6)
            above = y - 6 - area.top()
            use_below = c.height() <= below or below >= above
            space = max(1, below if use_below else above)
            c.setMinimumSize(min(MIN_W + 2 * MARGIN, area.width()), min(MIN_H + 2 * MARGIN, space))
            w, h = c.card_size
            side_left = full_rect.left() - area.left() - 6
            side_right = area.right() - full_rect.right() - 6
            if vertical or (h + 2 * MARGIN > space and max(side_left, side_right) >= MIN_W + 2 * MARGIN):
                side = max(side_left, side_right)
                c.setMinimumSize(min(MIN_W + 2 * MARGIN, max(1, side)), min(MIN_H + 2 * MARGIN, area.height()))
                c.resize(min(w + 2 * MARGIN, side), min(h + 2 * MARGIN, area.height()))
                cx = full_rect.left() - c.width() - 6 if side_left >= side_right else full_rect.right() + 7
                cy = max(area.top(), min(y, area.bottom() - c.height() + 1))
                c.move(cx, cy)
                c._size_timer.stop()
                return
            c.resize(min(w + 2 * MARGIN, area.width()), min(h + 2 * MARGIN, space))
            c._size_timer.stop()
            cy = y + height + 6 if use_below else y - c.height() - 6
            cx = max(area.left(), min(x, area.right() - c.width() + 1))
            c.move(cx, cy)

    def fullscreen(self, on):
        if on and self.expanded:
            self.hide()
            self.animation.stop()
            self.progress = 0.0
            self.strip.hide()
            self.suspended = True
        elif not on and self.suspended:
            self.suspended = False
            self.show()
