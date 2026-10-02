@echo off
cd /d "%~dp0"
uv sync || (pause & exit /b 1)
uv run pytest -q
pause
