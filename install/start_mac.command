#!/usr/bin/env bash
# Entry point for the writable DATA folder copied from the disk image.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -d "$ROOT/dashboard" ]; then ROOT="$(cd "$ROOT/.." && pwd)"; fi
fail() { printf '\n%s\nPress Return to close.\n' "$1" >&2; read -r _ || true; exit 1; }
case "$ROOT" in
    /Volumes/*) fail 'Copy the DATA folder from the disk image to your home folder before starting it.' ;;
esac
[ -w "$ROOT" ] || fail 'Copy DATA to a folder you can write to, such as your home folder.'
if [ -f "$ROOT/MAC_ARCH.txt" ] && [ "$(cat "$ROOT/MAC_ARCH.txt")" != "$(uname -m)" ]; then
    fail 'This download is for a different Mac processor. Download Apple Silicon for M-series Macs, or Intel for Intel Macs.'
fi
if ! bash "$ROOT/install/install.sh" --offline; then
    fail 'Setup failed. Read the error above and READ-ME-FIRST-MAC.txt.'
fi
echo 'Starting DATA. Keep this Terminal window open while using the dashboard.'
exec bash "$ROOT/start_data.sh"
