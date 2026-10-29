#!/usr/bin/env bash
# Build a clean, architecture-specific disk image from the committed source.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VERSION="${1:?Usage: build_dmg.sh X.Y.Z --arch arm64|x86_64 [--stage-only]}"
shift
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid version' >&2; exit 2; }
ARCH=""
STAGE_ONLY=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --arch) ARCH="${2:?Missing architecture}"; shift 2 ;;
        --stage-only) STAGE_ONLY=1; shift ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$ARCH" in arm64|x86_64) ;; *) echo '--arch must be arm64 or x86_64' >&2; exit 2 ;; esac
if [ "$STAGE_ONLY" = 0 ]; then
    [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = "$ARCH" ] || {
        echo 'A matching Mac runner is required. Use --stage-only on Windows.' >&2; exit 1;
    }
fi
[ -z "$(git status --porcelain)" ] || { echo 'Commit changes first; packages must match a clean commit.' >&2; exit 1; }
COMMIT="$(git rev-parse HEAD)"
if git rev-parse --verify "refs/tags/v$VERSION" >/dev/null 2>&1; then
    [ "$(git rev-list -n 1 "v$VERSION")" = "$COMMIT" ] || {
        echo 'This version tag points to a different commit. Refusing a mislabeled build.' >&2; exit 1;
    }
fi
mkdir -p "$ROOT/build" "$ROOT/dist"
WORK="$(mktemp -d "$ROOT/build/mac-$ARCH.XXXXXX")"
PAYLOAD="$WORK/image/DATA"
mkdir -p "$PAYLOAD"
git archive --format=tar HEAD | tar -xf - -C "$PAYLOAD"
for private in .git .github .env .mcp.json users DATA_MEMORY.md tools installer; do
    [ ! -e "$PAYLOAD/$private" ] || { echo "Forbidden package entry: $private" >&2; exit 1; }
done
args=(--out "$PAYLOAD/runtime" --arch "$ARCH")
if [ "$STAGE_ONLY" = 1 ]; then args+=(--stage-only); fi
bash tools/prep_runtime_mac.sh "${args[@]}"
cp "$PAYLOAD/install/start_mac.command" "$PAYLOAD/Start DATA.command"
cp "$PAYLOAD/install/start_data.sh" "$PAYLOAD/start_data.sh"
chmod +x "$PAYLOAD/Start DATA.command" "$PAYLOAD/start_data.sh" "$PAYLOAD/install/"*.sh "$PAYLOAD/install/"*.command
printf '%s\n' "$ARCH" > "$PAYLOAD/MAC_ARCH.txt"
printf '{"version":"%s","commit":"%s","architecture":"%s","python":"3.12.15","psutil":"7.2.2","signed":false}\n' \
    "$VERSION" "$COMMIT" "$ARCH" > "$PAYLOAD/MAC_BUILD.json"
cp "$PAYLOAD/READ-ME-FIRST-MAC.txt" "$WORK/image/READ ME FIRST.txt"
if [ "$STAGE_ONLY" = 1 ]; then
    echo "Windows staging preview: $PAYLOAD"
    echo 'No DMG was created or executed. Run the GitHub workflow for native verification.'
    exit 0
fi

# Test a separate writable copy so generated state never leaks into the disk image.
TEST_ROOT="$WORK/test install/DATA"
mkdir -p "$(dirname "$TEST_ROOT")" "$WORK/test-home"
ditto "$PAYLOAD" "$TEST_ROOT"
export HOME="$WORK/test-home"
export HERMES_DIR="$HOME/.local/share/hermes"
export PYTHONDONTWRITEBYTECODE=1
bash "$TEST_ROOT/install/install.sh" --offline
bash "$TEST_ROOT/start_data.sh" --check
"$TEST_ROOT/runtime/python/bin/python3" tools/test_mac_payload.py "$TEST_ROOT" "$ARCH"
"$TEST_ROOT/runtime/python/bin/python3" tools/test_mac_first_run.py

DMG="$ROOT/dist/DATA-v$VERSION-$ARCH.dmg"
[ ! -e "$DMG" ] || { echo "Artifact already exists: $DMG" >&2; exit 1; }
hdiutil create -quiet -volname "DATA $VERSION $ARCH" -srcfolder "$WORK/image" -format UDZO -fs HFS+ "$DMG"
hdiutil verify "$DMG"
mkdir "$WORK/mounted"
hdiutil attach -quiet -readonly -nobrowse -mountpoint "$WORK/mounted" "$DMG"
trap 'hdiutil detach -quiet "$WORK/mounted" || true' EXIT
cmp "$PAYLOAD/MAC_BUILD.json" "$WORK/mounted/DATA/MAC_BUILD.json"
cmp "$PAYLOAD/dashboard/bridge_server.py" "$WORK/mounted/DATA/dashboard/bridge_server.py"
cmp "$PAYLOAD/READ-ME-FIRST-MAC.txt" "$WORK/mounted/READ ME FIRST.txt"
cmp "$PAYLOAD/READ-ME-FIRST-MAC.txt" "$WORK/mounted/DATA/READ-ME-FIRST-MAC.txt"
"$WORK/mounted/DATA/runtime/python/bin/python3" -c 'import psutil, ssl, sqlite3; print("Mounted DMG runtime OK")'
hdiutil detach -quiet "$WORK/mounted"
trap - EXIT
cp "$PAYLOAD/MAC_BUILD.json" "$DMG.build.json"
(cd "$ROOT/dist" && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "DMG verified: $DMG"
