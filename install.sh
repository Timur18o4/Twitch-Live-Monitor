#!/bin/sh
set -eu

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_DIR="$HOME/.local/share/twitch-live-browser"
CONFIG_DIR="$HOME/.config/twitch-live-browser"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"

mkdir -p "$APP_DIR" "$CONFIG_DIR" "$UNIT_DIR" "$AUTOSTART_DIR"
install -m 0755 "$SOURCE_DIR/monitor.py" "$APP_DIR/monitor.py"
install -m 0755 "$SOURCE_DIR/start-from-desktop.sh" "$APP_DIR/start-from-desktop.sh"
install -m 0644 "$SOURCE_DIR/twitch-live-browser.service" "$UNIT_DIR/twitch-live-browser.service"

if [ ! -f "$CONFIG_DIR/config.env" ]; then
    install -m 0600 "$SOURCE_DIR/config.env.example" "$CONFIG_DIR/config.env"
fi

cat > "$AUTOSTART_DIR/twitch-live-browser.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Twitch-Live-Monitor
Comment=Öffnet den Twitch-Kanal, sobald der Stream beginnt
Exec="$APP_DIR/start-from-desktop.sh"
Terminal=false
X-GNOME-Autostart-enabled=true
EOF

chmod 0600 "$CONFIG_DIR/config.env"
systemctl --user daemon-reload

printf '%s\n' "Installation abgeschlossen."
printf '%s\n' "Bearbeite $CONFIG_DIR/config.env und trage dort deine Twitch-Anmeldedaten ein."
printf '%s\n' "Starte den Dienst anschließend mit: $APP_DIR/start-from-desktop.sh"
printf '%s\n' "Bei der nächsten Anmeldung an der grafischen Oberfläche startet er automatisch."
