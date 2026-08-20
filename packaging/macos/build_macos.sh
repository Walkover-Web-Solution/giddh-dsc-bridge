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
DEFAULT_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || echo 1.6.0)"
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
# onedir output is a folder; install its contents into INSTALL_DIR so the
# executable lands at $INSTALL_DIR/giddh-dsc-host with libs in _internal/ beside it.
cp -R "$ROOT/dist/giddh-dsc-host/." "$PKGROOT$INSTALL_DIR/"
chmod +x "$PKGROOT$INSTALL_DIR/giddh-dsc-host"

# Bake the version into the app so the GUI can display it, then build the
# visible status/companion app (.app) and stage it under /Applications.
BUILDINFO="$ROOT/dsc-bridge/native-host/_buildinfo.py"
echo "VERSION = \"$VERSION\"" > "$BUILDINFO"
trap 'rm -f "$BUILDINFO"' EXIT

echo "==> Building status app (Giddh DSC Bridge.app)"
( cd "$ROOT" && VERSION="$VERSION" python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_status.spec )
mkdir -p "$PKGROOT/Applications"
cp -R "$ROOT/dist/Giddh DSC Bridge.app" "$PKGROOT/Applications/"

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
