# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec — the visible "Giddh DSC Bridge" status/companion app.
# Unlike the headless native host, this is a WINDOWED GUI (Tkinter):
#   * macOS  -> dist/Giddh DSC Bridge.app   (via BUNDLE)
#   * Windows-> dist/giddh-dsc-status/giddh-dsc-status.exe (Start-menu shortcut)
#
# It reuses pkcs11_signer to probe the token, so it needs the same native deps
# as the host PLUS tkinter (which the host spec deliberately excludes).

import os
import sys
from PyInstaller.utils.hooks import collect_all

HOST_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "..", "dsc-bridge", "native-host"))
ENTRY = os.path.join(HOST_DIR, "giddh_dsc_status.py")

datas, binaries, hiddenimports = [], [], []
for pkg in ("pkcs11", "cryptography", "cffi", "asn1crypto"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += ["pkcs11", "pkcs11._pkcs11", "pkcs11_signer",
                  "tkinter", "tkinter.ttk", "tkinter.messagebox"]

a = Analysis(
    [ENTRY],
    pathex=[HOST_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PySide6", "matplotlib", "numpy"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="giddh-dsc-status",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # windowed GUI — no console
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
    name="giddh-dsc-status",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Giddh DSC Bridge.app",
        icon=None,
        bundle_identifier="com.giddh.dsc.bridge.status",
        info_plist={
            "CFBundleName": "Giddh DSC Bridge",
            "CFBundleDisplayName": "Giddh DSC Bridge",
            "CFBundleShortVersionString": os.environ.get("VERSION", "1.3.0"),
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
