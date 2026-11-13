"""Open the dashboard in its own app window instead of a browser tab.

The launchers call this right after they start the supervisor. It waits for
the bridge to answer, then opens the dashboard with Chrome or Edge in "app
mode": a plain window with no tabs or address bar, its own taskbar entry, and
its own browser profile so it never mixes with the user's everyday browsing.
App mode keeps the full browser engine, so speech recognition (wake word and
dictation), the microphone, notifications, and downloads all keep working.

If no Chromium-based browser is installed, or DATA_APP_WINDOW=0 is set, it
falls back to opening the dashboard in the default browser as before.

Usage:
    python app_window.py [--name DATA] [--port 7777] [--wait 45]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# First-run window size. The browser remembers the size and position the user
# picks afterwards, so this is only passed when the profile is brand new.
FIRST_RUN_SIZE = "1440,900"


def wait_for_bridge(url: str, timeout: float) -> bool:
    """Poll until the bridge answers HTTP (any status) or the timeout passes."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while True:
        try:
            with opener.open(url, timeout=2):
                return True
        except urllib.error.HTTPError:
            return True  # the server answered, even if with an error status
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


def profile_root(name: str) -> Path:
    """Per-user folder that holds the app window's private browser profile."""
    home = Path.home()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / name / "app-window"


def _windows_registry_path(exe: str) -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    key_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        value = str(value).strip().strip('"')
        if value and os.path.isfile(value):
            return value
    return None


def find_browser() -> tuple[str, str] | None:
    """Return (label, launch target) for the first Chromium browser found.

    Chrome is preferred because it is usually the user's chosen browser and its
    speech recognition is the reference implementation; Edge ships with every
    copy of Windows 10 and 11, so Windows nearly always has a fallback.
    """
    if sys.platform == "win32":
        program_dirs = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        browsers = [
            ("chrome", "chrome.exe", ("Google", "Chrome", "Application", "chrome.exe")),
            ("edge", "msedge.exe", ("Microsoft", "Edge", "Application", "msedge.exe")),
            ("brave", "brave.exe", ("BraveSoftware", "Brave-Browser", "Application", "brave.exe")),
        ]
        for label, exe, parts in browsers:
            found = _windows_registry_path(exe)
            if found:
                return label, found
            for base in program_dirs:
                if base:
                    candidate = os.path.join(base, *parts)
                    if os.path.isfile(candidate):
                        return label, candidate
        return None

    if sys.platform == "darwin":
        apps = [
            ("chrome", "Google Chrome.app"),
            ("edge", "Microsoft Edge.app"),
            ("brave", "Brave Browser.app"),
            ("chromium", "Chromium.app"),
        ]
        for label, bundle in apps:
            for folder in (Path("/Applications"), Path.home() / "Applications"):
                candidate = folder / bundle
                if candidate.is_dir():
                    return label, str(candidate)
        return None

    for label, names in (
        ("chrome", ("google-chrome", "google-chrome-stable")),
        ("chromium", ("chromium", "chromium-browser")),
        ("edge", ("microsoft-edge", "microsoft-edge-stable")),
        ("brave", ("brave-browser", "brave")),
    ):
        for name in names:
            found = shutil.which(name)
            if found:
                return label, found
    return None


def browser_flags(url: str, profile: Path, name: str, first_run: bool) -> list[str]:
    flags = [
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if first_run:
        flags.append(f"--window-size={FIRST_RUN_SIZE}")
    if sys.platform.startswith("linux"):
        flags.append(f"--class={name}")  # groups the window under its own dock icon
    return flags


def launch_command(target: str, flags: list[str]) -> list[str]:
    if sys.platform == "darwin":
        # LaunchServices starts a separate, detached instance for the private
        # profile, so closing the Terminal never takes the window with it.
        return ["open", "-na", target, "--args", *flags]
    return [target, *flags]


def _spawn(cmd: list[str]) -> None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def open_app_window(url: str, name: str) -> str:
    """Open `url` in an app window. Returns which path was taken."""
    if os.environ.get("DATA_APP_WINDOW", "1").strip().lower() in ("0", "false", "no", "off"):
        webbrowser.open(url)
        return "browser (DATA_APP_WINDOW is off)"

    browser = find_browser()
    if browser is None:
        webbrowser.open(url)
        return "browser (no Chrome or Edge found)"

    label, target = browser
    profile = profile_root(name) / label
    first_run = not profile.exists()
    try:
        profile.mkdir(parents=True, exist_ok=True)
        _spawn(launch_command(target, browser_flags(url, profile, name, first_run)))
    except Exception:
        webbrowser.open(url)
        return f"browser ({label} app window failed to start)"
    return f"{label} app window"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default="DATA", help="profile folder name (DATA or LCARS)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DATA_PORT", "7777")))
    parser.add_argument("--wait", type=float, default=45.0, help="seconds to wait for the bridge")
    args = parser.parse_args(argv)

    url = f"http://localhost:{args.port}/"
    # Open even if the bridge is slow to start; the window can be refreshed
    # with Ctrl+R once the bridge is up.
    wait_for_bridge(url, args.wait)
    print(open_app_window(url, args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
