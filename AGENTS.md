# AGENTS.md

## Environment
- Always use `.venv/Scripts/python.exe`; bare `python` is the Windows Store stub.

## Devin CLI
- When delegating implementation to Devin CLI, select SWE-2 Flash and use its highest supported reasoning/thinking effort. Model selection and reasoning effort are separate preferences; do not substitute `swe-2-max` for higher thinking effort.
- Resolve and verify the exact SWE-2 Flash model ID and supported effort setting through CLI/bridge preflight before inference. If the integration cannot support this selection, report the blocker rather than silently choosing another model or lower effort.
- Codex (Lead) plans, reviews, and accepts; Devin implements all feature source edits.

## Verification
- UI smoke + screenshots (isolated temp DB, no tray/hotkeys, offscreen): `.venv/Scripts/python.exe ui_smoke.py` -> `audit_shots/redesign/`
- Native Windows shell check (real window platform; asserts OS style bits WS_SYSMENU/WS_MINIMIZEBOX/WS_MAXIMIZEBOX and WM_SYSCOMMAND/ShowWindow minimize/restore — system-command level evidence, NOT a physical taskbar click): `.venv/Scripts/python.exe taskbar_smoke.py`
- Unit tests: `.venv/Scripts/python.exe -m unittest discover -s tests`
  (sync engine/transport tests use a local fake WebDAV server and are occasionally flaky with WinError 10053)

## Delivery
- After each completed application change and its verification, rebuild and verify the Windows EXE, then update the canonical `dist/Lumo` package. Include packaging in Devin implementation assignments; the Lead owns final package verification and delivery.
- Do not push to GitHub or publish/update a GitHub Release unless the user explicitly requests that upload for the current delivery. Local EXE packaging is the default; GitHub upload is a separate action.

## UI conventions
- App display name 轻笺 · Lumo (exe `Lumo.exe`); internal ids stay `memo`/`memo_app` (QSettings, data dir, credentials) — do not rename them.
- Skins: palettes + QSS template in `src/memo_app/theme.py`; use `theme.c(token)` / `theme.qc(token)` instead of hex literals, and refresh icons on `theme.notifier.changed`.
- Settings live in `settings_dialog.py` as an in-window `SettingsView` (pages: general/appearance/hotkeys/sync/about), opened via `MainWindow.open_settings(page)`.
- Palette/QSS: `src/memo_app/theme.py`; icons: `src/memo_app/icons.py`; motion: `src/memo_app/effects.py`.
- Memo cards and the composer are custom-painted (paintEvent), not styled via QSS.
- Never keep Python references to Qt animations started with `DeleteWhenStopped`
  (stale wrapper -> access violation). Reuse persistent animations owned by the widget.
