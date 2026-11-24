---
name: mac-desktop-control
description: "Drive the Mac screen with DATA's own screenshot, click and type tools: permissions, keys, Retina, and the safe loop."
version: 1.0.0
author: DATA
license: Apache-2.0
platforms: [macos]
---

# Mac desktop control (DATA)

Use this skill when a task on a Mac needs the screen itself: a native app, a
settings window, an installer, or anything with no API or command line. It uses
DATA's built-in desktop control (screenshots plus pyautogui), the same tools
DATA uses on Windows.

## Pick the right surface first

1. A command line or API exists (AppleScript via `osascript`, `open`, `defaults`,
   a CLI, a web API): use that. It is faster and more reliable than clicking.
2. A logged-in website: use the `chrome-cdp` skill. It drives the user's own
   Chrome with their sessions.
3. Only the GUI is left: use the loop below.

## Check readiness

```bash
curl -s http://localhost:7777/computer/info
```

- `available: false` means the screen-control packages are not installed. Tell
  the user to open Settings > Upgrades > Skill Library and click
  **Install screen control**, then try again.
- `enabled: false` means the user switched desktop control off. Ask before doing
  anything else.
- `screen_width` / `screen_height` are in macOS points. Screenshots are scaled
  to the same size, so a pixel you see in a screenshot is the coordinate you
  click. Do not double coordinates for Retina screens.

## macOS permissions (one-time, done by the user)

If clicks do nothing, or screenshots show only the desktop wallpaper, macOS is
blocking DATA. The user must open **System Settings > Privacy & Security** and
turn on, for the app that runs DATA (usually **Terminal**):

- **Accessibility**: needed for clicking, typing and key presses.
- **Screen & System Audio Recording** (older macOS: **Screen Recording**):
  needed for screenshots.

Then quit and reopen DATA. You cannot grant these yourself; explain the steps
and wait.

## The loop

Always: screenshot, look, act, screenshot again to verify.

```bash
# Look
curl -s -X POST -H 'Content-Type: application/json' -d '{}' http://localhost:7777/computer/screenshot
# Act
curl -s -X POST -H 'Content-Type: application/json' -d '{"x":500,"y":300}' http://localhost:7777/computer/click
curl -s -X POST -H 'Content-Type: application/json' -d '{"text":"hello"}' http://localhost:7777/computer/type
curl -s -X POST -H 'Content-Type: application/json' -d '{"keys":"cmd+space"}' http://localhost:7777/computer/key
curl -s -X POST -H 'Content-Type: application/json' -d '{"amount":-3}' http://localhost:7777/computer/scroll
```

The screenshot reply gives a file path; open the image to see it. Brains with
the native `take_screenshot` and `desktop_*` tools can call those instead.

## Mac keys

- Use `cmd` for shortcuts: `cmd+c`, `cmd+v`, `cmd+s`, `cmd+tab`, `cmd+space`
  (Spotlight), `cmd+q`, `cmd+w`. `win` and `super` are also treated as `cmd`.
- `option` (or `alt`), `ctrl`, `shift`, `return`, `escape`, `tab`, `delete`
  (backspace), `up` / `down` / `left` / `right`.
- Open an app reliably: `cmd+space`, type the app name, `return`. Or run
  `open -a "App Name"` from a shell, which is faster.
- Typing non-English characters or emoji is done through the clipboard
  automatically.

## Safety

- Moving the mouse into the top-left corner (0,0) is the emergency stop.
- Never type passwords, Apple ID codes or two-factor codes. Hand control back.
- Before anything destructive (delete, send, buy, publish, close unsaved work),
  take a screenshot, describe what you are about to do, and wait for a yes.
