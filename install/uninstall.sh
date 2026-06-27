#!/usr/bin/env bash
# DAITA — macOS / Linux / ChromeOS uninstaller
# Reverses install.sh: stops DAITA, removes the generated launcher, the app-menu
# entry, and (optionally) the bundled DAITA-core skills. Leaves your data and the
# DAITA folder itself in place — deleting the folder is the last step you do by hand.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Flags (optional): --remove-skills  --remove-env  --force (assume yes, non-interactive)
REMOVE_SKILLS=0; REMOVE_ENV=0; FORCE=0
for a in "$@"; do
    case "$a" in
        --remove-skills) REMOVE_SKILLS=1 ;;
        --remove-env)    REMOVE_ENV=1 ;;
        --force)         FORCE=1 ;;
    esac
done

echo ""
echo "  DAITA — Uninstaller"
echo "  Removing what the installer added outside the DAITA folder."
echo ""

confirm() {  # confirm "message" default(0=no,1=yes) -> returns 0 for yes
    local msg="$1"; local def="$2"
    [ "$FORCE" = "1" ] && return 0
    if [ ! -t 0 ]; then [ "$def" = "1" ] && return 0 || return 1; fi
    local hint="[y/N]"; [ "$def" = "1" ] && hint="[Y/n]"
    read -r -p "  $msg $hint " ans
    [ -z "$ans" ] && { [ "$def" = "1" ] && return 0 || return 1; }
    case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# 1. Stop DAITA (the bridge listens on 7777; the start script execs it directly).
echo "  [..] Stopping DAITA..."
if command -v pkill >/dev/null 2>&1; then
    pkill -f "bridge_server.py" 2>/dev/null || true
    pkill -f "supervisor.py"   2>/dev/null || true
fi
# Best-effort: free port 7777 if lsof is available.
if command -v lsof >/dev/null 2>&1; then
    for pid in $(lsof -ti tcp:7777 2>/dev/null); do kill -9 "$pid" 2>/dev/null || true; done
fi
echo "  [OK] DAITA stopped."

# 2. Remove the Linux app-menu launcher (created outside the folder).
DESKTOP_ENTRY="$HOME/.local/share/applications/data-dashboard.desktop"
if [ -f "$DESKTOP_ENTRY" ]; then
    rm -f "$DESKTOP_ENTRY"
    echo "  [OK] Removed app-menu launcher (DAITA)"
else
    echo "  [--] No app-menu launcher found (nothing to remove)."
fi

# 3. Remove the generated launcher.
if [ -f "$ROOT/start_daita.sh" ]; then
    rm -f "$ROOT/start_daita.sh"
    echo "  [OK] Removed start_daita.sh"
fi
[ -f "$ROOT/bridge.log" ] && rm -f "$ROOT/bridge.log" && echo "  [OK] Removed bridge.log"

# 4. .env — your saved settings. Kept by default so a reinstall keeps your config.
if [ -f "$ROOT/.env" ]; then
    if [ "$REMOVE_ENV" = "1" ] || confirm "Delete your saved settings (.env)?" 0; then
        rm -f "$ROOT/.env"
        echo "  [OK] Removed .env"
    else
        echo "  [--] Kept .env (your settings)."
    fi
fi

# 5. Bundled DAITA-core skills — shared with the rest of your Claude setup, so
#    KEPT by default. Only unmodified copies DAITA placed are removed, opt-in.
if [ -f "$ROOT/dashboard/uninstall_skills.py" ]; then
    if [ "$REMOVE_SKILLS" = "1" ] || confirm "Also remove the bundled DAITA-core skills it installed?" 0; then
        PY=""
        for cmd in python3 python; do command -v "$cmd" >/dev/null 2>&1 && { PY="$cmd"; break; }; done
        if [ -n "$PY" ]; then
            echo "  [..] Removing bundled skills (unmodified copies only)..."
            "$PY" "$ROOT/dashboard/uninstall_skills.py"
        else
            echo "  [!!] Python not found — skipping. Run later: python dashboard/uninstall_skills.py"
        fi
    else
        echo "  [--] Kept the bundled skills (used across your Claude setup)."
        echo "       Remove later: python dashboard/uninstall_skills.py"
    fi
fi

# 6. Deliberately untouched.
echo ""
echo "  Left in place on purpose:"
echo "    - psutil (a shared Python package). Remove with: pip uninstall psutil"
echo "    - Your AI provider CLI (claude / codex / ...) — DAITA did not install it."

# 7. Final step: the folder. The script runs from inside it, so the user deletes it.
echo ""
echo "  Done. DAITA is uninstalled."
echo "  The only thing left is this folder — delete it when ready:"
echo "    $ROOT"
echo ""
