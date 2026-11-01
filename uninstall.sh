#!/bin/sh
set -eu

systemctl --user stop twitch-live-browser.service 2>/dev/null || true
rm -f "$HOME/.config/autostart/twitch-live-browser.desktop"
rm -f "$HOME/.config/systemd/user/twitch-live-browser.service"
rm -f "$HOME/.local/share/twitch-live-browser/monitor.py"
rm -f "$HOME/.local/share/twitch-live-browser/start-from-desktop.sh"
systemctl --user daemon-reload
rmdir "$HOME/.local/share/twitch-live-browser" 2>/dev/null || true
printf '%s\n' "Removed the service and program files. Configuration and browser profile were kept."
