"""Explicit CLI updates, targeting the installation used by AgentDock."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess

from agentdock.agent_info import find_program, program_version

PACKAGES = {'codex': '@openai/codex', 'claude-code': '@anthropic-ai/claude-code',
            'grok-cli': '@xai-official/grok'}


@dataclass(frozen=True)
class UpdatePlan:
    executable: str
    command: list[str]


def plan_update(kind: str) -> UpdatePlan:
    if kind not in PACKAGES:
        raise ValueError('此 Agent 尚未支援更新。')
    executable = find_program(kind)
    if not executable:
        raise ValueError('找不到客戶端執行檔。')
    path = Path(executable)
    # Match the outer package, not the nested platform package's node_modules.
    for parent in path.parents:
        if parent.name.lower() == 'node_modules':
            package = parent.joinpath(*PACKAGES[kind].split('/'))
            if path.is_relative_to(package):
                npm = shutil.which('npm.cmd') or shutil.which('npm')
                if not npm:
                    raise ValueError('找不到 npm，無法更新這份安裝。')
                return UpdatePlan(executable, [npm, 'install', '-g', '--prefix', str(parent.parent),
                                               PACKAGES[kind] + '@latest'])
    native = {'claude-code': Path.home() / '.local' / 'bin',
              'grok-cli': Path.home() / '.grok' / 'bin'}.get(kind)
    if native and path.parent == native and path.suffix.lower() == '.exe':
        return UpdatePlan(executable, [executable, 'update'])
    raise ValueError('這份程式由其他安裝方式管理，請從原安裝來源更新。\n' + executable)


def run_update(plan: UpdatePlan) -> str:
    try:
        result = subprocess.run(plan.command, stdin=subprocess.DEVNULL, capture_output=True,
                                encoding='utf-8', errors='replace', timeout=600,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as e:
        raise RuntimeError('更新逾時，請確認安裝狀態後再試。') from e
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()[-2000:]
        raise RuntimeError(detail or f'更新失敗（結束代碼 {result.returncode}）。')
    version = program_version(plan.executable)
    if not version:
        raise RuntimeError('更新指令已完成，但無法確認程式版本。請確認安裝狀態。')
    return version
