@echo off
cd /d %~dp0
.venv\Scripts\python.exe -m memo_app
if errorlevel 1 pause
