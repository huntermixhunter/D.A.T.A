#!/usr/bin/env python3
"""
uninstall_skills.py  —  UNINSTALL side (run when removing DAITA)

Reverses install_skills.py: removes the curated DAITA-core skills that DAITA
copied into the two skill-discovery directories on install.

    live dirs                                   ──remove──>  gone
    ~/.claude/skills/<name>/                          (if DAITA put it there)
    <hermes>/skills/<cat>/<name>/                      (if DAITA put it there)

SAFETY — never delete a skill you actually use.
install_skills.py "never clobbers your own copies": on install it SKIPS any
skill already present, so a skill you authored or installed yourself was left
untouched. The uninstaller honors the same contract in reverse: a live skill is
removed ONLY when it is byte-for-byte identical to the copy that ships in
skills_bundle/ — i.e. DAITA placed it and you have not modified it. If the live
copy differs in any file (you edited it, or it was your own pre-existing copy),
it is KEPT and reported, never deleted. Skills not listed in the manifest are
never touched at all.

    --force     remove a bundled skill even if it differs from the bundle
                (use only if you are sure you want every DAITA-core skill gone)
    --dry-run   show what would be removed, change nothing

Discovery dirs are resolved EXACTLY the way bridge_server.py / install_skills.py
resolve them (honors $HERMES_DIR, AppData on Windows, ~/.local/share elsewhere).
"""
import filecmp
import json
import os
import shutil
import sys
from pathlib import Path

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

# Mirror install_skills.py's copy ignores so an "identical" check is fair:
# files the installer never copied must not count as a difference.
_IGNORED_NAMES = {".git", ".github", "__pycache__", ".DS_Store",
                  "node_modules", ".venv", "venv"}


def _dirs_identical(a: Path, b: Path) -> bool:
    """True if every file under bundle dir `a` exists and matches under live dir
    `b`. Extra files under `b` (e.g. the user added something) count as a
    difference, so we err toward keeping a skill the user may have touched."""
    cmp = filecmp.dircmp(a, b, ignore=list(_IGNORED_NAMES))
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    for common_dir in cmp.common_dirs:
        if not _dirs_identical(a / common_dir, b / common_dir):
            return False
    return True


def _remove(name: str, bundle: Path, live: Path, label: str, stats: dict) -> None:
    if not live.exists():
        stats["absent"] += 1
        print(f"  --    {label}  (not installed)")
        return
    safe = bundle.is_dir() and _dirs_identical(bundle, live)
    if not safe and not FORCE:
        stats["kept"].append(label)
        reason = "modified / your own copy" if bundle.is_dir() else "not in bundle"
        print(f"  KEEP  {label}  ({reason} - left in place)")
        return
    if not DRY:
        shutil.rmtree(live, ignore_errors=True)
    stats["removed"] += 1
    print(f"  {'(dry) ' if DRY else 'GONE '}{label}{'  [forced]' if (FORCE and not safe) else ''}")


def main() -> int:
    if not MANIFEST.exists():
        print(f"ERROR: manifest not found: {MANIFEST}")
        print("Cannot tell which skills DAITA installed. Nothing removed.")
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))

    print(f"Bundle:          {BUNDLE_DIR}")
    print(f"Target (Claude): {CLAUDE_SKILLS_DIR}")
    print(f"Target (Hermes): {HERMES_SKILLS_DIR}")
    print(f"Mode:            {'DRY-RUN' if DRY else ('FORCE remove' if FORCE else 'remove-unmodified-only')}\n")

    stats = {"removed": 0, "absent": 0, "kept": []}

    for name in man.get("claude", []):
        _remove(name, BUNDLE_DIR / "claude" / name,
                CLAUDE_SKILLS_DIR / name, f"claude/{name}", stats)

    for ent in man.get("hermes", []):
        cat, name = ent["category"], ent["name"]
        _remove(name, BUNDLE_DIR / "hermes" / cat / name,
                HERMES_SKILLS_DIR / cat / name, f"hermes/{cat}/{name}", stats)

    print(f"\nRemoved: {stats['removed']}   "
          f"Already absent: {stats['absent']}   "
          f"Kept (modified/yours): {len(stats['kept'])}")
    if stats["kept"]:
        print("\nKept in place (you modified these or had your own copy):")
        for k in stats["kept"]:
            print(f"  - {k}")
        print("Re-run with --force to remove these too.")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
