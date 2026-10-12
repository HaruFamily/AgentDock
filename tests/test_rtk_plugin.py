import shutil
import subprocess
from pathlib import Path

import pytest


def test_rtk_opencode_plugin_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the OpenCode plugin contract test')
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [node, '--experimental-vm-modules', str(root / 'tests/rtk_plugin_contract.mjs'),
         str(root / 'agentdock/assets/opencode/rtk.ts')],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
