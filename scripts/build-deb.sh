#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
VERSION=${1:-0.1.0}
ARCH=${2:-$(dpkg --print-architecture)}
BUILD_ROOT=${PROJECT_ROOT}/build/deb
PACKAGE_ROOT=${BUILD_ROOT}/codex-indicator_${VERSION}_${ARCH}
OUTPUT=${PROJECT_ROOT}/dist/codex-indicator_${VERSION}_${ARCH}.deb

rm -rf "$BUILD_ROOT"
install -d \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/codex-indicator" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PROJECT_ROOT/dist"

cp -R "$PROJECT_ROOT/src/codex_indicator" "$PACKAGE_ROOT/usr/lib/codex-indicator/"
find "$PACKAGE_ROOT/usr/lib/codex-indicator" -type f -name '*.pyc' -delete
find "$PACKAGE_ROOT/usr/lib/codex-indicator" -type d -name '__pycache__' -empty -delete

{
    printf '%s\n' 'Package: codex-indicator'
    printf 'Version: %s\n' "$VERSION"
    printf 'Architecture: %s\n' "$ARCH"
    printf '%s\n' 'Maintainer: Heimao-c'
    printf '%s\n' 'Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, libayatana-appindicator3-1'
    printf '%s\n' 'Section: devel'
    printf '%s\n' 'Priority: optional'
    printf '%s\n' 'Homepage: https://github.com/Heimao-c/codex-indicator'
    printf '%s\n' 'Description: Minimal system-tray status for local Codex CLI sessions'
    printf '%s\n' ' Shows state, project, and conversation title without storing prompts or replies.'
} > "$PACKAGE_ROOT/DEBIAN/control"

{
    printf '%s\n' '#!/bin/sh'
    printf '%s\n' 'CODEX_INDICATOR_LAUNCHER=/usr/bin/codex-indicator PYTHONPATH=/usr/lib/codex-indicator exec /usr/bin/python3 -m codex_indicator "$@"'
} > "$PACKAGE_ROOT/usr/bin/codex-indicator"
chmod 0755 "$PACKAGE_ROOT/usr/bin/codex-indicator"

{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Type=Application'
    printf '%s\n' 'Name=Codex Indicator'
    printf '%s\n' 'Exec=codex-indicator'
    printf '%s\n' 'Icon=codex-indicator-symbolic'
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'Categories=Development;Utility;'
    printf '%s\n' 'Comment=Show local Codex CLI session status'
} > "$PACKAGE_ROOT/usr/share/applications/codex-indicator.desktop"

install -m 0644 "$PROJECT_ROOT/src/codex_indicator/assets/codex-indicator-symbolic.svg" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/codex-indicator-symbolic.svg"
install -m 0644 "$PROJECT_ROOT/src/codex_indicator/assets/codex-indicator-attention.svg" \
    "$PACKAGE_ROOT/usr/share/icons/hicolor/scalable/apps/codex-indicator-attention.svg"

dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT"
printf '%s\n' "$OUTPUT"
