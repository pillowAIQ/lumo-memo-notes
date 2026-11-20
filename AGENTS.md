# AGENTS.md

## Environment
- The venv is managed by uv (Python 3.11 pinned in `.python-version`, deps locked in `uv.lock`). Create/refresh it with `uv sync`; change deps via `pyproject.toml` + `uv lock`, never `pip install`.
- Always use `.venv/Scripts/python.exe`; bare `python` is the Windows Store stub.

## Verification
- UI smoke + screenshots (isolated temp DB, no tray/hotkeys, offscreen): `.venv/Scripts/python.exe ui_smoke.py` -> `audit_shots/redesign/`
- Native Windows shell check (real window platform; asserts OS style bits WS_SYSMENU/WS_MINIMIZEBOX/WS_MAXIMIZEBOX and WM_SYSCOMMAND/ShowWindow minimize/restore — system-command level evidence, NOT a physical taskbar click): `.venv/Scripts/python.exe taskbar_smoke.py`
- Unit tests: `.venv/Scripts/python.exe -m unittest discover -s tests`
  (sync engine/transport tests use a local fake WebDAV server and are occasionally flaky with WinError 10053)

## Delivery
- After each completed application change and its verification, rebuild and verify the Windows EXE, then update the canonical `dist/Lumo` package. Codex owns implementation, verification, packaging, and delivery.
- Do not push to GitHub or publish/update a GitHub Release unless the user explicitly requests that upload for the current delivery. Local EXE packaging is the default; GitHub upload is a separate action.

## UI conventions
- App display name 轻笺 · Lumo (exe `Lumo.exe`); internal ids stay `memo`/`memo_app` (QSettings, data dir, credentials) — do not rename them.
- Skins: palettes + QSS template in `src/memo_app/theme.py`; use `theme.c(token)` / `theme.qc(token)` instead of hex literals, and refresh icons on `theme.notifier.changed`.
- Styles (`theme.STYLES`: `mica` default = 3 panes + date-grouped list + always-on detail pane; `paper` = serif journal, calendar-page sidebar, ruled list, detail pane only while a memo is selected) are orthogonal to skins. QSettings keys: `theme` (skin) and `ui_style`. Both styles share one widget tree; branch with `theme.is_paper()` in paint code and put style-only rules in `_MICA_QSS` / `_PAPER_QSS`. `theme.apply_style(k)` also switches to that style's recommended skin.
- The main list contains non-selectable group-header items (`UserRole` is None): use `MainWindow.memo_items()` / `memo_count()`, not `list.count()`.
- `DetailPane` (`detail_pane.py`) saves immediately; anything that writes from inside a list selection signal must be deferred (`QTimer.singleShot(0, ...)`) because writes trigger `reload()`, which rebuilds the QListWidget.
- Settings live in `settings_dialog.py` as an in-window `SettingsView` (pages: general/appearance/hotkeys/sync/about), opened via `MainWindow.open_settings(page)`.
- Palette/QSS: `src/memo_app/theme.py`; icons: `src/memo_app/icons.py`; motion: `src/memo_app/effects.py`.
- Memo cards and the composer are custom-painted (paintEvent), not styled via QSS.
- Never keep Python references to Qt animations started with `DeleteWhenStopped`
  (stale wrapper -> access violation). Reuse persistent animations owned by the widget.
