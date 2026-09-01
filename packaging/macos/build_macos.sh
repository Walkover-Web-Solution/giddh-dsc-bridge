#!/bin/bash
# Build the macOS distributable: freeze the native host, bundle it inside the
# signed companion .app, then package the .app inside a .dmg.
#
# No .pkg / Developer ID Installer certificate is required. The companion app
# installs the native host into the user's Application Support folder on first
# launch.
#
# Inputs (env):
#   VERSION        package version         (default: contents of ../../VERSION)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ../extension-id.txt)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEFAULT_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || echo 1.0.0)"
VERSION="${VERSION:-$DEFAULT_VERSION}"
EXT_ID="${GIDDH_EXT_ID:-$(tr -d '[:space:]' < "$ROOT/packaging/extension-id.txt")}"
HOST_NAME="com.giddh.dsc.bridge"

BUILD="$ROOT/packaging/macos/build"
DMGROOT="$BUILD/dmgroot"
OUT="$ROOT/dist"
rm -rf "$BUILD"; mkdir -p "$DMGROOT" "$OUT"

echo "==> Freezing native host with PyInstaller (onedir)"
( cd "$ROOT" && python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_host.spec )

# COLLECT can return before all framework binaries finish being written to disk.
sync
HOST_OUT="$ROOT/dist/giddh-dsc-host"
HOST_BIN="$HOST_OUT/giddh-dsc-host"
if [ ! -f "$HOST_BIN" ]; then
  echo "ERROR: PyInstaller did not produce $HOST_BIN"
  exit 1
fi
PY_SHLIB=$(find "$HOST_OUT/_internal" -maxdepth 6 -type l \( -name 'Python' -o -name 'Python3' \) 2>/dev/null | head -1)
if [ -z "$PY_SHLIB" ] || [ ! -e "$PY_SHLIB" ]; then
  echo "ERROR: PyInstaller did not write the Python shared library (looked for"
  echo "       _internal/Python{,3} symlinks in $HOST_OUT/_internal)"
  exit 1
fi

echo "==> Smoke-testing frozen host binary"
SMOKE_LOG="$BUILD/host_smoketest.log"
if ! "$HOST_BIN" --diagnose >"$SMOKE_LOG" 2>&1; then
  echo "ERROR: frozen host binary failed to run. Output:"
  cat "$SMOKE_LOG" || true
  exit 1
fi
echo "    OK: $(cat "$SMOKE_LOG" | head -c 200)"

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> Signing native host binary"
  codesign \
    --force \
    --verbose \
    --options runtime \
    --timestamp \
    --sign "$APPLE_SIGNING_IDENTITY" \
    "$HOST_BIN"
fi

# Write build metadata so the status app can display the version and register
# the correct extension id in the native-messaging manifest.
BUILDINFO="$ROOT/dsc-bridge/native-host/_buildinfo.py"
cat > "$BUILDINFO" <<EOF
VERSION = "$VERSION"
GIDDH_EXT_ID = "$EXT_ID"
EOF
trap 'rm -f "$BUILDINFO"' EXIT

echo "==> Building status app (Giddh DSC Bridge.app)"
( cd "$ROOT" && VERSION="$VERSION" python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_status.spec )

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
  exit 1
fi

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> Signing status app (Giddh DSC Bridge.app)"
  codesign \
    --deep \
    --force \
    --verbose \
    --options runtime \
    --timestamp \
    --sign "$APPLE_SIGNING_IDENTITY" \
    "$APP_OUT"

  echo "==> Verifying status app signature"
  codesign \
    --verify \
    --deep \
    --strict \
    --verbose=2 \
    "$APP_OUT"
fi

echo "==> Staging DMG contents"
cp -R "$APP_OUT" "$DMGROOT/"
cp "$ROOT/packaging/macos/DMG_README.txt" "$DMGROOT/READ ME FIRST.txt" 2>/dev/null || true

echo "==> Building dmg"
hdiutil create \
  -volname "Giddh DSC Bridge" \
  -srcfolder "$DMGROOT" \
  -ov -format UDZO \
  "$OUT/GiddhDSCBridge-$VERSION.dmg"

# The loose PyInstaller .app in dist/ is already captured inside the dmg.
# Remove it so LaunchServices/Spotlight don't index a stray, non-installed copy.
rm -rf "$OUT/Giddh DSC Bridge.app"

echo "==> Done: $OUT/GiddhDSCBridge-$VERSION.dmg"
