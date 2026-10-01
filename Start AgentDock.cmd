@echo off
if not exist "%~dp0output\AgentDock\AgentDock.exe" (
  echo Download a release, or run npm run package:win first.
  pause
  exit /b 1
)
start "" "%~dp0output\AgentDock\AgentDock.exe"
