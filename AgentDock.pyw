"""Start AgentDock with no console window. AgentDock.exe runs this with the interpreter the venv was made from,
so the venv's packages are added here (runtime\\venv, or .venv for a plain `uv sync` setup)."""
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for venv in (ROOT / "runtime" / "venv", ROOT / ".venv"):
    packages = venv / "Lib" / "site-packages"
    if (venv / "pyvenv.cfg").exists() and packages.is_dir():
        if Path(sys.prefix).resolve() != venv.resolve():
            site.addsitedir(str(packages))
        break
sys.path.insert(0, str(ROOT))

if sys.argv[1:2] == ["--module"] and len(sys.argv) > 2:  # e.g. TokenGauge's floating window
    import runpy
    module = sys.argv[2]
    sys.argv = [module, *sys.argv[3:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    sys.exit(0)

from agentdock.ui.app import main  # noqa: E402

sys.exit(main())
