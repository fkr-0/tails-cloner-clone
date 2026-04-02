# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "src"

block_cipher = None

a = Analysis(
    [str(SRC / "tails_cloner" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(ROOT / "assets" / "tails-cloner.svg"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tails-cloner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="tails-cloner",
)
