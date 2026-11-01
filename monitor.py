#!/usr/bin/env python3
"""Öffnet für jeden konfigurierten Live-Kanal eine eigene Twitch-Seite."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("twitch-live-browser")
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
STREAMS_URL = "https://api.twitch.tv/helix/streams"
REQUEST_TIMEOUT_SECONDS = 15


def required_setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or is_placeholder(value):
        raise RuntimeError(f"Trage {name} in ~/.config/twitch-live-browser/config.env ein.")
    return value


def is_placeholder(value: str) -> bool:
    return value.lower().startswith(("replace_with_", "hier_eintragen_"))


def get_channels() -> list[str]:
    configured = os.environ.get("TWITCH_CHANNELS", "").strip()
    if not configured:
        configured = os.environ.get("TWITCH_CHANNEL", "timur18o4")
    channels = list(dict.fromkeys(item.strip().lower() for item in configured.split(",") if item.strip()))
    if not channels:
        raise RuntimeError("Trage mindestens einen Twitch-Login in TWITCH_CHANNELS oder TWITCH_CHANNEL ein.")
    if any(not re.fullmatch(r"[a-z0-9_]{1,25}", channel) for channel in channels):
        raise RuntimeError("TWITCH_CHANNELS darf nur durch Kommas getrennte Twitch-Logins enthalten.")
    return channels


class TwitchClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str | None,
        channels: list[str] | str,
        access_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.channels = [channels] if isinstance(channels, str) else list(channels)
        self.user_mode = bool(access_token and refresh_token)
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_valid_until = 0.0
        self.validate_again_at = 0.0
        self.initial_validation_pending = self.user_mode
        self.token_state_path = (
            Path.home() / ".local" / "share" / "twitch-live-browser" / "token_state.json"
        )
        if self.user_mode:
            self._load_user_token_state()

    def _get_app_token(self) -> str:
        if self.access_token and time.monotonic() < self.token_valid_until:
            return self.access_token

        if not self.client_secret:
            raise RuntimeError("Für den App-Token-Modus ist TWITCH_CLIENT_SECRET erforderlich.")
        body = urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            }
        ).encode("ascii")
        request = Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"Twitch-Token-Anfrage fehlgeschlagen (HTTP {exc.code}).") from None
        except URLError as exc:
            raise RuntimeError(f"Twitch-Token-Anfrage fehlgeschlagen: {exc.reason}") from None

        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Twitch hat kein Zugriffstoken zurückgegeben.")
        expires_in = int(payload.get("expires_in", 3600))
        self.access_token = token
        self.token_valid_until = time.monotonic() + max(0, expires_in - 60)
        return token

    def _load_user_token_state(self) -> None:
        if not self.token_state_path.is_file():
            return
        try:
            state = json.loads(self.token_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            LOG.warning("Der gespeicherte Twitch-Tokenstatus ist unlesbar; die konfigurierten Tokens werden verwendet.")
            return
        if state.get("client_id") != self.client_id:
            LOG.warning("Der gespeicherte Tokenstatus gehört zu einer anderen App; die konfigurierten Tokens werden verwendet.")
            return
        self.access_token = state.get("access_token") or self.access_token
        self.refresh_token = state.get("refresh_token") or self.refresh_token

    def _save_user_token_state(self) -> None:
        if not self.access_token or not self.refresh_token:
            return
        self.token_state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix="token_state.", dir=str(self.token_state_path.parent)
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as state_file:
                json.dump(
                    {
                        "client_id": self.client_id,
                        "access_token": self.access_token,
                        "refresh_token": self.refresh_token,
                    },
                    state_file,
                )
                state_file.write("\n")
            os.replace(temporary_path, self.token_state_path)
        except OSError as exc:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise RuntimeError("Erneuerte Twitch-Tokens konnten nicht sicher gespeichert werden.") from exc

    def _validate_user_token(self) -> bool:
        if not self.access_token:
            return False
        request = Request(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": f"OAuth {self.access_token}"},
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            if exc.code in (400, 401):
                return False
            raise RuntimeError(f"Twitch-Tokenprüfung fehlgeschlagen (HTTP {exc.code}).") from None
        except URLError as exc:
            raise RuntimeError(f"Twitch-Tokenprüfung fehlgeschlagen: {exc.reason}") from None

        if payload.get("client_id") != self.client_id:
            raise RuntimeError("TWITCH_CLIENT_ID passt nicht zum angegebenen Twitch-Access-Token.")
        now = time.monotonic()
        self.token_valid_until = now + max(0, int(payload.get("expires_in", 0)))
        self.validate_again_at = now + 3600
        return True

    def _refresh_user_token(self) -> str:
        if not self.refresh_token:
            raise RuntimeError("Zum Erneuern des User-Access-Tokens ist ein Twitch-Refresh-Token erforderlich.")
        fields = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        request = Request(
            TOKEN_URL,
            data=urlencode(fields).encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise RuntimeError(
                f"Twitch hat das Refresh-Token abgelehnt (HTTP {exc.code}); möglicherweise ist ein Client Secret erforderlich."
            ) from None
        except URLError as exc:
            raise RuntimeError(f"Erneuerung des Twitch-Tokens fehlgeschlagen: {exc.reason}") from None

        new_access = payload.get("access_token")
        new_refresh = payload.get("refresh_token")
        if not new_access or not new_refresh:
            raise RuntimeError("Twitch hat kein neues Paar aus Access-Token und Refresh-Token zurückgegeben.")
        self.access_token = new_access
        self.refresh_token = new_refresh
        now = time.monotonic()
        self.token_valid_until = now + max(0, int(payload.get("expires_in", 3600)) - 60)
        self.validate_again_at = now + 3600
        self._save_user_token_state()
        LOG.info("Twitch-User-Access-Token erneuert.")
        return new_access

    def _get_user_token(self) -> str:
        now = time.monotonic()
        if self.initial_validation_pending:
            self.initial_validation_pending = False
            if self._validate_user_token() and self.token_valid_until > time.monotonic() + 60:
                return self.access_token or ""
            return self._refresh_user_token()

        if not self.access_token or now >= self.token_valid_until - 60:
            return self._refresh_user_token()
        if now >= self.validate_again_at and not self._validate_user_token():
            return self._refresh_user_token()
        return self.access_token

    def _get_token(self) -> str:
        if self.user_mode:
            return self._get_user_token()
        return self._get_app_token()

    def live_channels(self) -> set[str]:
        query = urlencode([("user_login", channel) for channel in self.channels])
        for attempt in range(2):
            token = self._get_token()
            request = Request(
                f"{STREAMS_URL}?{query}",
                headers={
                    "Client-Id": self.client_id,
                    "Authorization": f"Bearer {token}",
                },
            )
            try:
                with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                    payload = json.load(response)
                streams = payload.get("data", [])
                return {
                    item.get("user_login", "").lower()
                    for item in streams
                    if item.get("type") == "live"
                }
            except HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    if self.user_mode:
                        self._refresh_user_token()
                    else:
                        self.access_token = None
                    continue
                raise RuntimeError(f"Twitch-Live-Statusabfrage fehlgeschlagen (HTTP {exc.code}).") from None
            except URLError as exc:
                raise RuntimeError(f"Twitch-Live-Statusabfrage fehlgeschlagen: {exc.reason}") from None
        raise RuntimeError("Twitch-Anmeldung schlug auch nach Erneuerung des App-Tokens fehl.")

    def is_live(self) -> bool:
        """Rückwärtskompatible Hilfsmethode für Aufrufe mit einem Kanal."""
        return bool(self.live_channels())


class BrowserWindow:
    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.process: subprocess.Popen[bytes] | None = None
        self.opened_this_live_wave = False
        self.missing_display_reported = False
        profile_root = Path.home() / ".local" / "share" / "twitch-live-browser"
        self.profile_dir = profile_root / "browser-profile"
        if channel != "timur18o4":
            self.profile_dir = profile_root / f"browser-profile-{channel}"

    def _commands(self) -> list[list[str]]:
        url = f"https://www.twitch.tv/{self.channel}"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        commands: list[list[str]] = []

        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        if chromium:
            commands.append(
                [
                    chromium,
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={self.profile_dir}",
                    "--new-window",
                    url,
                ]
            )

        firefox = shutil.which("firefox") or shutil.which("firefox-esr")
        if firefox:
            firefox_profile = self.profile_dir / "firefox"
            firefox_profile.mkdir(parents=True, exist_ok=True)
            commands.append(
                [
                    firefox,
                    "--no-remote",
                    "--new-instance",
                    "--profile",
                    str(firefox_profile),
                    url,
                ]
            )
        return commands

    def open(self) -> bool:
        if self.opened_this_live_wave:
            return True
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            if not self.missing_display_reported:
                LOG.warning("Noch keine grafische Sitzung verfügbar; der Browserstart wird erneut versucht.")
                self.missing_display_reported = True
            return False

        self.missing_display_reported = False
        commands = self._commands()
        if not commands:
            LOG.error("Kein Chromium- oder Firefox-Programm gefunden.")
            return False

        for command in commands:
            try:
                process = subprocess.Popen(command, start_new_session=True)
            except OSError as exc:
                LOG.warning("Browser konnte nicht gestartet werden: %s", exc)
                continue

            time.sleep(1)
            if process.poll() is None:
                self.process = process
                self.opened_this_live_wave = True
                LOG.info("Twitch-Kanal %s in einem eigenen Browserfenster geöffnet.", self.channel)
                return True
            LOG.warning("Ein Browserprogramm wurde beim Start beendet; das nächste wird versucht.")

        LOG.error("Browserfenster konnte nicht geöffnet bleiben; ein neuer Versuch folgt.")
        return False

    def close(self) -> None:
        process = self.process
        self.process = None
        self.opened_this_live_wave = False
        self.missing_display_reported = False

        if process is None:
            return

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as exc:
            LOG.warning("Browser konnte nicht ordnungsgemäß geschlossen werden: %s", exc)
            return

        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            LOG.info("Twitch-Browserfenster geschlossen.")
            return
        except OSError as exc:
            LOG.warning("Schließstatus des Browsers konnte nicht geprüft werden: %s", exc)
            return

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        LOG.info("Twitch-Browserfenster geschlossen.")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client_id = required_setting("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if is_placeholder(client_secret):
        client_secret = ""
    user_access_token = os.environ.get("TWITCH_ACCESS_TOKEN", "").strip()
    user_refresh_token = os.environ.get("TWITCH_REFRESH_TOKEN", "").strip()
    if bool(user_access_token) != bool(user_refresh_token):
        raise RuntimeError("Trage entweder beide Werte TWITCH_ACCESS_TOKEN und TWITCH_REFRESH_TOKEN oder keinen davon ein.")
    if not client_secret and not (user_access_token and user_refresh_token):
        raise RuntimeError(
            "Trage TWITCH_CLIENT_SECRET oder beide User-Tokens ein."
        )
    channels = get_channels()
    try:
        interval = max(10, int(os.environ.get("CHECK_INTERVAL_SECONDS", "15")))
    except ValueError as exc:
        raise RuntimeError("CHECK_INTERVAL_SECONDS muss eine ganze Zahl sein.") from exc

    twitch = TwitchClient(
        client_id,
        client_secret or None,
        channels,
        user_access_token or None,
        user_refresh_token or None,
    )
    browsers = {channel: BrowserWindow(channel) for channel in channels}
    stopping = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    LOG.info("Überwache Twitch-Kanäle %s alle %s Sekunden.", ", ".join(channels), interval)
    last_known_live: dict[str, bool | None] = {channel: None for channel in channels}

    while not stopping.is_set():
        try:
            live_channels = twitch.live_channels()
            for channel, browser in browsers.items():
                live = channel in live_channels
                if live:
                    if last_known_live[channel] is not True:
                        LOG.info("Kanal %s ist live.", channel)
                    browser.open()
                else:
                    if last_known_live[channel] is True:
                        LOG.info("Kanal %s ist offline.", channel)
                    browser.close()
                last_known_live[channel] = live
        except (HTTPError, URLError, RuntimeError, ValueError) as exc:
            LOG.error("Twitch-Status konnte nicht abgerufen werden: %s", exc)

        stopping.wait(interval)

    for browser in browsers.values():
        browser.close()
    LOG.info("Monitor beendet.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
        LOG.error("%s", exc)
        raise SystemExit(1)
