# Remote access

Open **Settings > Remote Access** on the computer running DATA. The two options
can be used independently or together. Setup does not require editing files.

## Full mobile dashboard

1. Install Tailscale on the DATA computer and your phone. Sign in with the same
   account on both devices and leave both connected.
2. Select **Check connection**, then **Enable full mobile access**.
3. If Tailscale needs HTTPS permission, follow **Approve HTTPS in Tailscale** and
   retry. Your Tailscale account must allow Serve and MagicDNS.
4. Select **Copy phone link**, transfer it privately to your phone, and open it
   in Safari or Chrome. Use the browser's Add to Home Screen action for a shortcut.

The link opens the responsive dashboard: chats, project panes, files and status.
Administrative actions such as installing tools, changing remote access and
managing marketplace accounts remain restricted to the DATA computer. The
computer must stay powered on, awake, online, and running DATA.

Tailscale Serve provides private HTTPS access within your Tailscale network.
The Personal plan is free for eligible personal use; business plans have their
own terms. DATA does not subscribe to a plan, purchase anything or open a public
Funnel. See [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
and [Tailscale pricing](https://tailscale.com/pricing).

The setup uses HTTPS port 8443 and proxies to the running bridge's loopback port.
It refuses to overwrite another service or an existing public Funnel on that
port. **Disable mobile access** revokes generated access keys and removes only
the matching DATA proxy. If Tailscale cannot be reached, the keys are still
revoked and Settings explains that its proxy needs attention. A separately
configured `DATA_BRIDGE_TOKEN` remains valid for existing external integrations.

The phone link always uses its own revocable key, including when a separate
bridge token is configured. The private link carries its key in a URL fragment,
which is removed after the browser saves it locally. Keep the link private.
Anyone who has it and can reach the private Tailscale address has access to DATA.
The access key is separate from
your marketplace account password.

## Telegram bot

1. Choose **Telegram bot**, then **Open BotFather**.
2. Send `/newbot` to Telegram's official BotFather and follow its prompts.
3. Paste the bot token into DATA and select **Connect Telegram bot**.
4. Open **Pair in Telegram** on your phone and tap **Start** within ten minutes.
   **Copy pairing link** lets you transfer it to another device. Generating a new
   link invalidates the previous one.
5. Select **Send test message** to confirm delivery without making an AI request.

Only the paired private Telegram account can send requests. Groups, other users,
expired pairing links, repeated updates and stale queued messages are ignored.
Normal text uses the currently selected DATA AI connection and its usual limits
or charges. `/help` and `/status` work without invoking AI. Telegram messages and
replies pass through Telegram's service.

The bot is associated with the DATA profile selected during setup. Selecting
another profile pauses its AI replies; DATA does not switch profiles in response
to Telegram. A profile switch is blocked while a Telegram request is running.
Standing orders that opt into Telegram delivery use the paired active profile.

Create a dedicated bot for DATA. Setup checks for an existing webhook and refuses
to replace it. A polling conflict with another application stops the connection
and is shown in Settings. **Reconnect bot** retries after the conflict is fixed.
See the [Telegram Bot API](https://core.telegram.org/bots/api) for the underlying
getMe, getWebhookInfo, getUpdates and sendMessage methods.

**Disconnect Telegram** removes the stored token and pairing. An AI task that
has already started may finish locally; its reply is not delivered after
disconnection. Voice notes and attachments are not supported by this transport.

## Background operation and storage

Enabling either option also enables **Keep DATA running when I close the browser**.
This preference survives restarts. The bridge now stays up on its own when the
browser closes, so turning the preference off no longer stops DATA; it restores
browser-coupled shutdown only when the bridge was started with
`DATA_LIFECYCLE_MODE=auto`. System Offline, sleep, power loss or loss of
internet still interrupts remote access.

Configuration and secrets are saved in `users/remote-access/config.json`, outside
the static web directory and excluded from release archives by the existing
`users/` exclusion. Treat that directory as private. POSIX files are owner-only;
Windows uses the installation folder's inherited user access permissions.
Tokens are never returned by status endpoints. Setup routes require a direct
loopback connection, a matching Host/Origin and the dashboard's request header.
Remote API requests require an access key even when no environment token is set.

## Local verification

- `python -m unittest discover -s dashboard/tests -p test_remote_access.py -v`
- `python dashboard/tests/remote_access_browser.py`
- `python dashboard/remote_access_preview.py --port 7803`

Tests use temporary settings, local fixtures, and mocked external APIs. The
preview is read-only and starts no bot, Tailscale connection, AI request or
schedule. Open its `/#remote-access` route to inspect the Settings tab.
