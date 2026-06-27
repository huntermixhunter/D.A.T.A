#!/usr/bin/env python3
"""
install_skills.py  —  INSTALL side (run once on a fresh DAITA install)

Copies the curated DAITA-core skill set that ships in skills_bundle/ OUT to the two
directories the bridge discovers skills from, so a fresh machine has the full
DAITA-core toolset on first launch — no manual downloading.

    repo bundle  ──copy──>  live dirs
    skills_bundle/claude/<name>/        ->  ~/.claude/skills/<name>/
    skills_bundle/hermes/<cat>/<name>/  ->  <hermes>/skills/<cat>/<name>/

Discovery dirs are resolved EXACTLY the way bridge_server.py resolves them
(honors $HERMES_DIR, AppData on Windows, ~/.local/share/hermes elsewhere).

Idempotent: a skill already present in the live dir is left untouched (so a
user's own newer copy is never clobbered) unless --force is given.

Usage:
    python install_skills.py            # install bundled skills that are missing
    python install_skills.py --force    # overwrite even if already present
    python install_skills.py --dry-run  # show what would happen, change nothing
"""
import json
import os
import shutil
import sys
from pathlib import Path

# Make stdout/stderr safe on any console codepage. The Windows installer runs
# this with a hidden, non-UTF-8 pipe (cp1252), where printing a non-ASCII glyph
# (e.g. an em-dash) would raise UnicodeEncodeError and crash with exit code 1.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
BUNDLE_DIR = HERE / "skills_bundle"
MANIFEST = BUNDLE_DIR / "manifest.json"

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
FORCE = "--force" in sys.argv

_IGNORE = shutil.ignore_patterns(".git", ".github", "__pycache__", "*.pyc",
                                 ".DS_Store", "node_modules", ".venv", "venv")


def _has_manifest(d: Path) -> bool:
    return (d / "SKILL.md").exists() or (d / "skill.md").exists()


def _install(src: Path, dst: Path, label: str, stats: dict) -> None:
    if not src.is_dir():
        stats["missing"].append(label)
        print(f"  MISS  {label}  (not in bundle)")
        return
    # One unusual target (a junction/symlink, a permission-protected path, a
    # reparse point the install token cannot traverse -> WinError 448) must never
    # abort the whole skill install. Guard every filesystem touch per-skill.
    try:
        if dst.exists():
            if not FORCE:
                stats["skipped"] += 1
                print(f"  SKIP  {label}  (already installed)")
                return
            if not DRY:
                shutil.rmtree(dst)
        if not DRY:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, ignore=_IGNORE)
            if not _has_manifest(dst):
                print(f"  WARN  {label}  (copied, but no SKILL.md/skill.md found)")
    except OSError as e:
        stats["failed"].append(label)
        print(f"  FAIL  {label}  ({e.__class__.__name__}: {e})")
        return
    stats["installed"] += 1
    print(f"  {'(dry) ' if DRY else 'OK   '}{label}")


def main() -> int:
    if not MANIFEST.exists():
        print(f"ERROR: manifest not found: {MANIFEST}")
        print("Nothing to install. (Was the bundle shipped with this copy?)")
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    print(f"Bundle:          {BUNDLE_DIR}")
    print(f"Target (Claude): {CLAUDE_SKILLS_DIR}")
    print(f"Target (Hermes): {HERMES_SKILLS_DIR}")
    print(f"Mode:            {'DRY-RUN' if DRY else ('FORCE overwrite' if FORCE else 'install-missing')}\n")

    stats = {"installed": 0, "skipped": 0, "missing": [], "failed": []}

    for name in man.get("claude", []):
        _install(BUNDLE_DIR / "claude" / name,
                 CLAUDE_SKILLS_DIR / name,
                 f"claude/{name}", stats)

    for ent in man.get("hermes", []):
        cat, name = ent["category"], ent["name"]
        _install(BUNDLE_DIR / "hermes" / cat / name,
                 HERMES_SKILLS_DIR / cat / name,
                 f"hermes/{cat}/{name}", stats)

    print(f"\nInstalled: {stats['installed']}   "
          f"Skipped (already present): {stats['skipped']}   "
          f"Missing from bundle: {len(stats['missing'])}   "
          f"Failed: {len(stats['failed'])}")
    if stats["missing"]:
        print("\nMissing from bundle (maintainer should run bundle_skills.py):")
        for m in stats["missing"]:
            print(f"  - {m}")
    if stats["failed"]:
        print("\nFailed to install (left for the user to add manually):")
        for m in stats["failed"]:
            print(f"  - {m}")
    print("\nDone. New skills appear on DAITA's next message - no restart needed.")
    return 0


if __name__ == "__main__":
    # Never let an unexpected error fail the installer's skill step. Skills are a
    # convenience layer; a partial or failed install must not break the product.
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as e:
        print(f"\nSkill install hit an unexpected error and was skipped: {e}")
        raise SystemExit(0)
