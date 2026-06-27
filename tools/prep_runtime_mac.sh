#!/usr/bin/env bash
# DATA — bundled-Python runtime builder (macOS)
# ----------------------------------------------------------------------------
# Produces a self-contained, RELOCATABLE CPython that DATA's bridge runs on, so
# a buyer needs ZERO pre-installed Python. macOS has no official "embeddable"
# package like Windows does, so we use the standalone, relocatable CPython
# builds from astral-sh/python-build-standalone (the same ones uv/Rye ship).
# We then pre-install psutil (the bridge's one optional third-party dep);
# everything else the bridge needs is in the standard library.
#
# Output:  <OutDir>/python/  (bin/python3, lib/python3.x/, Lib site-packages w/ psutil)
#
# Usage:
#   ./tools/prep_runtime_mac.sh --out build/staging/DATA/runtime
#   ./tools/prep_runtime_mac.sh --out build/staging/DATA/runtime --arch x86_64
#   ./tools/prep_runtime_mac.sh --out build/staging/DATA/runtime --py 3.12.8 --force
#
# Idempotent-ish: pass --force to rebuild from scratch (deletes an existing python/).
# MUST run on macOS (uses the macOS standalone tarball + tar). The build_dmg.sh
# orchestrator and the GitHub Actions macOS runner both call this.
set -euo pipefail

# ---- defaults (override with flags) ----------------------------------------
PYVER="3.12.8"             # CPython version to bundle
PBS_RELEASE="20241219"     # python-build-standalone release tag holding that version
ARCH=""                    # aarch64 | x86_64 ; empty = detect host
OUTDIR=""
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out)   OUTDIR="$2"; shift 2 ;;
        --py)    PYVER="$2"; shift 2 ;;
        --release) PBS_RELEASE="$2"; shift 2 ;;
        --arch)  ARCH="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        *) echo "  [X] Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$OUTDIR" ]; then
    echo "  [X] --out <dir> is required (e.g. --out build/staging/DATA/runtime)" >&2
    exit 2
fi
if [ "$(uname -s)" != "Darwin" ]; then
    echo "  [X] prep_runtime_mac.sh must run on macOS (got $(uname -s))." >&2
    echo "      Build the DMG on a Mac or via the GitHub Actions macOS runner." >&2
    exit 1
fi

# ---- resolve arch ----------------------------------------------------------
if [ -z "$ARCH" ]; then
    case "$(uname -m)" in
        arm64|aarch64) ARCH="aarch64" ;;
        x86_64)        ARCH="x86_64" ;;
        *) echo "  [X] Unsupported host arch: $(uname -m). Pass --arch aarch64|x86_64." >&2; exit 1 ;;
    esac
fi
case "$ARCH" in
    aarch64|arm64) ARCH="aarch64" ;;
    x86_64|amd64)  ARCH="x86_64" ;;
    *) echo "  [X] --arch must be aarch64 or x86_64 (got '$ARCH')." >&2; exit 1 ;;
esac

PYTHON_DIR="$OUTDIR/python"
TARBALL_NAME="cpython-${PYVER}+${PBS_RELEASE}-${ARCH}-apple-darwin-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${TARBALL_NAME}"

echo ""
echo "  DATA bundled-runtime builder (macOS)"
echo "  CPython ${PYVER} (${ARCH}-apple-darwin) -> ${PYTHON_DIR}"
echo ""

if [ -d "$PYTHON_DIR" ]; then
    if [ "$FORCE" = "1" ]; then
        rm -rf "$PYTHON_DIR"
    else
        echo "  [X] $PYTHON_DIR already exists. Pass --force to rebuild." >&2
        exit 1
    fi
fi

mkdir -p "$OUTDIR"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/data_runtime_mac.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# 1. Download the relocatable standalone CPython
TARBALL="$WORK/python.tar.gz"
echo "  [..] Downloading standalone CPython..."
echo "       $URL"
if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 -o "$TARBALL" "$URL"
elif command -v wget >/dev/null 2>&1; then
    wget -O "$TARBALL" "$URL"
else
    echo "  [X] Need curl or wget to download the runtime." >&2
    exit 1
fi

# 2. Extract — the install_only tarball unpacks to a top-level 'python/' dir
echo "  [..] Extracting..."
tar -xzf "$TARBALL" -C "$WORK"
if [ ! -x "$WORK/python/bin/python3" ]; then
    echo "  [X] Extracted tree missing bin/python3 — unexpected tarball layout." >&2
    exit 1
fi
mv "$WORK/python" "$PYTHON_DIR"
echo "  [OK] Extracted to $PYTHON_DIR"

PYBIN="$PYTHON_DIR/bin/python3"

# 3. Pre-install psutil (system-vitals panel). Baked in -> no internet at install time.
echo "  [..] Installing psutil into the runtime..."
"$PYBIN" -m pip install --no-warn-script-location --quiet --upgrade pip >/dev/null 2>&1 || true
"$PYBIN" -m pip install --no-warn-script-location --quiet psutil
echo "  [OK] psutil baked in"

# 4. Smoke test: the bundled interpreter must run and import psutil
CHECK="$("$PYBIN" -c 'import sys, psutil; print(sys.version.split()[0])')"
echo "  [OK] Runtime verified: Python $CHECK + psutil import OK"

# 5. Trim build noise (pip cache, __pycache__, tests) to keep the bundle lean
find "$PYTHON_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
rm -rf "$PYTHON_DIR/lib"/python*/test "$PYTHON_DIR/lib"/python*/*/test 2>/dev/null || true

SIZE="$(du -sh "$PYTHON_DIR" | cut -f1)"
echo ""
echo "  Runtime ready: $PYTHON_DIR ($SIZE)"
echo ""
