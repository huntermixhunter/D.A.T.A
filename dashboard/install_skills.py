#!/usr/bin/env python3
"""
install_skills.py  —  INSTALL side (run on a fresh DATA install, and after updates)

Copies the curated DATA-core skill set that ships in skills_bundle/ OUT to the two
directories the bridge discovers skills from, so a fresh machine has the full
DATA-core toolset on first launch — no manual downloading.

    repo bundle  ──copy──>  live dirs
    skills_bundle/claude/<name>/        ->  ~/.claude/skills/<name>/
    skills_bundle/hermes/<cat>/<name>/  ->  <hermes>/skills/<cat>/<name>/

Discovery dirs are resolved EXACTLY the way bridge_server.py resolves them
(honors $HERMES_DIR, AppData on Windows, ~/.local/share/hermes elsewhere).

Platform filter: a manifest entry may carry "platforms": ["darwin"] (values are
sys.platform names). It is installed only on those systems, so the Mac skills
ride along in every download but only land on a Mac.

Modes
    python install_skills.py            # install bundled skills that are missing
    python install_skills.py --force    # overwrite even if already present
    python install_skills.py --sync     # after a self-update: see below
    python install_skills.py --dry-run  # show what would happen, change nothing

--sync is what the self-updater and the bridge run after new bundle files
arrive. It (1) installs skills that are new to the bundle, and (2) refreshes a
skill DATA installed earlier ONLY if the live copy is still exactly what DATA
put there. A skill the user edited is never overwritten, and a bundled skill
the user deleted is not brought back. What DATA installed is recorded (as a
content fingerprint per skill) in skills_bundle/.install_state.json.
"""
import hashlib
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
STATE_FILE = BUNDLE_DIR / ".install_state.json"

CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"
_hermes_env = os.environ.get("HERMES_DIR", "").strip()
if _hermes_env:
    HERMES_DIR = Path(_hermes_env)
elif sys.platform == "win32":
    HERMES_DIR = Path.home() / "AppData" / "Local" / "hermes"
else:
    HERMES_DIR = Path.home() / ".local" / "share" / "hermes"
HERMES_SKILLS_DIR = HERMES_DIR / "skills"

_IGNORED_NAMES = {".git", ".github", "__pycache__", ".DS_Store",
                  "node_modules", ".venv", "venv",
                  ".data-library.json"}   # Skill Library install marker
_IGNORE = shutil.ignore_patterns(".git", ".github", "__pycache__", "*.pyc",
                                 ".DS_Store", "node_modules", ".venv", "venv")


def _has_manifest(d: Path) -> bool:
    return (d / "SKILL.md").exists() or (d / "skill.md").exists()


def fingerprint(d: Path) -> str:
    """Content fingerprint of a skill folder. Paths are compared without case
    so a SKILL.md -> skill.md rename does not read as a user edit."""
    h = hashlib.sha256()
    files = []
    for root, dirs, names in os.walk(d):
        dirs[:] = sorted(x for x in dirs if x not in _IGNORED_NAMES)
        for n in names:
            if n in _IGNORED_NAMES or n.endswith(".pyc"):
                continue
            p = Path(root) / n
            files.append((p.relative_to(d).as_posix().lower(), p))
    for rel, p in sorted(files):
        h.update(rel.encode("utf-8") + b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()


def _normalize_hermes(dst: Path) -> None:
    """The bridge looks for <cat>/<name>/skill.md (lowercase). Windows and
    default macOS disks ignore case, but a case-sensitive disk (Linux) would
    hide a skill stored as SKILL.md, so rename only where it matters."""
    if (dst / "skill.md").exists():
        return
    upper = dst / "SKILL.md"
    if upper.exists():
        os.rename(upper, dst / "skill.md")


def _platform_ok(ent) -> bool:
    plats = ent.get("platforms") if isinstance(ent, dict) else None
    return not plats or sys.platform in plats


def _entries(man: dict):
    """Yield (label, src, dst, is_hermes, entry) for every manifest entry."""
    for ent in man.get("claude", []):
        name = ent if isinstance(ent, str) else ent["name"]
        yield (f"claude/{name}", BUNDLE_DIR / "claude" / name,
               CLAUDE_SKILLS_DIR / name, False, ent)
    for ent in man.get("hermes", []):
        cat, name = ent["category"], ent["name"]
        yield (f"hermes/{cat}/{name}", BUNDLE_DIR / "hermes" / cat / name,
               HERMES_SKILLS_DIR / cat / name, True, ent)


def _load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return doc.get("installed", {}) if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(installed: dict, dry: bool) -> None:
    if dry:
        return
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"installed": installed}, indent=1, sort_keys=True),
                       encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"  WARN  could not save install state: {e}")


def _copy(src: Path, dst: Path, is_hermes: bool) -> str:
    """Replace dst with a fresh copy of src. Returns the new fingerprint."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=_IGNORE)
    if is_hermes:
        _normalize_hermes(dst)
    if not _has_manifest(dst):
        print(f"  WARN  {dst.name}  (copied, but no SKILL.md/skill.md found)")
    return fingerprint(dst)


def run(mode: str = "missing", dry: bool = False, quiet: bool = False) -> dict:
    """mode: "missing" (default), "force" or "sync". Returns a stats dict.
    Never raises for a single bad skill; failures are listed in the result."""
    say = (lambda *a: None) if quiet else print
    stats = {"installed": [], "refreshed": [], "skipped": 0, "kept": [],
             "missing": [], "failed": [], "other_platform": 0}
    if not MANIFEST.exists():
        stats["failed"].append(f"manifest not found: {MANIFEST}")
        return stats
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    installed = _load_state()

    for label, src, dst, is_hermes, ent in _entries(man):
        if not _platform_ok(ent):
            stats["other_platform"] += 1
            continue
        if not src.is_dir():
            stats["missing"].append(label)
            say(f"  MISS  {label}  (not in bundle)")
            continue
        # One unusual target (a junction/symlink, a permission-protected path, a
        # reparse point the install token cannot traverse -> WinError 448) must
        # never abort the whole skill install. Guard every touch per skill.
        try:
            if not dst.exists():
                if mode == "sync" and label in installed:
                    stats["kept"].append(label)          # user removed it; respect that
                    say(f"  KEEP  {label}  (you removed it; not reinstalled)")
                    continue
                if not dry:
                    installed[label] = _copy(src, dst, is_hermes)
                stats["installed"].append(label)
                say(f"  {'(dry) ' if dry else 'OK   '}{label}")
                continue
            if mode == "force":
                if not dry:
                    installed[label] = _copy(src, dst, is_hermes)
                stats["refreshed"].append(label)
                say(f"  {'(dry) ' if dry else 'OK   '}{label}  [overwritten]")
                continue
            live = fingerprint(dst)
            bundled = fingerprint(src)
            if live == bundled:
                installed[label] = live                  # already current
                stats["skipped"] += 1
                say(f"  SKIP  {label}  (up to date)")
                continue
            if mode == "sync" and installed.get(label) == live:
                if not dry:
                    installed[label] = _copy(src, dst, is_hermes)
                stats["refreshed"].append(label)
                say(f"  {'(dry) ' if dry else 'NEW  '}{label}  [updated]")
                continue
            stats["skipped"] += 1
            if mode == "sync":
                stats["kept"].append(label)
                say(f"  KEEP  {label}  (your own or edited copy, left alone)")
            else:
                say(f"  SKIP  {label}  (already installed)")
        except OSError as e:
            stats["failed"].append(label)
            say(f"  FAIL  {label}  ({e.__class__.__name__}: {e})")

    _save_state(installed, dry)
    return stats


def sync_quiet() -> dict:
    """Entry point for the self-updater and bridge startup."""
    try:
        return run("sync", quiet=True)
    except Exception as e:                               # never break an update
        return {"installed": [], "refreshed": [], "failed": [f"sync error: {e}"]}


def main() -> int:
    dry = "--dry-run" in sys.argv
    mode = "force" if "--force" in sys.argv else ("sync" if "--sync" in sys.argv else "missing")
    if not MANIFEST.exists():
        print(f"ERROR: manifest not found: {MANIFEST}")
        print("Nothing to install. (Was the bundle shipped with this copy?)")
        return 1

    print(f"Bundle:          {BUNDLE_DIR}")
    print(f"Target (Claude): {CLAUDE_SKILLS_DIR}")
    print(f"Target (Hermes): {HERMES_SKILLS_DIR}")
    print(f"Mode:            {'DRY-RUN ' if dry else ''}"
          f"{ {'force': 'FORCE overwrite', 'sync': 'sync after update', 'missing': 'install-missing'}[mode] }\n")

    stats = run(mode, dry=dry)

    print(f"\nInstalled: {len(stats['installed'])}   Updated: {len(stats['refreshed'])}   "
          f"Skipped: {stats['skipped']}   For other systems: {stats['other_platform']}   "
          f"Missing from bundle: {len(stats['missing'])}   Failed: {len(stats['failed'])}")
    if stats["missing"]:
        print("\nMissing from bundle (maintainer should run bundle_skills.py):")
        for m in stats["missing"]:
            print(f"  - {m}")
    if stats["failed"]:
        print("\nFailed to install (left for the user to add manually):")
        for m in stats["failed"]:
            print(f"  - {m}")
    print("\nDone. New skills appear on DATA's next message - no restart needed.")
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
