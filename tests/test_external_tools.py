from pathlib import Path
import subprocess
import pytest
from agentdock.external_tools import ExternalTools


def test_launch_presets_and_literal_targets(tmp_path, monkeypatch):
    exe = tmp_path / 'tool with spaces.exe'
    exe.touch()
    root = tmp_path / 'repo & name'
    root.mkdir()
    calls = []
    monkeypatch.setattr(subprocess, 'Popen', lambda command, **kw: calls.append((command, kw)))
    for tool, prefix in [('git_extensions', ['browse']), ('fork', []), ('git_gui', ['-C'])]:
        state = {'external_tools': {'git': {'tool': tool, 'executable': str(exe)}}}
        service = ExternalTools(state)
        assert service.open('git', str(root))
        command, options = calls[-1]
        expected = [str(exe), '-C', str(root), 'gui'] if tool == 'git_gui' else [str(exe), *prefix, str(root)]
        assert command == expected
        assert options['shell'] is False
        assert options['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def test_browser_image_defaults_and_invalid_configuration(tmp_path, monkeypatch):
    import os
    opened = []
    monkeypatch.setattr(os, 'startfile', opened.append)
    errors = []
    service = ExternalTools({}, errors.append)
    photo = tmp_path / 'picture.png'
    photo.touch()
    assert service.open('image', str(photo))
    assert service.open('browser', 'https://example.com/?a=1&b=2')
    assert len(opened) == 2
    assert not service.open('browser', 'file:///C:/Windows/cmd.exe')
    service.state['external_tools'] = {'image': {'tool': 'custom', 'executable': str(tmp_path / 'missing.exe')}}
    assert not service.open('image', str(photo))
    assert len(opened) == 2 and errors


def test_preferences_choose_cancel_and_save(ui_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from agentdock.ui.external_tools import ExternalToolsCard
    state, saved = {}, []
    panel = ExternalToolsCard(state, lambda: saved.append(True))
    choice, path, browse = panel.rows['git']
    choice.setCurrentIndex(choice.findData('fork'))
    exe = tmp_path / 'Fork.exe'
    exe.touch()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(exe), ''))
    browse.click()
    assert ExternalTools(state).command('git', str(tmp_path)) == [str(exe), str(tmp_path)]
    snapshot = dict(state['external_tools']['git'])
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: ('', ''))
    browse.click()
    assert state['external_tools']['git'] == snapshot
    restored = ExternalToolsCard(state, lambda: None)
    assert restored.rows['git'][0].currentData() == 'fork'
    assert restored.rows['git'][1].text() == str(exe)
    panel.close(); restored.close()


def test_embedded_git_root_switch_discards_old_results(ui_app, tmp_path, monkeypatch):
    import threading, time
    from agentdock import gitops
    from agentdock.ui.git_view import GitView
    old, new = str(tmp_path / 'old'), str(tmp_path / 'new')
    gate = threading.Event()
    started = threading.Event()
    def repo_root(path):
        if path == old:
            started.set(); gate.wait(3)
        return path
    monkeypatch.setattr(gitops, 'repo_root', repo_root)
    monkeypatch.setattr(gitops, 'status', lambda root: gitops.Status(root, branch=Path(root).name))
    monkeypatch.setattr(gitops, 'numstat', lambda *a: {})
    panel = GitView()
    panel.set_root(old)
    assert started.wait(2)
    panel.set_root(new)
    end = time.monotonic() + 3
    while time.monotonic() < end and (panel.status is None or panel.status.branch != 'new'):
        ui_app.processEvents(); time.sleep(.01)
    assert panel.status.branch == 'new'
    gate.set()
    end = time.monotonic() + 3
    while time.monotonic() < end and panel.busy:
        ui_app.processEvents(); time.sleep(.01)
    assert panel.status.branch == 'new' and panel.root == new
    panel.close()


def test_embedded_git_empty_state_and_first_diff(ui_app, monkeypatch):
    from agentdock import gitops
    from agentdock.ui.git_view import GitView
    panel = GitView(embedded=True)
    panel.resize(720, 400)
    monkeypatch.setattr(panel, '_run', lambda job, then, name='': then('+hello'))
    panel._loaded(('repo', gitops.Status('repo', branch='main'), {}, {}))
    assert panel.split.isHidden() and panel.empty.text() == '沒有未提交的變更'
    panel._loaded(('repo', gitops.Status('repo', branch='main',
        changes=[gitops.Change('a.py', ' ', 'M')]), {}, {}))
    assert not panel.split.isHidden() and panel.empty.isHidden()
    assert panel.changes.topLevelItemCount() == 1
    assert panel.changes.currentItem().text(0).startswith('a.py')
    assert 'hello' in panel.diff.toPlainText()
    panel._loaded(None)
    assert panel.split.isHidden() and '不是 Git' in panel.empty.text()
    panel.close()
