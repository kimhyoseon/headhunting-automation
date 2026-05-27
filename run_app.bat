@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_app.py
) else (
  python run_app.py
)
pause
