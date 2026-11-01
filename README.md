# Twitch-Live-Monitor für Raspberry Pi

Der Dienst prüft, ob einer der eingetragenen Twitch-Kanäle live ist. Für jeden Live-Kanal öffnet er ein eigenes Fenster mit der Twitch-Kanalseite. Sobald Twitch den Kanal als offline meldet, schließt der Dienst das von ihm gestartete Fenster. Standardmäßig fragt er den Status alle 15 Sekunden ab.

Der Monitor verwendet die Twitch-Helix-Schnittstelle `Get Streams` mit einem App-Token oder einem User-Token. Er braucht keine Berechtigung, den Kanal zu verwalten. Geöffnet wird die normale Twitch-Kanalseite in einem eigenen Browserprofil. Dort kannst du dich bei Twitch anmelden; die Wiedergabe steuert Twitch selbst.

## Voraussetzungen

- Raspberry Pi OS mit grafischer Oberfläche und Internetverbindung
- Python 3
- Chromium oder Firefox
- Eine Twitch-Entwickleranwendung mit Client ID und entweder Client Secret oder einem Paar aus User-Access-Token und Refresh-Token

## Twitch-Anwendung vorbereiten

Erstelle eine Anwendung in der [Twitch Developer Console](https://dev.twitch.tv/console/apps) und notiere ihre Client ID. Für den Zugriff auf die Entwicklerkonsole verlangt Twitch eine aktivierte Zwei-Faktor-Authentifizierung. Wenn beim Erstellen eine Weiterleitungs-URL verlangt wird, trage `http://localhost:3000` ein. Der Monitor verwendet keine interaktive Twitch-Anmeldung.

## Installation

Entpacke den Ordner auf dem Pi, öffne darin ein Terminal und führe Folgendes aus:

    chmod +x install.sh
    ./install.sh

Öffne anschließend die Konfigurationsdatei:

    nano ~/.config/twitch-live-browser/config.env

Trage `TWITCH_CLIENT_ID` und eine Anmeldemethode ein. Für den App-Token-Modus brauchst du `TWITCH_CLIENT_SECRET`. Für den User-Token-Modus entfernst du die Platzhalterzeile für das Client Secret und setzt sowohl `TWITCH_ACCESS_TOKEN` als auch `TWITCH_REFRESH_TOKEN`. Schreibe jeden Wert in eine eigene Zeile und verwende keine Anführungszeichen.

Starte den Dienst in der aktuellen grafischen Sitzung:

    ~/.local/share/twitch-live-browser/start-from-desktop.sh

Das Installationsskript legt außerdem einen Autostart-Eintrag auf dem Desktop an. Der Monitor startet damit bei der nächsten Anmeldung an der grafischen Oberfläche automatisch. Den Dienststatus prüfst du mit:

    systemctl --user status twitch-live-browser.service

Aktuelle Meldungen zeigt dieser Befehl:

    journalctl --user -u twitch-live-browser.service -f

## Einstellungen

Die Datei `~/.config/twitch-live-browser/config.env` unterstützt folgende Werte:

- `TWITCH_CHANNELS`: durch Kommas getrennte Twitch-Logins. Die Beispielkonfiguration überwacht `timur18o4` und `frau_potti`.
- `TWITCH_CHANNEL`: ältere Einstellung für einen einzelnen Kanal; wird verwendet, wenn `TWITCH_CHANNELS` fehlt.
- `CHECK_INTERVAL_SECONDS`: Abstand zwischen Statusabfragen in Sekunden; Standardwert 15, Mindestwert 10.
- `TWITCH_CLIENT_ID`: erforderliche Client ID der Twitch-Anwendung.
- `TWITCH_CLIENT_SECRET`: Client Secret für den App-Token-Modus; im User-Token-Modus optional.
- `TWITCH_ACCESS_TOKEN` und `TWITCH_REFRESH_TOKEN`: optionales Token-Paar für den User-Token-Modus.

Eine Statusänderung wird spätestens nach einem Prüfintervall zuzüglich der Antwortzeit der Twitch-Schnittstelle erkannt. Bei vorübergehenden Problemen mit Twitch oder der Netzwerkverbindung behält der Monitor den aktuellen Browserstatus bei und versucht es erneut.

Im User-Token-Modus prüft der Dienst das Access-Token beim Start und danach stündlich. Bei Bedarf erneuert er es. Twitch kann Refresh-Tokens austauschen; jedes neue Token-Paar wird mit Dateirechten gespeichert, die nur dem Pi-Benutzer Zugriff erlauben, unter `~/.local/share/twitch-live-browser/token_state.json`. Eine öffentliche Twitch-Anwendung kann Refresh-Tokens ohne Client Secret erneuern; bei einer vertraulichen Anwendung kann ein Client Secret erforderlich sein.

Der bestehende Kanal `timur18o4` verwendet weiterhin sein Browserprofil unter `~/.local/share/twitch-live-browser/browser-profile`. Für weitere Kanäle erstellt der Monitor getrennte Profile mit Namen wie `~/.local/share/twitch-live-browser/browser-profile-frau_potti`. Dadurch kommen sich die Browserfenster nicht in die Quere.

## Dienst anhalten oder entfernen

Bis zur nächsten Anmeldung kannst du den Dienst so anhalten:

    systemctl --user stop twitch-live-browser.service

Um den Autostart-Eintrag und die installierten Programmdateien zu entfernen, führe `./uninstall.sh` im entpackten Ordner aus. Konfiguration und Browserprofile bleiben dabei erhalten.
