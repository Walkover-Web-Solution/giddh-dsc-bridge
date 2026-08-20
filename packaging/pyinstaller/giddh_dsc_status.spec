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
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

HOST_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "..", "dsc-bridge", "native-host"))
ENTRY = os.path.join(HOST_DIR, "giddh_dsc_status.py")
ROOT_DIR = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

# Single source of truth for version.
VERSION_FILE = os.path.join(ROOT_DIR, "VERSION")
if os.path.exists(VERSION_FILE):
    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        APP_VERSION = f.read().strip()
else:
    APP_VERSION = os.environ.get("VERSION", "1.6.0")

# Choose the right icon file for the platform being built on.
if sys.platform == "darwin":
    ICON_PATH = os.path.join(ROOT_DIR, "icons", "app.icns")
elif sys.platform == "win32":
    ICON_PATH = os.path.join(ROOT_DIR, "icons", "app.ico")
else:
    ICON_PATH = os.path.join(ROOT_DIR, "icons", "tray.png")

# Ensure packaged resources are discoverable next to the frozen executable.
datas, binaries, hiddenimports = [], [], []
for pkg in ("pkcs11", "cryptography", "cffi", "asn1crypto"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Bundle icon resources so the UI and tray can load them at runtime.
if os.path.exists(os.path.join(ROOT_DIR, "icons")):
    datas.append((os.path.join(ROOT_DIR, "icons"), "icons"))
for icon_name in ("app.icns", "app.ico"):
    p = os.path.join(ROOT_DIR, "icons", icon_name)
    if os.path.exists(p):
        datas.append((p, "."))

hiddenimports += [
    "pkcs11", "pkcs11._pkcs11", "pkcs11_signer",
    "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog",
    "PIL", "PIL.Image", "PIL.ImageTk",
]

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
    icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
    version=os.path.join(ROOT_DIR, "packaging", "windows", "version_info.txt") if sys.platform == "win32" and os.path.exists(os.path.join(ROOT_DIR, "packaging", "windows", "version_info.txt")) else None,
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
        icon=ICON_PATH if os.path.exists(ICON_PATH) else None,
        bundle_identifier="com.giddh.dsc.bridge.status",
        info_plist={
            "CFBundleName": "Giddh DSC Bridge",
            "CFBundleDisplayName": "Giddh DSC Bridge",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            # System Tk 8.5 has no dark-mode support and paints label text
            # black regardless of appearance. Opt the bundle out of dark mode
            # so the title bar and native dialogs match the light palette the
            # GUI pins for itself.
            "NSRequiresAquaSystemAppearance": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
