@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 open_browser_workspace.py
) else (
  python open_browser_workspace.py
)
