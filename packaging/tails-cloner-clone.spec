# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path.cwd()
SRC = ROOT / "src"

block_cipher = None

a = Analysis(
    [str(SRC / "tails_cloner" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "assets" / "tails-cloner-clone.svg"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-16.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-24.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-32.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-48.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-64.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-128.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-256.png"), "assets"),
        (str(ROOT / "assets" / "tails-cloner-clone-512.png"), "assets"),
        (str(ROOT / "assets" / "tails-signing-minimal.key"), "assets"),
    ],
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
    name="tails-cloner-clone",
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
    name="tails-cloner-clone",
)
