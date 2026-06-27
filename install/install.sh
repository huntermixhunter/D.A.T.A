#!/usr/bin/env bash
# DAITA — macOS / Linux / ChromeOS installer
# Checks Python, installs optional deps, creates the start_daita.sh launcher.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo ""
echo "  DAITA — Dashboard for Artificial Intelligence Thought and Action"
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
    echo "      NOTE: install the Claude Code COMMAND-LINE tool, not the"
    echo "      Claude Desktop app. The CLI installs via npm, which needs"
    echo "      Node.js first: https://nodejs.org (LTS). Then run:"
    echo "        npm install -g @anthropic-ai/claude-code"
    echo "      After installing, run 'claude' in a terminal and type"
    echo "      '/login' once to sign in (DAITA can't show the login"
    echo "      prompt itself; if chat says 'run /login', that's why)."
    echo "      Verify with 'claude --version'."
fi

# 4. Seed .env from the example if missing
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo "  [OK] Created .env (edit it to set weather coords, port, etc.)"
fi

# 4b. Install the bundled DAITA-core skills (idempotent; never clobbers your own copies)
if [ -f "$ROOT/dashboard/install_skills.py" ]; then
    echo "  [..] Installing bundled DAITA-core skills..."
    if "$PYTHON" "$ROOT/dashboard/install_skills.py" >/dev/null 2>&1; then
        echo "  [OK] DAITA-core skills installed"
    else
        echo "  [!!] Skill install hit an issue — DAITA still runs. Re-run later: python dashboard/install_skills.py"
    fi
fi

# 5. Write the launcher
cat > "$ROOT/start_daita.sh" <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")/dashboard"
( sleep 2
  if command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:7777
  elif command -v open >/dev/null 2>&1; then open http://localhost:7777
  fi ) &
exec $PYTHON bridge_server.py
EOF
chmod +x "$ROOT/start_daita.sh"
echo "  [OK] Launcher written: start_daita.sh"

# 6. App-menu launcher with the DAITA icon (Linux / ChromeOS)
if [ "$(uname -s)" = "Linux" ]; then
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/data-dashboard.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=DAITA
Comment=Dashboard for Artificial Intelligence Thought and Action
Exec=$ROOT/start_daita.sh
Icon=$ROOT/dashboard/assets/icon-256.png
Terminal=false
Categories=Utility;Development;
DESK
    echo "  [OK] App launcher installed - find DAITA in your application menu"
fi

echo ""
echo "  Done. Run ./start_daita.sh to launch DAITA."
echo "  Dashboard: http://localhost:7777"
echo ""
