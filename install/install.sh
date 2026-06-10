#!/usr/bin/env bash
# DATA — macOS / Linux / ChromeOS installer
# Checks Python, installs optional deps, creates the start_data.sh launcher.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ""
echo "  DATA — Dashboard for Analytical Thought and Action"
echo "  macOS / Linux / ChromeOS installer"
echo ""

# 1. Find Python 3.10+
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$cmd"; break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "  [X] Python 3.10+ not found."
    echo "      Debian/Ubuntu/ChromeOS: sudo apt install python3 python3-pip"
    echo "      macOS:                  brew install python3"
    exit 1
fi
echo "  [OK] $($PYTHON --version) found ($PYTHON)"

# 2. Optional: psutil for the system-vitals panel
if "$PYTHON" -c 'import psutil' 2>/dev/null; then
    echo "  [OK] psutil already installed"
else
    echo "  [..] Installing psutil (system vitals — optional but recommended)..."
    if "$PYTHON" -m pip install --quiet --user psutil 2>/dev/null || \
       "$PYTHON" -m pip install --quiet --user --break-system-packages psutil 2>/dev/null; then
        echo "  [OK] psutil installed"
    else
        echo "  [!!] psutil install failed — vitals will read zero. Continuing."
    fi
fi

# 3. Check for an AI provider CLI
FOUND=""
for p in claude codex gemini ollama; do
    command -v "$p" >/dev/null 2>&1 && FOUND="$FOUND $p"
done
if [ -n "$FOUND" ]; then
    echo "  [OK] AI provider(s) found:$FOUND"
else
    echo "  [!!] No AI provider CLI found (claude / codex / gemini / ollama)."
    echo "      The dashboard will load, but chat needs one. Recommended:"
    echo "      https://docs.claude.com/en/docs/claude-code"
fi

# 4. Seed .env from the example if missing
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "  [OK] Created .env (edit it to set weather coords, port, etc.)"
fi

# 5. Write the launcher
cat > "$ROOT/start_data.sh" <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")/dashboard"
( sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:7777
  elif command -v open >/dev/null 2>&1; then open http://localhost:7777
  fi ) &
exec $PYTHON bridge_server.py
EOF
chmod +x "$ROOT/start_data.sh"
echo "  [OK] Launcher written: start_data.sh"

echo ""
echo "  Done. Run ./start_data.sh to launch DATA."
echo "  Dashboard: http://localhost:7777"
echo ""
