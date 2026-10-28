"""Settings and UI routing for external applications."""
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QFileDialog
from agentdock.external_tools import ExternalTools, GIT_TOOLS, locate
from agentdock.ui.qa_view import button, label
from agentdock.ui.settings_page import Card


def open_target(kind: str, target: str) -> bool:
    app = QApplication.instance()
    service = getattr(app, 'agentdock_external_tools', None)
    if service is None:
        if kind == 'git':
            from PySide6.QtWidgets import QMessageBox
            return ExternalTools({}, lambda text: QMessageBox.warning(None, '外部工具', text)).open(kind, target)
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        return QDesktopServices.openUrl(QUrl(target) if kind == 'browser' else QUrl.fromLocalFile(target))
    return service.open(kind, target)


class ExternalToolsCard(Card):
    def __init__(self, state: dict, save):
        super().__init__()
        self.state, self.save = state, save
        self.rows = {}
        for kind, title in [('git', 'Git'), ('image', '圖片'), ('browser', '瀏覽器')]:
            self.lay.addWidget(label(title, name='FieldLabel'))
            row = QHBoxLayout()
            choice = QComboBox()
            choices = GIT_TOOLS if kind == 'git' else {'system': '系統預設', 'custom': '自訂程式'}
            for key, text in choices.items():
                choice.addItem(text, key)
            current = ExternalTools(state).config(kind)
            choice.setCurrentIndex(max(0, choice.findData(current['tool'])))
            row.addWidget(choice)
            path = QLineEdit()
            path.setReadOnly(True)
            path.setAccessibleName(title + '執行檔')
            row.addWidget(path, 1)
            browse = button('瀏覽…', flat=True)
            row.addWidget(browse)
            self.lay.addLayout(row)
            self.rows[kind] = (choice, path, browse)
            choice.currentIndexChanged.connect(lambda _i, k=kind: self.select(k))
            browse.clicked.connect(lambda _c=False, k=kind: self.browse(k))
            self.render(kind)

    def render(self, kind):
        choice, path, browse = self.rows[kind]
        config = ExternalTools(self.state).config(kind)
        tool = choice.currentData()
        path.setText(config.get('executable') or locate(tool))
        path.setCursorPosition(0)
        path.setVisible(tool != 'system')
        browse.setVisible(tool != 'system')

    def select(self, kind):
        tool = self.rows[kind][0].currentData()
        self.state.setdefault('external_tools', {})[kind] = {'tool': tool, 'executable': ''}
        self.save()
        self.render(kind)

    def browse(self, kind):
        choice, path, _browse = self.rows[kind]
        selected, _ = QFileDialog.getOpenFileName(self, '選擇執行檔', path.text(), '程式 (*.exe)')
        if selected:
            self.state.setdefault('external_tools', {})[kind] = {'tool': choice.currentData(), 'executable': selected}
            self.save()
            self.render(kind)
