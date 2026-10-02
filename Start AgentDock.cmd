@echo off
cd /d "%~dp0"
rem Keep packages in sync with pyproject.toml (fast when nothing changed).
where uv >nul 2>nul && uv sync -q
if not exist ".venv\Scripts\pythonw.exe" (
  echo uv is required: https://docs.astral.sh/uv/
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m agentdock %*
