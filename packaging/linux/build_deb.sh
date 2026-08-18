#!/bin/bash
# Build the Linux .deb: freezes the native host, lays it out under
# /opt/giddh-dsc-bridge, and ships a postinst that registers the
# native-messaging manifest system-wide for Chromium-family browsers.
#
# Requirements (Linux CI runner): python3, pyinstaller, dpkg-deb.
#
# Inputs (env):
#   VERSION        package version         (default: 1.3.0)
#   GIDDH_EXT_ID   published extension ID  (default: contents of ../extension-id.txt)
#
# NOTE: the visible companion app is a Tkinter GUI, so the build runner needs
# python3-tk installed (apt-get install -y python3-tk) for PyInstaller to bundle
# it. Without it, only the headless host is packaged.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VERSION="${VERSION:-1.3.0}"
EXT_ID="${GIDDH_EXT_ID:-$(tr -d '[:space:]' < "$ROOT/packaging/extension-id.txt")}"
HOST_NAME="com.giddh.dsc.bridge"
INSTALL_DIR="/opt/giddh-dsc-bridge"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

BUILD="$ROOT/packaging/linux/build"
PKG="$BUILD/giddh-dsc-bridge_${VERSION}_${ARCH}"
OUT="$ROOT/dist"
rm -rf "$BUILD"; mkdir -p "$PKG/DEBIAN" "$PKG$INSTALL_DIR" "$OUT"

echo "==> Freezing native host with PyInstaller (onedir)"
( cd "$ROOT" && python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_host.spec )
# onedir output is a folder; install its contents so the executable lands at
# $INSTALL_DIR/giddh-dsc-host with libs in _internal/ beside it.
cp -R "$ROOT/dist/giddh-dsc-host/." "$PKG$INSTALL_DIR/"
chmod +x "$PKG$INSTALL_DIR/giddh-dsc-host"

# Bake the version in, then freeze the visible companion app and ship it under
# $INSTALL_DIR/status/ with an app-menu .desktop entry. Requires python3-tk.
BUILDINFO="$ROOT/dsc-bridge/native-host/_buildinfo.py"
echo "VERSION = \"$VERSION\"" > "$BUILDINFO"
trap 'rm -f "$BUILDINFO"' EXIT

if ( cd "$ROOT" && VERSION="$VERSION" python3 -m PyInstaller --clean --noconfirm packaging/pyinstaller/giddh_dsc_status.spec ) \
   && [ -x "$ROOT/dist/giddh-dsc-status/giddh-dsc-status" ]; then
  echo "==> Bundling companion app + .desktop entry"
  mkdir -p "$PKG$INSTALL_DIR/status" "$PKG/usr/share/applications"
  cp -R "$ROOT/dist/giddh-dsc-status/." "$PKG$INSTALL_DIR/status/"
  chmod +x "$PKG$INSTALL_DIR/status/giddh-dsc-status"
  cat > "$PKG/usr/share/applications/giddh-dsc-bridge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Giddh DSC Bridge
Comment=DSC token status, check, and uninstall
Exec=$INSTALL_DIR/status/giddh-dsc-status
Terminal=false
Categories=Utility;
EOF
else
  echo "!! python3-tk missing or status build failed — packaging headless host only"
fi

echo "==> Writing native-messaging manifest (ext id: $EXT_ID)"
cat > "$PKG$INSTALL_DIR/$HOST_NAME.json" <<EOF
{
  "name": "$HOST_NAME",
  "description": "Giddh DSC Bridge — PKCS#11 token signing",
  "path": "$INSTALL_DIR/giddh-dsc-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$EXT_ID/"]
}
EOF

echo "==> Writing DEBIAN control + maintainer scripts"
INSTALLED_KB="$(du -sk "$PKG$INSTALL_DIR" | cut -f1)"
sed -e "s/@VERSION@/$VERSION/g" -e "s/@ARCH@/$ARCH/g" -e "s/@SIZE@/$INSTALLED_KB/g" \
    "$HERE/debian/control.in" > "$PKG/DEBIAN/control"
cp "$HERE/debian/postinst" "$PKG/DEBIAN/postinst"
cp "$HERE/debian/prerm"    "$PKG/DEBIAN/prerm"
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

echo "==> Building deb"
dpkg-deb --build --root-owner-group "$PKG" "$OUT/giddh-dsc-bridge_${VERSION}_${ARCH}.deb"

echo "==> Done: $OUT/giddh-dsc-bridge_${VERSION}_${ARCH}.deb"
