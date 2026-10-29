#!/usr/bin/env bash
# DATA macOS first-run recovery for a trusted, copied download.
# Run with: bash "$HOME/Downloads/DATA/install/mac_first_run.command"
# Finder approval uses System Settings > Privacy & Security > Open Anyway.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
fail() { printf '\n%s\nPress Return to close.\n' "$1" >&2; read -r _ || true; exit 1; }

[ "$(uname -s)" = Darwin ] || fail 'This helper is for macOS. On Linux, run bash install/install.sh.'
case "$ROOT" in
    /Volumes/*) fail 'Copy the DATA folder out of the disk image into Downloads first, then run the copied helper.' ;;
esac
[ -w "$ROOT" ] || fail 'Copy DATA to a folder you own, such as Downloads or your home folder.'
[ -f "$ROOT/dashboard/bridge_server.py" ] && [ -f "$ROOT/install/install.sh" ] || \
    fail 'The DATA folder is incomplete. Extract or copy the entire download, then try again.'

if [ -f "$ROOT/MAC_ARCH.txt" ]; then
    [ "$(cat "$ROOT/MAC_ARCH.txt")" = "$(uname -m)" ] || \
        fail 'This download is for a different Mac processor. Choose Apple Silicon for M-series Macs, or Intel for Intel Macs.'
    [ -f "$ROOT/runtime/python/bin/python3" ] && [ -f "$ROOT/install/start_mac.command" ] || \
        fail 'The bundled Mac runtime or launcher is missing. Download and copy the entire Mac DMG again.'
fi

printf '\n  DATA - macOS first-run setup\n  DATA folder: %s\n\n' "$ROOT"
printf '  Removing downloaded-file quarantine from this DATA folder only.\n'
# Recursive deletion tolerates absent attributes; actual access errors must stop
# setup. Keep stderr visible so users can distinguish a permission failure.
if ! xattr -dr com.apple.quarantine "$ROOT"; then
    fail 'Could not clear quarantine. Check Terminal access to Downloads in Privacy & Security > Files & Folders. See READ-ME-FIRST-MAC.txt.'
fi
printf '  [OK] Quarantine cleared or already absent.\n\n'

if [ -f "$ROOT/MAC_ARCH.txt" ]; then
    # The DMG uses its bundled runtime and must not install optional packages.
    exec bash "$ROOT/install/start_mac.command"
fi

# Cross-platform source ZIP: use the regular installer and installed Python.
if ! bash "$ROOT/install/install.sh"; then
    fail 'Setup failed. Read the error above and READ-ME-FIRST-MAC.txt before trying again.'
fi
printf '\n  Starting DATA. Keep this Terminal window open while using the dashboard.\n'
exec bash "$ROOT/start_data.sh"
