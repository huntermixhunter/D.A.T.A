#!/usr/bin/env python3
"""
build_skill_library.py  -  MAINTAINER side of the optional Skill Library.

The Skill Library is the set of extra skills a DATA user can add (and remove)
one at a time from Settings > Upgrades > Skill Library. Unlike the DATA-core
bundle (dashboard/skills_bundle/), these files are NOT inside any download:

  * They live in the repo at skills_library/ , which .gitattributes marks
    export-ignore, so the Windows zip, the Windows installer and the Mac DMG
    (all built with `git archive`) leave them out.
  * The self-updater only syncs dashboard/ , so existing installs never pull
    them either.
  * dashboard/skill_library.py downloads ONE skill from GitHub when the user
    clicks Install, verifying every file against GitHub's own blob SHA.

This script copies each listed skill FROM your live skill folders INTO
skills_library/ and writes the catalog the UI reads:
dashboard/skill_library_catalog.json (which does ship and self-updates).

Hermes skills nested deeper than <category>/<name> (e.g. mlops/inference/vllm)
are flattened to <top-category>/<name>, because the bridge only discovers
Hermes skills exactly two levels deep.

Usage (from the repo root):
    python tools/build_skill_library.py            # rebuild skills_library/ + catalog
    python tools/build_skill_library.py --dry-run  # report only
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "skills_library"
CATALOG = ROOT / "dashboard" / "skill_library_catalog.json"
BUNDLE_MANIFEST = ROOT / "dashboard" / "skills_bundle" / "manifest.json"

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

_IGNORE = shutil.ignore_patterns(".git", ".github", "__pycache__", "*.pyc", ".DS_Store",
                                 "node_modules", ".venv", "venv", ".data-library.json")

# Group ids -> labels shown in the UI filter.
GROUPS = {
    "build":    "Plan and build",
    "agents":   "Coding agents",
    "debug":    "Debugging",
    "design":   "Design and creative",
    "business": "Business and productivity",
    "research": "Research",
    "account":  "Needs an account or app",
    "ml":       "AI models (advanced)",
    "fun":      "Fun and niche",
}

# (skill, source, group, needs). source "claude" = ~/.claude/skills/<skill>,
# otherwise the Hermes path under <hermes>/skills. `needs` is shown on the card
# so nobody installs a skill expecting it to work without its account or app.
LIBRARY = [
    ("plan", "claude", "build", ""),
    ("spike", "claude", "build", ""),
    ("subagent-driven-development", "claude", "build", ""),
    ("agent-teams", "claude", "build", ""),
    ("github-issues", "claude", "build", "GitHub CLI (gh) signed in"),
    ("changelog-generator", "claude", "build", ""),
    ("full-output-enforcement", "claude", "build", ""),
    ("claude-code", "claude", "agents", "Claude Code CLI"),
    ("codex", "claude", "agents", "OpenAI Codex CLI"),
    ("opencode", "claude", "agents", "OpenCode CLI"),
    ("node-inspect-debugger", "claude", "debug", "Node.js"),
    ("python-debugpy", "software-development/python-debugpy", "debug", "Python"),
    ("jupyter-live-kernel", "claude", "debug", "Python with Jupyter"),
    ("excalidraw", "claude", "design", ""),
    ("architecture-diagram", "claude", "design", ""),
    ("artifacts-builder", "claude", "design", "Node.js"),
    ("popular-web-designs", "creative/popular-web-designs", "design", ""),
    ("sketch", "creative/sketch", "design", ""),
    ("claude-design", "creative/claude-design", "design", ""),
    ("design-md", "creative/design-md", "design", ""),
    ("creative-ideation", "creative/creative-ideation", "design", ""),
    ("p5js", "claude", "design", ""),
    ("ascii-art", "claude", "design", ""),
    ("remotion-best-practices", "claude", "design", "Node.js"),
    ("slack-gif-creator", "claude", "design", "Python"),
    ("file-organizer", "claude", "business", ""),
    ("invoice-organizer", "claude", "business", ""),
    ("image-enhancer", "claude", "business", ""),
    ("domain-name-brainstormer", "claude", "business", ""),
    ("lead-research-assistant", "claude", "business", ""),
    ("tailored-resume-generator", "claude", "business", ""),
    ("meeting-insights-analyzer", "claude", "business", ""),
    ("internal-comms", "claude", "business", ""),
    ("competitive-ads-extractor", "claude", "business", ""),
    ("twitter-algorithm-optimizer", "claude", "business", ""),
    ("research-paper-writing", "claude", "research", ""),
    ("huggingface-hub", "claude", "research", "Hugging Face CLI (hf)"),
    ("maps", "productivity/maps", "research", ""),
    ("blogwatcher", "research/blogwatcher", "research", "blogwatcher CLI"),
    ("llm-wiki", "research/llm-wiki", "research", ""),
    ("polymarket", "research/polymarket", "research", ""),
    ("connect", "claude", "account", "Composio account"),
    ("connect-apps", "claude", "account", "Composio account"),
    ("skill-share", "claude", "account", "Slack workspace and Composio account"),
    ("langsmith-fetch", "claude", "account", "LangSmith account and langsmith-fetch CLI"),
    ("airtable", "productivity/airtable", "account", "Airtable account and API token"),
    ("notion", "productivity/notion", "account", "Notion account and integration token"),
    ("linear", "productivity/linear", "account", "Linear account and API key"),
    ("xurl", "social-media/xurl", "account", "X developer app and xurl CLI"),
    ("spotify", "media/spotify", "account", "Spotify Premium and a Spotify developer app"),
    ("openhue", "smart-home/openhue", "account", "Philips Hue Bridge and openhue CLI"),
    ("himalaya", "email/himalaya", "account", "Email account and himalaya CLI"),
    ("comfyui", "creative/comfyui", "ml", "ComfyUI installed; a GPU is recommended"),
    ("touchdesigner-mcp", "creative/touchdesigner-mcp", "ml", "TouchDesigner and the twozero MCP server"),
    ("heartmula", "media/heartmula", "ml", "HeartMuLa model; a GPU is required"),
    ("vllm", "mlops/inference/vllm", "ml", "vLLM and an NVIDIA GPU"),
    ("llama-cpp", "mlops/inference/llama-cpp", "ml", "llama.cpp"),
    ("audiocraft", "mlops/models/audiocraft", "ml", "AudioCraft; a GPU is recommended"),
    ("segment-anything", "mlops/models/segment-anything", "ml", "Segment Anything; a GPU is recommended"),
    ("dspy", "mlops/research/dspy", "ml", "DSPy and an AI model API key"),
    ("lm-evaluation-harness", "mlops/evaluation/lm-evaluation-harness", "ml", "lm-eval; a GPU or model API key"),
    ("weights-and-biases", "mlops/evaluation/weights-and-biases", "ml", "Weights and Biases account"),
    ("roblox-game", "gaming/roblox-game", "fun", "Roblox Studio"),
    ("minecraft-modpack-server", "gaming/minecraft-modpack-server", "fun", "Java"),
    ("pokemon-player", "gaming/pokemon-player", "fun", "Your own legally obtained game file"),
    ("manim-video", "creative/manim-video", "fun", "Manim Community Edition"),
    ("ascii-video", "creative/ascii-video", "fun", "ffmpeg"),
    ("baoyu-comic", "creative/baoyu-comic", "fun", "An image generation tool"),
    ("baoyu-infographic", "creative/baoyu-infographic", "fun", "An image generation tool"),
    ("pixel-art", "creative/pixel-art", "fun", "Python with Pillow"),
    ("songwriting-and-ai-music", "creative/songwriting-and-ai-music", "fun", ""),
    ("songsee", "media/songsee", "fun", "songsee CLI"),
    ("gif-search", "media/gif-search", "fun", "curl and jq"),
    ("pretext", "creative/pretext", "fun", ""),
]

_SAFE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _frontmatter_description(md: Path) -> str:
    try:
        text = md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return ""
    lines = m.group(1).splitlines()
    for i, line in enumerate(lines):
        dm = re.match(r"^description:\s*(.*)$", line)
        if not dm:
            continue
        val = dm.group(1).strip()
        if val in ("|", ">", "|-", ">-", ""):
            parts = []
            for nxt in lines[i + 1:]:
                if nxt and not nxt.startswith((" ", "\t")):
                    break
                parts.append(nxt.strip())
            val = " ".join(p for p in parts if p)
        val = val.strip().strip('"').strip("'")
        # House style: no em dashes in anything DATA shows.
        val = val.replace(" — ", ", ").replace("—", ", ").replace("–", "-")
        val = re.sub(r"\s+", " ", val)
        if len(val) > 240:
            val = val[:237].rsplit(" ", 1)[0] + "..."
        return val
    return ""


def _dir_stats(d: Path) -> tuple:
    n = b = 0
    for r, ds, fs in os.walk(d):
        ds[:] = [x for x in ds if x not in ("node_modules", ".git", "__pycache__", ".venv", "venv", ".github")]
        for f in fs:
            if f in (".DS_Store", ".data-library.json") or f.endswith(".pyc"):
                continue
            n += 1
            b += (Path(r) / f).stat().st_size
    return n, b


def main() -> int:
    core = json.loads(BUNDLE_MANIFEST.read_text(encoding="utf-8"))
    core_claude = set(core.get("claude", []))
    core_hermes = {e["name"] for e in core.get("hermes", [])}

    entries, problems = [], []
    seen = set()
    if not DRY and LIB_DIR.exists():
        for sub in ("claude", "hermes"):
            if (LIB_DIR / sub).exists():
                shutil.rmtree(LIB_DIR / sub)

    for name, source, group, needs in LIBRARY:
        if name in seen or name in core_claude or name in core_hermes:
            problems.append(f"{name}: duplicate or already in the core bundle")
            continue
        seen.add(name)
        if group not in GROUPS:
            problems.append(f"{name}: unknown group {group}")
            continue
        if source == "claude":
            src = CLAUDE_SKILLS_DIR / name
            kind, category, md_name = "claude", "", "SKILL.md"
            rel = f"skills_library/claude/{name}"
        else:
            src = HERMES_SKILLS_DIR / source
            kind, category, md_name = "hermes", source.split("/")[0], "skill.md"
            rel = f"skills_library/hermes/{category}/{name}"
        if not (_SAFE.match(name) and (not category or _SAFE.match(category))):
            problems.append(f"{name}: unsafe name or category")
            continue
        if not (src / md_name).exists():
            problems.append(f"{name}: {src / md_name} not found")
            continue
        files, size = _dir_stats(src)
        entries.append({
            "id": name,
            "name": name,
            "kind": kind,
            "category": category,
            "group": group,
            "description": _frontmatter_description(src / md_name),
            "needs": needs,
            "files": files,
            "kb": max(1, round(size / 1024)),
            "path": rel,
        })
        if not DRY:
            dst = ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst, ignore=_IGNORE)
        print(f"  {'(dry) ' if DRY else 'OK   '}{rel}  ({files} files, {size / 1024:.0f} KB)")

    catalog = {
        "_comment": ("Optional Skill Library shown in Settings > Upgrades. Built by "
                     "tools/build_skill_library.py. Files live in skills_library/ on GitHub "
                     "and are downloaded one skill at a time; they are not in any download."),
        "version": 1,
        "groups": GROUPS,
        "skills": sorted(entries, key=lambda e: (list(GROUPS).index(e["group"]), e["name"])),
    }
    if not DRY:
        CATALOG.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    total_kb = sum(e["kb"] for e in entries)
    print(f"\n{len(entries)} skills, about {total_kb / 1024:.1f} MB in skills_library/")
    if problems:
        print("\nProblems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
