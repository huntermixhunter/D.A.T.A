#!/usr/bin/env bash
# Download a pinned macOS Python and psutil wheel. --stage-only also works in Git Bash.
set -euo pipefail
OUTDIR=""
ARCH=""
STAGE_ONLY=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --out) OUTDIR="${2:?--out needs a directory}"; shift 2 ;;
        --arch) ARCH="${2:?--arch needs arm64 or x86_64}"; shift 2 ;;
        --stage-only) STAGE_ONLY=1; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -n "$OUTDIR" ] || { echo '--out is required' >&2; exit 2; }
[ -n "$ARCH" ] || ARCH="$(uname -m)"
case "$ARCH" in
    arm64|aarch64)
        ARCH=arm64
        TARGET=aarch64
        PY_SHA=ba32036ad8f4c434d97a1ee7feef8b7edc0fac16777639b97e39c74a108e4440
        WHEEL_URL=https://files.pythonhosted.org/packages/80/c4/f5af4c1ca8c1eeb2e92ccca14ce8effdeec651d5ab6053c589b074eda6e1/psutil-7.2.2-cp36-abi3-macosx_11_0_arm64.whl
        WHEEL_SHA=1a7b04c10f32cc88ab39cbf606e117fd74721c831c98a27dc04578deb0c16979
        ;;
    x86_64|amd64)
        ARCH=x86_64
        TARGET=x86_64
        PY_SHA=9686ddd8639b855905447ea4a16eb54a804a9ef12afbe7c9ecb1412d9c1fbc98
        WHEEL_URL=https://files.pythonhosted.org/packages/e7/36/5ee6e05c9bd427237b11b3937ad82bb8ad2752d72c6969314590dd0c2f6e/psutil-7.2.2-cp36-abi3-macosx_10_9_x86_64.whl
        WHEEL_SHA=ed0cace939114f62738d808fdcecd4c869222507e266e574799e9c0faa17d486
        ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 2 ;;
esac
if [ "$STAGE_ONLY" = 0 ]; then
    [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = "$ARCH" ] || {
        echo 'Build on a matching Mac runner, or use --stage-only for a file preview.' >&2
        exit 1
    }
fi
[ ! -e "$OUTDIR/python" ] || { echo 'Output already contains python; use a fresh output directory.' >&2; exit 1; }
mkdir -p "$OUTDIR"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/data-runtime.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
CACHE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/build/mac-runtime-cache"
mkdir -p "$CACHE_DIR"

verify() {
    local actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$1" | cut -d ' ' -f 1)"
    else
        actual="$(shasum -a 256 "$1" | cut -d ' ' -f 1)"
    fi
    [ "$actual" = "$2" ]
}

# Slow Windows connections can outlast one request. Keep partial downloads and
# resume them; never extract anything until the full expected SHA256 matches.
download() {
    local url="$1" dest="$2" expected="$3" attempt code
    if [ -f "$dest" ] && verify "$dest" "$expected"; then return 0; fi
    for attempt in 1 2 3; do
        echo "Download attempt $attempt: $(basename "$dest")"
        if curl -fsSL --continue-at - --connect-timeout 20 --max-time 600 --speed-limit 1024 --speed-time 60 -o "$dest" "$url"; then
            if verify "$dest" "$expected"; then return 0; fi
            echo 'Downloaded file failed SHA256 verification. Retrying from scratch.' >&2
            rm -f "$dest"
        else
            code=$?
            # A server can reject a Range request (including an already-complete
            # corrupt cache). Other failures retain the partial file for resume.
            case "$code" in 22|33|36) rm -f "$dest" ;; esac
        fi
    done
    echo "Could not download and verify $(basename "$dest"). Re-run to resume." >&2
    return 1
}

PYVER=3.12.15
PBS_RELEASE=20261013
URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE/cpython-$PYVER%2B$PBS_RELEASE-$TARGET-apple-darwin-install_only.tar.gz"
echo "Preparing Python $PYVER and psutil 7.2.2 for $ARCH"
PY_ARCHIVE="$CACHE_DIR/python-$PYVER-$PBS_RELEASE-$ARCH.tar.gz"
PSUTIL_WHEEL="$CACHE_DIR/psutil-7.2.2-$ARCH.whl"
download "$URL" "$PY_ARCHIVE" "$PY_SHA"
download "$WHEEL_URL" "$PSUTIL_WHEEL" "$WHEEL_SHA"
tar -xzf "$PY_ARCHIVE" -C "$OUTDIR"

# The pinned wheel has a flat site-packages layout and no install hooks.
# unzip runs on the host, so staging never executes a Mac binary on Windows.
unzip -q "$PSUTIL_WHEEL" -d "$OUTDIR/python/lib/python3.12/site-packages"
[ -f "$OUTDIR/python/bin/python3" ] || { echo 'Missing bundled interpreter' >&2; exit 1; }
if [ "$STAGE_ONLY" = 0 ]; then
    "$OUTDIR/python/bin/python3" -c 'import platform, ssl, sqlite3, psutil; print("Runtime OK:", platform.machine(), psutil.__version__)'
else
    echo 'Staged files only. macOS execution and DMG creation still require the CI build.'
fi
