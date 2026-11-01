#!/bin/sh
set -eu

CONFIG_FILE="$HOME/.config/twitch-live-browser/config.env"
HAS_CLIENT_ID=no
HAS_CLIENT_SECRET=no
HAS_USER_TOKEN_PAIR=no
if [ -f "$CONFIG_FILE" ]; then
    if grep -Eq '^TWITCH_CLIENT_ID=[^[:space:]#]+$' "$CONFIG_FILE" && ! grep -q '^TWITCH_CLIENT_ID=replace_with_' "$CONFIG_FILE"; then
        HAS_CLIENT_ID=yes
    fi
    if grep -Eq '^TWITCH_CLIENT_SECRET=[^[:space:]#]+$' "$CONFIG_FILE" && ! grep -q '^TWITCH_CLIENT_SECRET=replace_with_' "$CONFIG_FILE"; then
        HAS_CLIENT_SECRET=yes
    fi
    if grep -Eq '^TWITCH_ACCESS_TOKEN=[^[:space:]#]+$' "$CONFIG_FILE" && grep -Eq '^TWITCH_REFRESH_TOKEN=[^[:space:]#]+$' "$CONFIG_FILE"; then
        HAS_USER_TOKEN_PAIR=yes
    fi
fi
if [ "$HAS_CLIENT_ID" != yes ] || { [ "$HAS_CLIENT_SECRET" != yes ] && [ "$HAS_USER_TOKEN_PAIR" != yes ]; }; then
    echo "Twitch credentials are not configured yet; skipping startup."
    exit 0
fi

# Pass the graphical session environment to the per-user systemd manager.
systemctl --user import-environment 2>/dev/null || true
systemctl --user restart twitch-live-browser.service
