# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec — freezes the Giddh DSC native host into a single, self
# contained executable per OS (no Python required on the end-user machine).
#
# Build:  pyinstaller packaging/pyinstaller/giddh_dsc_host.spec
# Output: dist/giddh-dsc-host           (macOS / Linux)
#         dist/giddh-dsc-host.exe       (Windows)
#
# The compiled `pkcs11._pkcs11` C-extension and `cryptography` backends are
# collected explicitly so the frozen binary can talk to the token driver and
# parse certificates without a system Python.

import os
from PyInstaller.utils.hooks import collect_all

# SPECPATH is injected by PyInstaller; resolve source paths relative to it so
# the build works regardless of the current working directory.
HOST_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "..", "dsc-bridge", "native-host"))
ENTRY = os.path.join(HOST_DIR, "giddh_dsc_host.py")

datas, binaries, hiddenimports = [], [], []
for pkg in ("pkcs11", "cryptography", "cffi", "asn1crypto"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += ["pkcs11", "pkcs11._pkcs11", "pkcs11_signer"]

a = Analysis(
    [ENTRY],
    pathex=[HOST_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PySide6", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onedir mode: the interpreter + libraries live next to the executable instead
# of being extracted to a temp folder on each launch. This is essential on
# macOS — an unsigned onefile extraction is rejected by Gatekeeper when Chrome
# launches it ("Python.framework is damaged"). onedir loads the libs in place.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="giddh-dsc-host",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # native messaging talks over stdio — must be console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,      # CI sets arch per runner; None = host arch
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="giddh-dsc-host",   # produces dist/giddh-dsc-host/ (folder)
)
