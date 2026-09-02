#!/bin/bash
# Build the macOS installer: freezes the native host, wraps it in a signed-able
# .pkg that registers the Chrome native-messaging manifest, then packages the
# .pkg inside a distributable .dmg.
#
# When Apple signing identities are available (CI keychain or local keychain),
# binaries/apps are codesigned with Developer ID Application and the .pkg is
# productsign'd with Developer ID Installer. When APPLE_ID /
# APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID are set, the signed .pkg is also
# submitted to Apple's notary service and stapled.
#
# Apple rejects an unsigned .pkg during notarization, so without a Developer ID
# Installer certificate the .pkg (and the DMG around it) ships un-notarized and
# users have to right-click -> Open on first install.
#
# Requirements (provided by the macOS CI runner): python3, pyinstaller,
# pkgbuild, productsign, hdiutil (all ship with macOS + the pip deps installed
# by CI).
#
# Inputs (env):
#   VERSION                      package version (default: contents of ../../VERSION)
#   GIDDH_EXT_ID                 published extension ID (default: ../extension-id.txt)
#   APPLE_SIGNING_IDENTITY       Developer ID Application (auto-detected if unset)
#   APPLE_INSTALLER_IDENTITY     Developer ID Installer (auto-detected if unset)
#   APPLE_ID                     Apple ID for notarytool (optional)
#   APPLE_APP_SPECIFIC_PASSWORD  app-specific password for notarytool (optional)
#   APPLE_TEAM_ID                Apple Team ID for notarytool (optional)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEFAULT_VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION" 2>/dev/null || echo 1.0.0)"
VERSION="${VERSION:-$DEFAULT_VERSION}"
EXT_ID="${GIDDH_EXT_ID:-$(tr -d '[:space:]' < "$ROOT/packaging/extension-id.txt")}"
HOST_NAME="com.giddh.dsc.bridge"
INSTALL_DIR="/usr/local/giddh-dsc-bridge"

# Auto-detect signing identities from the active keychain when not provided.
# Developer ID Installer certificates are not valid for the codesigning
# policy, so fall back to the default policy listing before giving up.
find_signing_identity() {
  local pattern="$1"
  local found
  found=$(
    security find-identity -v -p macappstore 2>/dev/null |
      grep "$pattern" |
      head -n 1 |
      awk -F '"' '{print $2}' || true
  )
  if [ -z "$found" ]; then
    found=$(
      security find-identity -v 2>/dev/null |
        grep "$pattern" |
        head -n 1 |
        awk -F '"' '{print $2}' || true
    )
  fi
  printf '%s' "$found"
}

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
  APPLE_SIGNING_IDENTITY="$(find_signing_identity 'Developer ID Application')"
fi
if [ -z "${APPLE_INSTALLER_IDENTITY:-}" ]; then
  APPLE_INSTALLER_IDENTITY="$(find_signing_identity 'Developer ID Installer')"
fi

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> Using Developer ID Application: $APPLE_SIGNING_IDENTITY"
else
  echo "==> No Developer ID Application identity found — binaries will be unsigned"
fi
if [ -n "${APPLE_INSTALLER_IDENTITY:-}" ]; then
  echo "==> Using Developer ID Installer: $APPLE_INSTALLER_IDENTITY"
else
  echo "==> No Developer ID Installer identity found"
fi

# Apple only notarizes a .pkg that was productsign'd with a Developer ID
# Installer certificate. Without that certificate the .pkg still installs
# correctly, it just cannot be notarized — so the build keeps shipping it and
# the notarization/stapling steps are skipped instead.
if [ -z "${APPLE_INSTALLER_IDENTITY:-}" ]; then
  echo "==> WARNING: .pkg will be UNSIGNED and cannot be notarized."
  echo "             Users must right-click the .pkg -> Open on first install."
fi

# Apple notarization requires EVERY nested Mach-O (shared libraries, .so
# extension modules, the embedded Python framework binary) to carry a
# Developer ID signature with hardened runtime and a secure timestamp before
# the enclosing bundle or installer is submitted. PyInstaller does not sign
# them, so sign inside-out before signing the enclosing bundle.
sign_nested_binaries() {
  local target="$1"
  [ -n "${APPLE_SIGNING_IDENTITY:-}" ] || return 0
  [ -e "$target" ] || return 0

  while IFS= read -r lib; do
    echo "    signing nested binary: $lib"
    codesign \
      --force \
      --options runtime \
      --timestamp \
      --sign "$APPLE_SIGNING_IDENTITY" \
      "$lib"
  done < <(find "$target" -type f \( -name '*.so' -o -name '*.dylib' -o -name 'Python' -o -name 'Python3' \))
}

BUILD="$ROOT/packaging/macos/build"
PKGROOT="$BUILD/pkgroot"
SCRIPTS="$BUILD/scripts"
DMGROOT="$BUILD/dmgroot"
OUT="$ROOT/dist"
rm -rf "$BUILD"; mkdir -p "$PKGROOT$INSTALL_DIR" "$SCRIPTS" "$DMGROOT" "$OUT"

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

# onedir output is a folder; install its contents into INSTALL_DIR so the
# executable lands at $INSTALL_DIR/giddh-dsc-host with libs in _internal/ beside it.
cp -R "$HOST_OUT/." "$PKGROOT$INSTALL_DIR/"
chmod +x "$PKGROOT$INSTALL_DIR/giddh-dsc-host"
sync

echo "==> Smoke-testing staged host binary"
SMOKE_LOG="$BUILD/host_smoketest.log"
if ! "$PKGROOT$INSTALL_DIR/giddh-dsc-host" --diagnose >"$SMOKE_LOG" 2>&1; then
  echo "ERROR: staged host binary at $PKGROOT$INSTALL_DIR/giddh-dsc-host failed to run."
  echo "       This is the exact binary that would ship in the .pkg — the build"
  echo "       must not continue. Output:"
  cat "$SMOKE_LOG" || true
  exit 1
fi
echo "    OK: $(cat "$SMOKE_LOG" | head -c 200)"

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
  echo "==> Deep-signing staged native host and all internal dependencies"
  sign_nested_binaries "$PKGROOT$INSTALL_DIR/_internal"

  codesign \
    --force \
    --verbose \
    --options runtime \
    --timestamp \
    --sign "$APPLE_SIGNING_IDENTITY" \
    "$PKGROOT$INSTALL_DIR/giddh-dsc-host"
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
  echo "==> Deep-signing status app (Giddh DSC Bridge.app)"
  sign_nested_binaries "$APP_OUT"

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
UNSIGNED_PKG="$BUILD/GiddhDSCBridge-unsigned.pkg"
SIGNED_PKG="$OUT/GiddhDSCBridge-$VERSION.pkg"

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
  "$UNSIGNED_PKG"

if [ -n "${APPLE_INSTALLER_IDENTITY:-}" ]; then
  echo "==> Signing .pkg with Developer ID Installer"
  productsign \
    --sign "$APPLE_INSTALLER_IDENTITY" \
    "$UNSIGNED_PKG" \
    "$SIGNED_PKG"

  echo "==> Verifying .pkg signature"
  pkgutil --check-signature "$SIGNED_PKG"
else
  echo "==> Shipping UNSIGNED .pkg (no Developer ID Installer identity)"
  cp "$UNSIGNED_PKG" "$SIGNED_PKG"
fi

# Notarize the signed .pkg when Apple notary credentials are present.
if [ -n "${APPLE_INSTALLER_IDENTITY:-}" ] && [ -n "${APPLE_ID:-}" ] && \
   [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
  echo "==> Submitting .pkg to Apple Notary Service"
  set +e
  SUBMIT_OUTPUT=$(xcrun notarytool submit "$SIGNED_PKG" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait 2>&1)
  SUBMIT_EXIT=$?
  set -e
  echo "$SUBMIT_OUTPUT"

  if [ $SUBMIT_EXIT -ne 0 ] || ! echo "$SUBMIT_OUTPUT" | grep -q "status: Accepted"; then
    echo "ERROR: Apple notarization failed for .pkg"
    SUBMISSION_ID=$(echo "$SUBMIT_OUTPUT" | awk '/id:/ {print $2; exit}')
    if [ -n "${SUBMISSION_ID:-}" ]; then
      xcrun notarytool log "$SUBMISSION_ID" \
        --apple-id "$APPLE_ID" \
        --password "$APPLE_APP_SPECIFIC_PASSWORD" \
        --team-id "$APPLE_TEAM_ID" || true
    fi
    exit 1
  fi

  echo "==> Stapling notarization ticket to .pkg"
  xcrun stapler staple "$SIGNED_PKG"
  xcrun stapler validate "$SIGNED_PKG"
else
  echo "==> Skipping .pkg notarization"
fi

echo "==> Staging DMG contents"
cp "$SIGNED_PKG" "$DMGROOT/GiddhDSCBridge.pkg"
cp "$ROOT/packaging/macos/DMG_README.txt" "$DMGROOT/READ ME FIRST.txt" 2>/dev/null || true

echo "==> Building dmg"
hdiutil create \
  -volname "Giddh DSC Bridge" \
  -srcfolder "$DMGROOT" \
  -ov -format UDZO \
  "$OUT/GiddhDSCBridge-$VERSION.dmg"

# The loose PyInstaller .app in dist/ is already captured inside the pkg/dmg.
# Remove it so LaunchServices/Spotlight don't index a stray, non-installed copy.
rm -rf "$OUT/Giddh DSC Bridge.app"

echo "==> Done: $SIGNED_PKG"
echo "==> Done: $OUT/GiddhDSCBridge-$VERSION.dmg"
