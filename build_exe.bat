@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Please install uv, then run this script again.
    exit /b 1
)
rem uv.lock pins every dependency; --locked fails instead of silently re-resolving.
uv sync --locked
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe build_release.py
exit /b %errorlevel%
