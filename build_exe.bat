@echo off
cd /d %~dp0
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --name memo --add-data "src/memo_app/assets;memo_app/assets" --add-binary ".venv/Lib/site-packages/PySide6/plugins;PySide6/plugins" --hidden-import pynput.keyboard._win32 --hidden-import pynput._util.win32 launcher.py
.venv\Scripts\python.exe prune_dist.py
echo.
echo Done: dist\memo\memo.exe (slim)
pause
