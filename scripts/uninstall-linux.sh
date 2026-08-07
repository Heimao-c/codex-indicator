#!/bin/sh
set -eu

DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_PATH=${HOME}/.local/bin/codex-indicator

if [ -x "$BIN_PATH" ]; then
    "$BIN_PATH" --uninstall-hooks || true
    if [ -f "$HOME/.config/systemd/user/codex-indicator.service" ]; then
        systemctl --user stop codex-indicator.service || true
    fi
    "$BIN_PATH" --uninstall-autostart || true
fi

rm -f "$BIN_PATH"
rm -f "$DATA_HOME/applications/codex-indicator.desktop"
for icon in symbolic attention working done idle; do
    rm -f "$DATA_HOME/icons/hicolor/scalable/apps/codex-indicator-${icon}.svg"
done
rm -rf "$DATA_HOME/codex-indicator"

printf '%s\n' 'CC Indicator was removed. Session-status cache was kept in the platform state directory.'
