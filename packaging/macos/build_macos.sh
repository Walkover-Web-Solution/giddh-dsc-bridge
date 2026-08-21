#!/bin/bash
# Build the macOS installer: freezes the native host, wraps it in a signed-able
# .pkg that registers the Chrome native-messaging manifest, then packages the
# .pkg inside a distributable .dmg.
#
# Requirements (provided by the macOS CI runner): python3, pyinstaller,
# pkgbuild, hdiutil (all ship with macOS + the pip deps installed by CI).
#
# Inputs (env):
#   VERSION        package version         (default: contents of ../../VERSION)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ../extension-id.txt)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEFAULT_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || echo 1.7.0)"
VERSION="${VERSION:-$DEFAULT_VERSION}"
EXT_ID="${GIDDH_EXT_ID:-$(tr -d '[:space:]' < "$ROOT/packaging/extension-id.txt")}"
HOST_NAME="com.giddh.dsc.bridge"
INSTALL_DIR="/usr/local/giddh-dsc-bridge"

BUILD="$ROOT/packaging/macos/build"
PKGROOT="$BUILD/pkgroot"
SCRIPTS="$BUILD/scripts"
DMGROOT="$BUILD/dmgroot"
OUT="$ROOT/dist"
rm -rf "$BUILD"; mkdir -p "$PKGROOT$INSTALL_DIR" "$SCRIPTS" "$DMGROOT" "$OUT"

echo "==> Freezing native host with PyInstaller (onedir)"
( cd "$ROOT" && python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_host.spec )

# COLLECT can return before all framework binaries (notably the Python shared
# library at _internal/Python.framework/Versions/X.Y/Python) finish being
# written to disk — leaving a half-built _internal/ where the bootloader then
# fails with "Failed to load Python shared library ... no such file".
# Sync + verify the critical files exist before staging.
sync
HOST_OUT="$ROOT/dist/giddh-dsc-host"
HOST_BIN="$HOST_OUT/giddh-dsc-host"
if [ ! -f "$HOST_BIN" ]; then
  echo "ERROR: PyInstaller did not produce $HOST_BIN"
  exit 1
fi
# Find the actual Python shared library PyInstaller bundled and confirm it
# is on disk. _internal/Python is usually a symlink into a framework.
# NOTE: -type l must be grouped with \( -o \) — without it, `find` parses
# "-type l -name Python -o -name Python3" as "(-type l -name Python) OR
# (-name Python3)", so the second alternative matches ANY file/dir named
# Python3 (not just the symlink), silently masking a missing-symlink case.
PY_SHLIB=$(find "$HOST_OUT/_internal" -maxdepth 6 -type l \( -name 'Python' -o -name 'Python3' \) 2>/dev/null | head -1)
if [ -z "$PY_SHLIB" ] || [ ! -e "$PY_SHLIB" ]; then
  echo "ERROR: PyInstaller did not write the Python shared library (looked for"
  echo "       _internal/Python{,3} symlinks in $HOST_OUT/_internal)"
  echo "       _internal contents:"
  ls -la "$HOST_OUT/_internal" 2>&1 | head -20 || true
  exit 1
fi

# onedir output is a folder; install its contents into INSTALL_DIR so the
# executable lands at $INSTALL_DIR/giddh-dsc-host with libs in _internal/ beside it.
cp -R "$HOST_OUT/." "$PKGROOT$INSTALL_DIR/"
chmod +x "$PKGROOT$INSTALL_DIR/giddh-dsc-host"
sync

# The symlink-exists check above is not sufficient — it has passed on a CI
# runner before while the SHIPPED package still crashed for users with
# "Failed to load Python shared library ... no such file" (root cause never
# fully pinned down: something between PyInstaller COLLECT and the staged
# copy leaves the framework's versioned directory missing even though the
# symlink itself resolved at check time). The only check that actually
# proves the binary works is running it. `--diagnose` runs the real
# request handler and exits, so this doubles as an end-to-end smoke test of
# the exact bytes that get shipped in the .pkg.
echo "==> Smoke-testing staged host binary"
SMOKE_LOG="$BUILD/host_smoketest.log"
if ! "$PKGROOT$INSTALL_DIR/giddh-dsc-host" --diagnose >"$SMOKE_LOG" 2>&1; then
  echo "ERROR: staged host binary at $PKGROOT$INSTALL_DIR/giddh-dsc-host failed to run."
  echo "       This is the exact binary that would ship in the .dmg — the build"
  echo "       must not continue. Output:"
  cat "$SMOKE_LOG" || true
  exit 1
fi
echo "    OK: $(cat "$SMOKE_LOG" | head -c 200)"

# Bake the version into the app so the GUI can display it, then build the
# visible status/companion app (.app) and stage it under /Applications.
BUILDINFO="$ROOT/dsc-bridge/native-host/_buildinfo.py"
echo "VERSION = \"$VERSION\"" > "$BUILDINFO"
trap 'rm -f "$BUILDINFO"' EXIT

echo "==> Building status app (Giddh DSC Bridge.app)"
( cd "$ROOT" && VERSION="$VERSION" python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_status.spec )

# Same COLLECT race as the host: wait for the BUNDLE .app and its embedded
# Python.framework to be fully written before staging.
sync
APP_OUT="$ROOT/dist/Giddh DSC Bridge.app"
APP_BIN="$APP_OUT/Contents/MacOS/giddh-dsc-status"
if [ ! -f "$APP_BIN" ]; then
  echo "ERROR: PyInstaller did not produce $APP_BIN"
  exit 1
fi
APP_PY_SHLIB=$(find "$APP_OUT/Contents/Frameworks" -maxdepth 8 -type l \( -name 'Python' -o -name 'Python3' \) 2>/dev/null | head -1)
if [ -z "$APP_PY_SHLIB" ] || [ ! -e "$APP_PY_SHLIB" ]; then
  echo "ERROR: PyInstaller did not write the Python shared library inside the .app"
  echo "       Contents/Frameworks:"
  ls -la "$APP_OUT/Contents/Frameworks" 2>&1 | head -10 || true
  exit 1
fi

mkdir -p "$PKGROOT/Applications"
cp -R "$APP_OUT" "$PKGROOT/Applications/"
sync

echo "==> Writing native-messaging manifest (ext id: $EXT_ID)"
cat > "$PKGROOT$INSTALL_DIR/$HOST_NAME.json" <<EOF
{
  "name": "$HOST_NAME",
  "description": "Giddh DSC Bridge — PKCS#11 token signing",
  "path": "$INSTALL_DIR/giddh-dsc-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF

echo "==> Generating preinstall (wipes any previous install to avoid stale-file debris)"
cp "$HERE/scripts/preinstall" "$SCRIPTS/preinstall"
chmod +x "$SCRIPTS/preinstall"

echo "==> Generating postinstall (registers manifest for all Chromium browsers)"
cp "$HERE/scripts/postinstall" "$SCRIPTS/postinstall"
chmod +x "$SCRIPTS/postinstall"

echo "==> Building component pkg"
# pkgbuild marks .app/.framework bundles as RELOCATABLE by default, so the
# installer uses Spotlight to place them — which can silently divert the app to
# a stray copy's location instead of /Applications. Force fixed locations by
# emitting a component plist with BundleIsRelocatable=false for every bundle.
COMPONENT_PLIST="$BUILD/component.plist"
pkgbuild --analyze --root "$PKGROOT" "$COMPONENT_PLIST"
/usr/bin/python3 - "$COMPONENT_PLIST" <<'PY'
import plistlib, sys
path = sys.argv[1]
with open(path, "rb") as f:
    data = plistlib.load(f)
for comp in data:
    comp["BundleIsRelocatable"] = False
with open(path, "wb") as f:
    plistlib.dump(data, f)
PY

pkgbuild \
  --root "$PKGROOT" \
  --component-plist "$COMPONENT_PLIST" \
  --scripts "$SCRIPTS" \
  --identifier "$HOST_NAME" \
  --version "$VERSION" \
  --install-location "/" \
  "$DMGROOT/GiddhDSCBridge.pkg"

cp "$ROOT/packaging/macos/DMG_README.txt" "$DMGROOT/READ ME FIRST.txt" 2>/dev/null || true

echo "==> Building dmg"
hdiutil create \
  -volname "Giddh DSC Bridge" \
  -srcfolder "$DMGROOT" \
  -ov -format UDZO \
  "$OUT/GiddhDSCBridge-$VERSION.dmg"

# The loose PyInstaller .app in dist/ is already captured inside the pkg/dmg.
# Remove it so LaunchServices/Spotlight don't index a stray, non-installed copy
# (which would still launch via cmd+space after an uninstall).
rm -rf "$OUT/Giddh DSC Bridge.app"

echo "==> Done: $OUT/GiddhDSCBridge-$VERSION.dmg"
