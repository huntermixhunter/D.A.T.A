#!/usr/bin/env bash
# Resolve paths on every launch so a moved installation still works.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -d "$ROOT/dashboard" ] && [ -d "$ROOT/../dashboard" ]; then
    ROOT="$(cd "$ROOT/.." && pwd)"
fi
PYTHON="$ROOT/runtime/python/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON=""
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    done
fi
[ -n "$PYTHON" ] || { echo 'Python 3.10+ is required for this download.' >&2; exit 1; }
if [ "${1:-}" = --check ]; then
    exec "$PYTHON" -c 'import sys, ssl, sqlite3, psutil; print("DATA runtime OK:", sys.executable)'
fi
cd "$ROOT/dashboard"
(
    sleep 2
    if command -v open >/dev/null 2>&1; then open http://localhost:7777
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:7777
    fi
) &
exec "$PYTHON" supervisor.py
