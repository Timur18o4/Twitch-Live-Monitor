# Twitch Live Browser for Raspberry Pi

This service checks whether each configured Twitch channel is live. When a channel is live, it opens that channel page in its own Chromium or Firefox window. When Twitch reports the channel offline, it closes that window. Each channel has a separate browser profile. It checks every 15 seconds by default.

The monitor uses Twitch's Helix Get Streams endpoint with either an app access token or a user access token. It does not need permission to manage the channel. The regular Twitch channel page is opened in a dedicated browser profile, so you can sign in there if you want. Twitch controls playback on its own page.

## Requirements

- Raspberry Pi OS with a desktop session and internet access
- Python 3
- Chromium or Firefox
- A Twitch developer application with a Client ID and either a Client Secret or a user access/refresh token pair

Create the app at [Twitch Developer Console](https://dev.twitch.tv/console/apps). Twitch requires two-factor authentication for developer console access. Copy the app's Client ID. You can use either a Client Secret or a user access/refresh token pair. If the registration form requires a redirect URL, enter http://localhost:3000; this monitor does not use an interactive Twitch login.

## Install

Extract this folder on the Pi, open a terminal in it, then run:

    chmod +x install.sh
    ./install.sh

Edit the configuration file:

    nano ~/.config/twitch-live-browser/config.env

Set TWITCH_CLIENT_ID and one authentication option in the configuration. For a Client Secret, set TWITCH_CLIENT_SECRET. For user tokens, remove the placeholder Client Secret line and set both TWITCH_ACCESS_TOKEN and TWITCH_REFRESH_TOKEN. Keep values on one line and do not add quotes.

Start the service in the current desktop session:

    ~/.local/share/twitch-live-browser/start-from-desktop.sh

The installer also adds a desktop autostart entry. The monitor will start automatically at the next desktop login. Check its status with:

    systemctl --user status twitch-live-browser.service

See recent messages with:

    journalctl --user -u twitch-live-browser.service -f

## Settings

The file ~/.config/twitch-live-browser/config.env accepts:

- TWITCH_CHANNELS: comma-separated Twitch login names; default package config monitors timur18o4 and frau_potti
- TWITCH_CHANNEL: legacy single-channel setting, used if TWITCH_CHANNELS is not set
- CHECK_INTERVAL_SECONDS: status check interval; default 15, minimum 10
- TWITCH_CLIENT_ID: required Twitch app Client ID
- TWITCH_CLIENT_SECRET: app-token mode; may be omitted when using user tokens
- TWITCH_ACCESS_TOKEN and TWITCH_REFRESH_TOKEN: optional pair for user-token mode

A status change can take up to one check interval plus the API response time to appear. If Twitch's API or the network is temporarily unavailable, the monitor keeps the current browser state and retries.

In user-token mode, the service validates the access token on startup and hourly, then refreshes it when needed. Twitch rotates refresh tokens; each new pair is saved with owner-only file permissions in ~/.local/share/twitch-live-browser/token_state.json. A public Twitch app can refresh without a Client Secret; a confidential app may require one.

The existing timur18o4 browser profile remains under ~/.local/share/twitch-live-browser/browser-profile. Additional channel profiles use ~/.local/share/twitch-live-browser/browser-profile-CHANNEL, so multiple channel windows do not conflict.

## Stop or remove

Stop it until the next desktop login:

    systemctl --user stop twitch-live-browser.service

Remove the autostart entry and installed program by running ./uninstall.sh from this folder. Configuration and browser profile are retained.
