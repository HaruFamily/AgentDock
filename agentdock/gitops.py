"""Git for a project folder: status, diff, stage, commit, push / pull, branches.

Plain `git` commands, run only when the user asks; no Qt here so the UI can call it from a worker thread.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

TIMEOUT = 60
NETWORK_TIMEOUT = 180
MAX_DIFF = 400_000


class GitError(Exception):
    pass


@dataclass
class Change:
    path: str
    index: str      # staged state letter (' ' none, M, A, D, R, ?)
    work: str       # unstaged state letter
    old: str = ''   # rename source

    @property
    def staged(self) -> bool:
        return self.index not in (' ', '?')

    @property
    def unstaged(self) -> bool:
        return self.work != ' '


@dataclass
class Status:
    root: str
    branch: str = ''
    upstream: str = ''
    ahead: int = 0
    behind: int = 0
    changes: list[Change] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [self.branch or '（分離 HEAD）']
        if self.changes:
            parts.append(f'{len(self.changes)} 個變更')
        if self.ahead:
            parts.append(f'↑{self.ahead}')
        if self.behind:
            parts.append(f'↓{self.behind}')
        return ' · '.join(parts)


def run(root: str, *args: str, timeout: float = TIMEOUT, check: bool = True) -> str:
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0', 'LC_ALL': 'C.UTF-8'}
    try:
        done = subprocess.run(['git', '-C', root, *args], capture_output=True, timeout=timeout, env=env,
                              creationflags=flags, stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise GitError('找不到 git。') from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f'git {args[0]} 逾時。') from e
    out = done.stdout.decode('utf-8', errors='replace')
    if check and done.returncode != 0:
        lines = (done.stderr.decode('utf-8', errors='replace').strip() or out.strip()).splitlines()
        main = [line for line in lines if line.startswith(('fatal:', 'error:'))]
        raise GitError((main or lines or [f'git {args[0]} 失敗（{done.returncode}）'])[0].strip())
    return out


def repo_root(directory: str) -> str | None:
    """The repository holding `directory`, or None."""
    if not directory or not os.path.isdir(directory):
        return None
    try:
        return run(directory, 'rev-parse', '--show-toplevel').strip() or None
    except GitError:
        return None


def status(root: str) -> Status:
    out = run(root, 'status', '--porcelain=v2', '--branch', '-z', '--untracked-files=all')
    result = Status(root=root)
    records = out.split('\0')
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        if record.startswith('# branch.head '):
            head = record[len('# branch.head '):]
            result.branch = '' if head == '(detached)' else head
        elif record.startswith('# branch.upstream '):
            result.upstream = record[len('# branch.upstream '):]
        elif record.startswith('# branch.ab '):
            ahead, behind = record[len('# branch.ab '):].split()
            result.ahead, result.behind = int(ahead), abs(int(behind))
        elif record.startswith('1 '):
            fields = record.split(' ', 8)
            result.changes.append(Change(fields[8], fields[1][0].replace('.', ' '), fields[1][1].replace('.', ' ')))
        elif record.startswith('2 '):
            fields = record.split(' ', 9)
            old = records[i] if i < len(records) else ''
            i += 1
            result.changes.append(Change(fields[9], fields[1][0].replace('.', ' '), fields[1][1].replace('.', ' '), old))
        elif record.startswith('u '):
            fields = record.split(' ', 10)
            result.changes.append(Change(fields[10], 'U', 'U'))
        elif record.startswith('? '):
            result.changes.append(Change(record[2:], '?', '?'))
    return result


def diff(root: str, change: Change, staged: bool) -> str:
    if change.index == '?':
        path = os.path.join(root, change.path)
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read(MAX_DIFF)
        except OSError as e:
            return f'（無法讀取：{e}）'
        return ''.join(f'+{line}\n' for line in text.splitlines())
    args = ['diff', '--no-color', '--no-ext-diff', '--no-textconv']
    if staged:
        args.append('--cached')
    paths = [change.old, change.path] if change.old and staged else [change.path]
    out = run(root, *args, '-M', '--', *paths)
    return out[:MAX_DIFF]


def stage(root: str, paths: list[str]) -> None:
    if paths:
        run(root, 'add', '-A', '--', *paths)


def unstage(root: str, paths: list[str]) -> None:
    if not paths:
        return
    if run(root, 'rev-parse', '--verify', '-q', 'HEAD', check=False).strip():
        run(root, 'reset', '-q', 'HEAD', '--', *paths)
    else:   # no commit yet
        run(root, 'rm', '--cached', '-q', '-r', '--', *paths)


def discard(root: str, change: Change) -> None:
    """Drop the working-tree changes of one file (untracked files are deleted)."""
    if change.index == '?':
        path = os.path.join(root, change.path)
        if os.path.isdir(path):
            raise GitError('這是資料夾，請在檔案總管處理。')
        os.remove(path)
    else:
        run(root, 'checkout', '--', change.path)


def commit(root: str, message: str) -> str:
    if not message.strip():
        raise GitError('提交訊息是空的。')
    run(root, 'commit', '-q', '-m', message)
    return run(root, 'log', '-1', '--format=%h %s').strip()


def push(root: str) -> str:
    current = status(root)
    if not current.branch:
        raise GitError('分離 HEAD 無法推送。')
    if current.upstream:
        out = run(root, 'push', timeout=NETWORK_TIMEOUT)
    else:
        out = run(root, 'push', '-u', 'origin', current.branch, timeout=NETWORK_TIMEOUT)
    return out.strip()


def pull(root: str) -> str:
    return run(root, 'pull', '--ff-only', timeout=NETWORK_TIMEOUT).strip()


def fetch(root: str) -> None:
    run(root, 'fetch', '--prune', timeout=NETWORK_TIMEOUT)


def branches(root: str) -> list[str]:
    out = run(root, 'for-each-ref', '--format=%(refname:short)', '--sort=-committerdate', 'refs/heads')
    return [line for line in out.splitlines() if line]


def numstat(root: str, staged: bool) -> dict[str, tuple[int, int]]:
    """Lines added and removed per changed path (binary files left out); {} when git cannot say."""
    try:
        out = run(root, 'diff', '--numstat', '--no-color', '--no-ext-diff', '--no-textconv', '-M', *(['--cached'] if staged else []))
    except GitError:
        return {}
    found = {}
    for line in out.splitlines():
        parts = line.split('\t', 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        added, removed, path = parts
        if ' => ' in path:   # a rename: 'old => new' or 'dir/{old => new}/file'
            if '{' in path:
                head, rest = path.split('{', 1)
                inner, tail = rest.split('}', 1)
                path = head + inner.split(' => ')[1] + tail
            else:
                path = path.split(' => ')[1]
        found[path.replace('//', '/')] = (int(added), int(removed))
    return found


def checkout(root: str, branch: str, create: bool = False) -> None:
    if create:
        run(root, 'switch', '-c', branch)
    else:
        run(root, 'switch', branch)


def log(root: str, limit: int = 30) -> list[tuple[str, str, str]]:
    """Recent commits: (short hash, subject, relative date)."""
    if not run(root, 'rev-parse', '--verify', '-q', 'HEAD', check=False).strip():
        return []
    out = run(root, 'log', f'-{limit}', '--format=%h%x1f%s%x1f%cr')
    return [tuple(line.split('\x1f', 2)) for line in out.splitlines() if line.count('\x1f') == 2]
