# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — GerOS V0.5.2"""

import os

_PROJ = os.path.abspath(SPECPATH)

a = Analysis(
    [os.path.join(_PROJ, "Psystem_GerOS_V0.5.py")],
    pathex=[_PROJ],
    binaries=[],
    datas=[
        (os.path.join(_PROJ, "Ring10.wav"), "."),
        (os.path.join(_PROJ, "Windows Logoff Sound.wav"), "."),
        (os.path.join(_PROJ, "logo.jpg"), "."),
        (os.path.join(_PROJ, "system.png"), "."),
        (os.path.join(_PROJ, "zh.jpg"), "."),
        (os.path.join(_PROJ, "Ger壁纸推荐"), "Ger壁纸推荐"),
    ],
    hiddenimports=["PIL", "PIL.Image", "PIL.ImageTk", "PIL.ImageDraw", "psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib", "numpy", "scipy", "pandas",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
        "wx", "gi", "curses",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GerOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
