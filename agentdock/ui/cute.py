"""Dressing for the sweet look: every dialog, menu and tooltip of the app loses its square system frame.

Installed once on the QApplication. Dialogs (ours, QMessageBox, QInputDialog) become frameless and are
painted by the local look's draw_dialog(painter, rect, title, close_rect, close_hovered); without that hook
nothing changes and the system frame stays. Menus and tooltips only get a transparent window so their
rounded style-sheet corners show. Only painting and window flags are touched: buttons, keys and results
of the dialogs stay the same (Esc and the drawn close button reject).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMenu, QMessageBox, QWidget

from agentdock.ui import theme

MARGINS = (30, 58, 30, 26)   # room for the lace trim and the drawn title
CLOSE = 26


def round_mask(widget: QWidget, radius: float) -> None:
    """Cut the window itself to its rounded shape. Windows paints a transparent window's corners black when
    its transparency came too late; outside the mask there is nothing left to paint."""
    from PySide6.QtGui import QPainterPath, QRegion
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


BUTTON_WORDS = {'Ok': '確定', 'Cancel': '取消', 'Yes': '是', 'No': '否', 'Close': '關閉', 'Save': '儲存',
                'Discard': '不儲存', 'Retry': '重試', 'Apply': '套用', 'Abort': '中止', 'Ignore': '略過'}


def chinese_buttons(dialog: QWidget) -> None:
    """Qt ships no Chinese for its standard buttons (OK / Cancel / Yes / No …)."""
    from PySide6.QtWidgets import QDialogButtonBox
    for box in dialog.findChildren(QDialogButtonBox):
        for button in box.buttons():
            name = box.standardButton(button).name
            if name in BUTTON_WORDS:
                button.setText(BUTTON_WORDS[name])


def close_rect(widget: QWidget) -> QRect:
    return QRect(widget.width() - MARGINS[2] - CLOSE + 4, 20, CLOSE, CLOSE)


class Dresser(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag: QPoint | None = None

    # -- which widgets ---------------------------------------------------------------
    @staticmethod
    def dressed(widget) -> bool:
        return bool(widget.property('cute_dressed'))

    def _polish(self, widget) -> None:
        if widget.isVisible() or widget.property('cute_seen'):
            return
        widget.setProperty('cute_seen', True)
        if isinstance(widget, QDialog) and widget.isWindow() and not isinstance(widget, QFileDialog):
            if theme.decorated('dialog'):
                widget.setProperty('cute_dressed', True)
                widget.setWindowFlags(widget.windowFlags() | Qt.WindowType.FramelessWindowHint)
                widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                widget.setStyleSheet((widget.styleSheet() or '') + '\nQDialog { background: transparent; }')
                widget.setMouseTracking(True)
                widget.setProperty('cute_round', 26)
        elif isinstance(widget, QMenu) or widget.windowType() == Qt.WindowType.ToolTip:
            if theme.CUTE:
                widget.setProperty('cute_round', 16 if isinstance(widget, QMenu) else 12)
                # Without a transparent window the corners outside the rounded border stay square.
                widget.setWindowFlags(widget.windowFlags() | Qt.WindowType.FramelessWindowHint
                                      | Qt.WindowType.NoDropShadowWindowHint)
                widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    @staticmethod
    def _message_box(box) -> None:
        """Chinese button names (Qt ships none here) and, in the sweet look, its own icon instead of the
        system's blue question mark."""
        box.setProperty('cute_words', True)
        names = {QMessageBox.StandardButton.Yes: '是', QMessageBox.StandardButton.No: '否',
                 QMessageBox.StandardButton.Ok: '好', QMessageBox.StandardButton.Cancel: '取消',
                 QMessageBox.StandardButton.Close: '關閉', QMessageBox.StandardButton.Save: '儲存',
                 QMessageBox.StandardButton.Discard: '不儲存', QMessageBox.StandardButton.Retry: '重試'}
        for standard, text in names.items():
            b = box.button(standard)
            if b is not None:
                b.setText(text)
        if theme.CUTE and theme.PRIVATE is not None and callable(getattr(theme.PRIVATE, 'draw_icon', None)) \
                and box.icon() != QMessageBox.Icon.NoIcon:
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import QPointF
            pixmap = QPixmap(96, 96)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            try:
                theme.PRIVATE.draw_icon(painter, QPointF(48, 48), 84)
            except Exception:
                pass
            painter.end()
            pixmap.setDevicePixelRatio(2)
            box.setIconPixmap(pixmap)

    # -- events ------------------------------------------------------------------------
    def eventFilter(self, obj, event):  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Polish and isinstance(obj, QWidget) and obj.isWindow():
            self._polish(obj)
            return False
        if kind == QEvent.Type.Show and isinstance(obj, QMessageBox) and not obj.property('cute_words'):
            self._message_box(obj)
        if kind == QEvent.Type.Show and isinstance(obj, QDialog) and not obj.property('cute_buttons'):
            obj.setProperty('cute_buttons', True)
            chinese_buttons(obj)
        if kind == QEvent.Type.Resize and isinstance(obj, QWidget) and obj.isWindow() and obj.property('cute_round'):
            round_mask(obj, float(obj.property('cute_round')))
        if not isinstance(obj, QDialog) or not self.dressed(obj):
            return False
        if kind == QEvent.Type.Show and not obj.property('cute_margins'):
            layout = obj.layout()
            if layout is not None:
                obj.setProperty('cute_margins', True)
                m = layout.contentsMargins()
                layout.setContentsMargins(max(m.left(), MARGINS[0]), m.top() + MARGINS[1],
                                          max(m.right(), MARGINS[2]), m.bottom() + MARGINS[3])
                obj.adjustSize()
                parent = obj.parentWidget()
                if parent is not None:   # adjustSize moved the corner: centre on the parent again
                    centre = parent.window().frameGeometry().center()
                    obj.move(centre - QPoint(obj.width() // 2, obj.height() // 2))
        elif kind == QEvent.Type.Paint:
            painter = QPainter(obj)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # Windows may not honour the transparency at all: fill the whole rounded shape first, so nothing
            # the look leaves unpainted (between lace scallops, the corners) shows black.
            from PySide6.QtGui import QColor, QPainterPath
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(obj.rect()), float(obj.property('cute_round') or 26),
                                 float(obj.property('cute_round') or 26))
            painter.fillPath(shape, QColor(theme.CANVAS or '#ffffff'))
            hovered = close_rect(obj).contains(obj.mapFromGlobal(QCursor.pos()))
            theme.decorate('dialog', painter, QRectF(obj.rect()), obj.windowTitle(), QRectF(close_rect(obj)), hovered)
            painter.end()
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            at = event.position().toPoint()
            if close_rect(obj).contains(at):
                obj.reject()
                return True
            if at.y() < MARGINS[1]:
                self._drag = event.globalPosition().toPoint() - obj.frameGeometry().topLeft()
                return True
        elif kind == QEvent.Type.MouseMove:
            if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
                obj.move(event.globalPosition().toPoint() - self._drag)
                return True
            obj.update(close_rect(obj).adjusted(-4, -4, 4, 4))
        elif kind == QEvent.Type.MouseButtonRelease:
            self._drag = None
        elif kind in (QEvent.Type.WindowTitleChange, QEvent.Type.Resize, QEvent.Type.Leave):
            obj.update()
        return False


def install(app: QApplication) -> Dresser:
    dresser = Dresser(app)
    app.installEventFilter(dresser)
    return dresser


def tint_caption(widget: QWidget) -> None:
    """Windows 11: the main window's own title bar and border in the look's colours (round corners).
    Older Windows ignores the attributes; other systems do nothing."""
    import sys
    if sys.platform != 'win32' or not widget.isWindow():
        return
    try:
        import ctypes
        from PySide6.QtGui import QColor
        hwnd = int(widget.winId())

        def colorref(name: str | None) -> int:
            if not name:
                return 0xFFFFFFFF   # DWMWA_COLOR_DEFAULT
            c = QColor(name)
            return c.red() | (c.green() << 8) | (c.blue() << 16)
        cute = theme.CUTE
        values = {33: 2 if cute else 0,                                  # corner preference: round
                  34: colorref(theme.LINE if cute else None),            # border
                  35: colorref(theme.HEADER_A if cute else None),        # caption
                  36: colorref(theme.ACCENT_DEEP if cute else None)}     # caption text
        for attribute, value in values.items():
            v = ctypes.c_uint(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        import logging
        logging.info('caption colour not applied', exc_info=True)
