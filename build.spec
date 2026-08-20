import os
import sys
from pathlib import Path

# PyInstaller spec for VideoDownloader.exe
# Built by build_windows.bat (or: pyinstaller build.spec --noconfirm)

# PyInstaller >=6.22 no longer defines __file__ in the spec namespace;
# SPECPATH points at the directory containing this spec file.
ROOT = Path(SPECPATH).resolve()

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "yt_dlp",
        "yt_dlp.extractor",
        "httpx",
        "dotenv",
        "dotenv.main",
        "keyring",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VideoDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
