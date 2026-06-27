#!/usr/bin/env python3
"""
bundle_skills.py  —  MAINTAINER side (run on the dev machine, not on a fresh install)

Reads skills_bundle/manifest.json and copies each listed skill FROM your live
discovery dirs INTO skills_bundle/ so the curated DATA-core set physically travels
with the repo (and therefore with any zip or git clone).

    Live dirs  ──copy──>  repo bundle
    ~/.claude/skills/<name>                 ->  skills_bundle/claude/<name>/
    <hermes>/skills/<cat>/<name>            ->  skills_bundle/hermes/<cat>/<name>/

Discovery dirs are resolved EXACTLY the way bridge_server.py resolves them, so
this stays in lockstep with the server.

Usage:
    python bundle_skills.py            # refresh bundle from live dirs (overwrites bundled copies)
    python bundle_skills.py --clean    # wipe the bundle's claude/ and hermes/ first, then refresh
    python bundle_skills.py --dry-run  # show what would be copied, change nothing
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE_DIR = HERE / "skills_bundle"
MANIFEST = BUNDLE_DIR / "manifest.json"

# ── Resolve discovery dirs the same way bridge_server.py does ──────────────────
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
_hermes_env = os.environ.get("HERMES_DIR", "").strip()
if _hermes_env:
    HERMES_DIR = Path(_hermes_env)
elif sys.platform == "win32":
    HERMES_DIR = Path.home() / "AppData" / "Local" / "hermes"
else:
    HERMES_DIR = Path.home() / ".local" / "share" / "hermes"
HERMES_SKILLS_DIR = HERMES_DIR / "skills"

DRY = "--dry-run" in sys.argv
CLEAN = "--clean" in sys.argv

# Skip VCS / cache cruft when copying skill folders into the repo.
_IGNORE = shutil.ignore_patterns(".git", ".github", "__pycache__", "*.pyc",
                                 ".DS_Store", "node_modules", ".venv", "venv")


def _copytree(src: Path, dst: Path) -> None:
    if DRY:
        return
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=_IGNORE)


def main() -> int:
    if not MANIFEST.exists():
        print(f"ERROR: manifest not found: {MANIFEST}")
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if CLEAN and not DRY:
        for sub in ("claude", "hermes"):
            p = BUNDLE_DIR / sub
            if p.exists():
                shutil.rmtree(p)
        print("Cleaned bundle/claude and bundle/hermes.")

    copied, missing = 0, []

    print(f"Source (Claude): {CLAUDE_SKILLS_DIR}")
    print(f"Source (Hermes): {HERMES_SKILLS_DIR}")
    print(f"Bundle target:   {BUNDLE_DIR}\n")

    for name in man.get("claude", []):
        src = CLAUDE_SKILLS_DIR / name
        if not src.is_dir():
            missing.append(f"claude/{name}")
            print(f"  MISS  claude/{name}  (not in {CLAUDE_SKILLS_DIR})")
            continue
        _copytree(src, BUNDLE_DIR / "claude" / name)
        copied += 1
        print(f"  {'(dry) ' if DRY else 'OK   '}claude/{name}")

    for ent in man.get("hermes", []):
        cat, name = ent["category"], ent["name"]
        src = HERMES_SKILLS_DIR / cat / name
        if not src.is_dir():
            missing.append(f"hermes/{cat}/{name}")
            print(f"  MISS  hermes/{cat}/{name}  (not in {HERMES_SKILLS_DIR})")
            continue
        _copytree(src, BUNDLE_DIR / "hermes" / cat / name)
        copied += 1
        print(f"  {'(dry) ' if DRY else 'OK   '}hermes/{cat}/{name}")

    # Bundle size report
    total = 0
    if (BUNDLE_DIR).exists():
        for f in BUNDLE_DIR.rglob("*"):
            if f.is_file():
                total += f.stat().st_size

    print(f"\n{'Would copy' if DRY else 'Copied'}: {copied} skills"
          f"   Missing from live dirs: {len(missing)}")
    print(f"Bundle size: {total / 1_048_576:.1f} MB")
    if missing:
        print("\nMissing (install them locally first, or remove from manifest):")
        for m in missing:
            print(f"  - {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
