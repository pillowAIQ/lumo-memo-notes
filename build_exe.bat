@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Please install uv, then run this script again.
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo Missing .venv\Scripts\python.exe. Set up the project environment first.
    exit /b 1
)
uv pip sync requirements-build.txt --python .venv\Scripts\python.exe
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe build_release.py
exit /b %errorlevel%
