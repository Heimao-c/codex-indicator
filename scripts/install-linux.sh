#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_HOME=${HOME}/.local/bin
APP_ROOT=${DATA_HOME}/codex-indicator/app
ICON_HOME=${DATA_HOME}/icons/hicolor/scalable/apps
APPLICATION_HOME=${DATA_HOME}/applications
BIN_PATH=${BIN_HOME}/codex-indicator

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("AyatanaAppIndicator3", "0.1"); gi.require_version("Atspi", "2.0")' 2>/dev/null \
    || ! command -v xwininfo >/dev/null 2>&1 \
    || ! command -v xprop >/dev/null 2>&1; then
    printf '%s\n' 'Missing Ubuntu tray dependencies.'
    printf '%s\n' 'Install them with:'
    printf '%s\n' '  sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-atspi-2.0 libayatana-appindicator3-1 libx11-6 libxtst6 x11-utils'
    exit 2
fi

install -d "$APP_ROOT" "$BIN_HOME" "$ICON_HOME" "$APPLICATION_HOME"
cp -R "$PROJECT_ROOT/src/codex_indicator" "$APP_ROOT/"
find "$APP_ROOT/codex_indicator" -type f -name '*.pyc' -delete
find "$APP_ROOT/codex_indicator" -type d -name '__pycache__' -empty -delete

{
    printf '%s\n' '#!/bin/sh'
    printf '%s\n' 'APP_ROOT=${XDG_DATA_HOME:-"$HOME/.local/share"}/codex-indicator/app'
    printf '%s\n' 'CODEX_INDICATOR_LAUNCHER="$HOME/.local/bin/codex-indicator" PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}" exec /usr/bin/python3 -m codex_indicator "$@"'
} > "$BIN_PATH"
chmod 0755 "$BIN_PATH"

for icon in "$PROJECT_ROOT"/src/codex_indicator/assets/*.svg; do
    install -m 0644 "$icon" "$ICON_HOME/$(basename "$icon")"
done

{
    printf '%s\n' '[Desktop Entry]'
    printf '%s\n' 'Type=Application'
    printf '%s\n' 'Name=Codex Indicator'
    printf 'Exec=%s\n' "$BIN_PATH"
    printf '%s\n' 'Icon=codex-indicator-symbolic'
    printf '%s\n' 'Terminal=false'
    printf '%s\n' 'Categories=Development;Utility;'
    printf '%s\n' 'Comment=Show and manage Codex CLI session status'
} > "$APPLICATION_HOME/codex-indicator.desktop"

"$BIN_PATH" --install-hooks
"$BIN_PATH" --install-autostart
if [ -f "$HOME/.config/systemd/user/codex-indicator.service" ]; then
    systemctl --user restart codex-indicator.service
else
    nohup "$BIN_PATH" >/dev/null 2>&1 &
fi

printf '%s\n' "Installed Codex Indicator at $APP_ROOT"
printf '%s\n' 'Open /hooks in each running Codex CLI and trust the new hooks when prompted.'
