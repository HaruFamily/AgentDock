"""Per-app external openers. No shell strings, global associations or startup launches."""
from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

GIT_TOOLS = {'git_extensions': 'Git Extensions', 'fork': 'Fork', 'git_gui': 'Git GUI', 'custom': '自訂程式'}


def locate(tool: str) -> str:
    names = {'git_extensions': 'GitExtensions.exe', 'fork': 'Fork.exe', 'git_gui': 'git.exe'}
    name = names.get(tool, '')
    if not name:
        return ''
    found = shutil.which(name)
    if found:
        return found
    bases = [os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', ''),
             os.environ.get('LOCALAPPDATA', '')]
    suffixes = {'git_extensions': ['GitExtensions/GitExtensions.exe', 'Programs/GitExtensions/GitExtensions.exe'],
                'fork': ['Fork/Fork.exe', 'Programs/Fork/Fork.exe'], 'git_gui': ['Git/cmd/git.exe']}
    return next((str(Path(base) / suffix) for base in bases if base
                 for suffix in suffixes[tool] if (Path(base) / suffix).is_file()), '')


class ExternalTools:
    def __init__(self, state: dict, notify=lambda message: None):
        self.state, self.notify = state, notify

    def config(self, kind: str) -> dict:
        default = 'git_extensions' if kind == 'git' else 'system'
        return {'tool': default, 'executable': '', **self.state.get('external_tools', {}).get(kind, {})}

    def command(self, kind: str, target: str) -> list[str] | None:
        if kind not in ('git', 'image', 'browser'):
            raise ValueError('不支援的外部工具類型。')
        if kind == 'browser':
            if urlsplit(target).scheme.lower() not in ('http', 'https'):
                raise ValueError('只支援 HTTP／HTTPS 網址。')
        elif not (os.path.isdir(target) if kind == 'git' else os.path.isfile(target)):
            raise ValueError('找不到資料夾。' if kind == 'git' else '找不到圖片。')
        if kind == 'image' and Path(target).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.tif', '.tiff', '.heic', '.avif'):
            raise ValueError('不支援的圖片格式。')
        config = self.config(kind)
        tool = config['tool']
        if kind != 'git' and tool == 'system':
            return None
        if tool not in (GIT_TOOLS if kind == 'git' else {'custom': ''}):
            raise ValueError('外部工具設定無效。')
        executable = config.get('executable') or locate(tool)
        if not executable or not os.path.isfile(executable):
            raise ValueError('找不到外部程式，請至設定 → 一般 → 外部工具選擇執行檔。')
        if Path(executable).suffix.lower() != '.exe':
            raise ValueError('請選擇 .exe 執行檔。')
        if tool == 'git_gui':
            return [executable, '-C', target, 'gui']
        return [executable, *(['browse'] if tool == 'git_extensions' else []), target]

    def open(self, kind: str, target: str) -> bool:
        try:
            command = self.command(kind, target)
            if command is None:
                os.startfile(target)
            else:
                subprocess.Popen(command, shell=False, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return True
        except (OSError, ValueError) as exc:
            self.notify(str(exc))
            return False
