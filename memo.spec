# -*- mode: python ; coding: utf-8 -*-
from prune_dist import filter_binaries, filter_datas
from pathlib import Path
import sys
from importlib.util import find_spec
from prune_dist import QT_PLUGINS
from build_installer import app_version
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
)

release_version = app_version()
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=tuple(map(int, release_version.split('.'))) + (0,),
                     prodvers=tuple(map(int, release_version.split('.'))) + (0,),
                     mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('FileDescription', 'Lumo'), StringStruct('ProductName', 'Lumo'),
        StringStruct('FileVersion', release_version), StringStruct('ProductVersion', release_version),
        StringStruct('OriginalFilename', 'Lumo.exe'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
)

# Conda-based project interpreters keep these standard-library dependencies
# outside DLLs. Collect only the runtime libraries, never the entire bin folder.
runtime_bin = Path(sys.base_prefix) / 'Library/bin'
runtime_binaries = [(str(runtime_bin / name), '.') for name in
                    ('ffi.dll', 'LIBBZ2.dll', 'liblzma.dll', 'sqlite3.dll',
                     'libssl-3-x64.dll', 'libcrypto-3-x64.dll')
                    if (runtime_bin / name).is_file()]
qt_plugins = Path(find_spec('PySide6').origin).parent / 'plugins'
runtime_binaries += [(str(plugin), 'PySide6/plugins/' + str(plugin.parent.relative_to(qt_plugins)))
                     for plugin in qt_plugins.rglob('*.dll') if plugin.name in QT_PLUGINS]


a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=runtime_binaries,
    datas=[('src/memo_app/assets', 'memo_app/assets')],
    hiddenimports=['pynput.keyboard._win32', 'pynput._util.win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
              'PySide6.QtMultimedia', 'PySide6.Qt3DCore', 'PySide6.QtPdf'],
    noarchive=False,
    optimize=0,
)
a.binaries = filter_binaries(a.binaries)
a.datas = filter_datas(a.datas)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Lumo',
    version=version_info,
    icon='src/memo_app/assets/lumo.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Lumo',
)
