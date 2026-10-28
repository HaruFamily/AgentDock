"""`python -m agentdock.launch`: start the floating app detached and without a console window, then exit.
scripts\\setup.ps1 uses this after preparing runtime\\ so its window can close right away."""
from __future__ import annotations

import os
import subprocess
import sys

from agentdock.paths import ROOT, gui_command


def start(*args: str) -> None:
    """The app as its own detached process, outliving the one that started it (also used by 重新啟動)."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(gui_command(*args), cwd=str(ROOT), env=dict(os.environ),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True, start_new_session=sys.platform != "win32")


def main() -> int:
    start(*sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
