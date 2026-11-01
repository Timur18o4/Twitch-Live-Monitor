#!/usr/bin/env python3
"""Open one channel page per configured Twitch channel while live."""

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
    if not value or value.lower().startswith("replace_with_"):
        raise RuntimeError(f"Set {name} in ~/.config/twitch-live-browser/config.env.")
    return value


def get_channels() -> list[str]:
    configured = os.environ.get("TWITCH_CHANNELS", "").strip()
    if not configured:
        configured = os.environ.get("TWITCH_CHANNEL", "timur18o4")
    channels = list(dict.fromkeys(item.strip().lower() for item in configured.split(",") if item.strip()))
    if not channels:
        raise RuntimeError("Set TWITCH_CHANNELS or TWITCH_CHANNEL to at least one Twitch login name.")
    if any(not re.fullmatch(r"[a-z0-9_]{1,25}", channel) for channel in channels):
        raise RuntimeError("TWITCH_CHANNELS must contain comma-separated Twitch login names.")
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
            raise RuntimeError("TWITCH_CLIENT_SECRET is required for app-token mode.")
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
            raise RuntimeError(f"Twitch token request failed (HTTP {exc.code}).") from None
        except URLError as exc:
            raise RuntimeError(f"Twitch token request failed: {exc.reason}") from None

        token = payload.get("access_token")
        if not token:
            raise RuntimeError("Twitch did not return an access token.")
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
            LOG.warning("Saved Twitch token state could not be read; using configured tokens.")
            return
        if state.get("client_id") != self.client_id:
            LOG.warning("Saved Twitch token state belongs to another app; using configured tokens.")
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
            raise RuntimeError("Could not securely save refreshed Twitch tokens.") from exc

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
            raise RuntimeError(f"Twitch token validation failed (HTTP {exc.code}).") from None
        except URLError as exc:
            raise RuntimeError(f"Twitch token validation failed: {exc.reason}") from None

        if payload.get("client_id") != self.client_id:
            raise RuntimeError("TWITCH_CLIENT_ID does not match the supplied Twitch access token.")
        now = time.monotonic()
        self.token_valid_until = now + max(0, int(payload.get("expires_in", 0)))
        self.validate_again_at = now + 3600
        return True

    def _refresh_user_token(self) -> str:
        if not self.refresh_token:
            raise RuntimeError("A Twitch refresh token is required to renew the user access token.")
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
                f"Twitch rejected the refresh token (HTTP {exc.code}); a client secret may be required."
            ) from None
        except URLError as exc:
            raise RuntimeError(f"Twitch token refresh failed: {exc.reason}") from None

        new_access = payload.get("access_token")
        new_refresh = payload.get("refresh_token")
        if not new_access or not new_refresh:
            raise RuntimeError("Twitch did not return refreshed access and refresh tokens.")
        self.access_token = new_access
        self.refresh_token = new_refresh
        now = time.monotonic()
        self.token_valid_until = now + max(0, int(payload.get("expires_in", 3600)) - 60)
        self.validate_again_at = now + 3600
        self._save_user_token_state()
        LOG.info("Refreshed Twitch user access token.")
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
                raise RuntimeError(f"Twitch live-status request failed (HTTP {exc.code}).") from None
            except URLError as exc:
                raise RuntimeError(f"Twitch live-status request failed: {exc.reason}") from None
        raise RuntimeError("Twitch authorization failed after refreshing the app token.")

    def is_live(self) -> bool:
        """Backward-compatible convenience method for one-channel callers."""
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
                LOG.warning("No graphical session yet; browser launch will be retried.")
                self.missing_display_reported = True
            return False

        self.missing_display_reported = False
        commands = self._commands()
        if not commands:
            LOG.error("No Chromium or Firefox executable found.")
            return False

        for command in commands:
            try:
                process = subprocess.Popen(command, start_new_session=True)
            except OSError as exc:
                LOG.warning("Could not start browser: %s", exc)
                continue

            time.sleep(1)
            if process.poll() is None:
                self.process = process
                self.opened_this_live_wave = True
                LOG.info("Opened Twitch channel %s in a separate browser window.", self.channel)
                return True
            LOG.warning("A browser command exited during startup; trying the next one.")

        LOG.error("Could not keep a browser window open; launch will be retried.")
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
            LOG.warning("Could not close the browser cleanly: %s", exc)
            return

        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            LOG.info("Closed the Twitch browser window.")
            return
        except OSError as exc:
            LOG.warning("Could not check whether the browser closed: %s", exc)
            return

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        LOG.info("Closed the Twitch browser window.")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    client_id = required_setting("TWITCH_CLIENT_ID")
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    if client_secret.lower().startswith("replace_with_"):
        client_secret = ""
    user_access_token = os.environ.get("TWITCH_ACCESS_TOKEN", "").strip()
    user_refresh_token = os.environ.get("TWITCH_REFRESH_TOKEN", "").strip()
    if bool(user_access_token) != bool(user_refresh_token):
        raise RuntimeError("Set both TWITCH_ACCESS_TOKEN and TWITCH_REFRESH_TOKEN, or neither.")
    if not client_secret and not (user_access_token and user_refresh_token):
        raise RuntimeError(
            "Set TWITCH_CLIENT_SECRET, or provide both user access and refresh tokens."
        )
    channels = get_channels()
    try:
        interval = max(10, int(os.environ.get("CHECK_INTERVAL_SECONDS", "15")))
    except ValueError as exc:
        raise RuntimeError("CHECK_INTERVAL_SECONDS must be a whole number.") from exc

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

    LOG.info("Monitoring Twitch channels %s every %s seconds.", ", ".join(channels), interval)
    last_known_live: dict[str, bool | None] = {channel: None for channel in channels}

    while not stopping.is_set():
        try:
            live_channels = twitch.live_channels()
            for channel, browser in browsers.items():
                live = channel in live_channels
                if live:
                    if last_known_live[channel] is not True:
                        LOG.info("Channel %s is live.", channel)
                    browser.open()
                else:
                    if last_known_live[channel] is True:
                        LOG.info("Channel %s is offline.", channel)
                    browser.close()
                last_known_live[channel] = live
        except (HTTPError, URLError, RuntimeError, ValueError) as exc:
            LOG.error("Could not update Twitch status: %s", exc)

        stopping.wait(interval)

    for browser in browsers.values():
        browser.close()
    LOG.info("Monitor stopped.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
        LOG.error("%s", exc)
        raise SystemExit(1)
