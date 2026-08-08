#!/usr/bin/env bash
# DATA — macOS first-run helper (double-clickable)
# ----------------------------------------------------------------------------
# Why this file exists:
#   When you download DATA as a .dmg or .zip, macOS stamps every file inside
#   with a "quarantine" flag (com.apple.quarantine). Gatekeeper then refuses to
#   run the installer and warns that files are from an "unidentified developer"
#   or even "damaged". This helper removes that flag from the whole DATA folder
#   in one shot, then runs the normal installer for you. No Terminal typing.
#
# How to use it (FIRST TIME ONLY):
#   1. If you opened DATA from a .dmg window, first DRAG the DATA folder out to
#      your Desktop or Applications. A .dmg is read-only and the flag cannot be
#      cleared while the files live inside it.
#   2. RIGHT-CLICK this file (or Control-click) and choose "Open" — NOT a normal
#      double-click. The first time, macOS blocks a plain double-click; the
#      right-click "Open" gives you an "Open" button that gets past it.
#   3. Click "Open" in the little dialog. A Terminal window runs the steps below.
#
# After this runs once, DATA is unlocked and you just launch start_data.sh (or
# the DATA icon) normally from then on.
# ----------------------------------------------------------------------------
set -e

# Resolve the DATA root (this file lives in DATA/install/, so root is one up).
SELF="${BASH_SOURCE[0]}"
DIR="$(cd "$(dirname "$SELF")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

printf '\n'
printf '  ============================================================\n'
printf '   DATA — macOS first-run setup\n'
printf '  ============================================================\n'
printf '\n'
printf '  DATA folder: %s\n\n' "$ROOT"

# Guard: a read-only volume means we are still inside the mounted .dmg. We
# cannot strip the quarantine flag there. Tell the user to copy the folder out.
if [ ! -w "$ROOT" ]; then
    printf '  [X] This DATA folder is READ-ONLY.\n'
    printf '      You are running this from inside the disk image (.dmg).\n\n'
    printf '      Fix: drag the DATA folder onto your Desktop (or into your\n'
    printf '      Applications folder) first, then open this file again from\n'
    printf '      there (right-click > Open).\n\n'
    printf '  Press Return to close this window.\n'
    read -r _
    exit 1
fi

# 1. Dispel Gatekeeper quarantine on the entire folder.
printf '  [..] Removing the macOS quarantine flag from every file...\n'
if xattr -dr com.apple.quarantine "$ROOT" 2>/dev/null; then
    printf '  [OK] Quarantine cleared. Gatekeeper will stop blocking DATA.\n\n'
else
    # -dr returns non-zero if the attribute was already absent; that is fine.
    printf '  [OK] Nothing left to clear (already unlocked).\n\n'
fi

# 2. Run the normal installer.
printf '  [..] Running the DATA installer...\n\n'
if bash "$ROOT/install/install.sh"; then
    printf '\n  [OK] Install finished.\n\n'
else
    printf '\n  [!!] The installer reported a problem (see the lines above).\n'
    printf '       DATA may still run. You can re-open this file to try again.\n\n'
fi

# 3. Offer to launch now.
printf '  Launch DATA now? [Y/n] '
read -r ANS
case "$ANS" in
    n|N|no|NO) printf '\n  OK. Launch anytime by double-clicking start_data.sh in the DATA folder.\n\n' ;;
    *)
        printf '\n  Starting DATA... your browser will open to http://localhost:7777\n'
        printf '  (Leave this window open while you use DATA. Close it to stop.)\n\n'
        exec bash "$ROOT/start_data.sh"
        ;;
esac

printf '  Done. You can close this window.\n\n'
