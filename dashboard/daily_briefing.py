#!/usr/bin/env python3
"""
DATA — Potential Upgrades scanner
---------------------------------
Scans curated public sources for new AI tools, MCP servers, Claude Code
skills, and Anthropic releases. Filters by relevance, ranks the top 5-8,
and writes `daily_briefing.json` in the DATA install folder.

Runs from the dashboard's POTENTIAL UPGRADES panel (POST /briefing/refresh)
or on a standing-order schedule. Can also be invoked manually:

Usage:  python daily_briefing.py
"""

import os
import sys
import json
import uuid
import datetime
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_DIR   = Path(__file__).parent.parent.resolve()   # the DATA install folder
BRIEFING_FILE = PROJECT_DIR / "daily_briefing.json"

# ── Curated sources ──────────────────────────────────────────
# format: "markdown" = raw text, "github-search" = parsed JSON repos list
SOURCES = [
    {
        "name":   "MCP Servers (official)",
        "url":    "https://raw.githubusercontent.com/modelcontextprotocol/servers/main/README.md",
        "format": "markdown",
        "type":   "mcp-server",
        "max_chars": 15000,
    },
    {
        "name":   "Awesome MCP Servers (community)",
        "url":    "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
        "format": "markdown",
        "type":   "mcp-server",
        "max_chars": 15000,
    },
    {
        "name":   "Claude Code Release Notes",
        "url":    "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md",
        "format": "markdown",
        "type":   "claude-update",
        "max_chars": 12000,
    },
    {
        "name":   "GitHub: recent MCP server repos",
        "url":    "https://api.github.com/search/repositories?q=topic%3Amcp-server&sort=updated&order=desc&per_page=20",
        "format": "github-search",
        "type":   "mcp-server",
        "max_chars": 6000,
    },
    {
        "name":   "GitHub: top Claude Code projects",
        "url":    "https://api.github.com/search/repositories?q=topic%3Aclaude-code&sort=stars&order=desc&per_page=20",
        "format": "github-search",
        "type":   "claude-skill",
        "max_chars": 6000,
    },
    {
        "name":   "Anthropic Cookbook",
        "url":    "https://raw.githubusercontent.com/anthropics/anthropic-cookbook/main/README.md",
        "format": "markdown",
        "type":   "link",
        "max_chars": 6000,
    },
]

USER_AGENT = "Mozilla/5.0 (compatible; DATA-Briefing/1.0)"


# ── Load API key (optional — CLI subscription is the normal path) ──
def _load_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    return ""


# ── Fetch helpers ────────────────────────────────────────────
def fetch(url: str, max_chars: int = 30000) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
            return data[:max_chars]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[fetch] {url} failed: {e}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[fetch] {url} unexpected error: {e}", file=sys.stderr)
        return ""


def format_github_search(raw: str, max_chars: int) -> str:
    """Convert GitHub search-API JSON into compact text the model can curate."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    items = data.get("items", [])
    lines = []
    for it in items:
        stars   = it.get("stargazers_count", 0)
        updated = (it.get("pushed_at") or it.get("updated_at") or "")[:10]
        name    = it.get("full_name", "")
        url     = it.get("html_url", "")
        desc    = (it.get("description") or "").strip().replace("\n", " ")
        topics  = ", ".join(it.get("topics", [])[:6])
        if not name or not url:
            continue
        lines.append(f"- {name}  ({stars:,}★, pushed {updated})  {url}")
        if desc:
            lines.append(f"    {desc[:200]}")
        if topics:
            lines.append(f"    topics: {topics}")
    return "\n".join(lines)[:max_chars]


def load_existing() -> dict:
    if BRIEFING_FILE.exists():
        try:
            return json.loads(BRIEFING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"generated_at": None, "items": []}


def _captain_context() -> str:
    """Short context block so the model can rank by relevance."""
    return (
        "The Captain runs DATA — the Dashboard for Analytical Thought and Action: "
        "a self-hosted, local-first AI operations dashboard driven by AI provider "
        "CLIs (Claude Code by default). DATA has tools for web search, file I/O, "
        "terminal, screenshots, persistent memory, and can dynamically load "
        "skills. The Captain likes: Claude Code skills, MCP servers, agentic "
        "patterns, dashboard UX, automation, and anything that makes the system "
        "smarter or more capable."
    )


# ── Briefing generation ──────────────────────────────────────
def _build_prompt(source_contents: list[tuple[dict, str]]) -> str:
    context = _captain_context()
    parts = [
        "You are curating a daily briefing for the Captain on new AI tools and capabilities. "
        "Your job: pick the 5-8 most interesting items across these sources for the Captain to consider trying.",
        "",
        "CAPTAIN CONTEXT:",
        context,
        "",
        "SOURCES (raw text — extract entries with names/links from each):",
    ]
    for src, content in source_contents:
        if not content:
            continue
        parts.append(f"\n### Source: {src['name']} (type: {src['type']})")
        parts.append(content)

    parts.append(
        "\n\nReturn ONLY a JSON array (no prose, no markdown fences) of 5-8 objects with this exact shape:\n"
        '[{\n'
        '  "title": "short name of the tool/skill/feature",\n'
        '  "source": "which source above this came from",\n'
        '  "url": "direct link to repo, docs, or release notes",\n'
        '  "summary": "1 sentence — what it is",\n'
        '  "why_relevant": "1 sentence — why the Captain might want this",\n'
        '  "install_type": "mcp-server | claude-skill | pip-package | link",\n'
        '  "install_hint": "exact command or path, or empty string if just a link to read"\n'
        '}]\n\n'
        "Pick variety — do not return 8 MCP servers if there are also interesting skills. "
        "Skip anything trivial, deprecated, or unrelated to the system's capabilities. "
        "Prefer recently-updated and well-maintained items."
    )
    return "\n".join(parts)


def _call_claude_api(prompt: str, api_key: str) -> str:
    """Try the Anthropic API. Returns raw text, or raises on failure."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",  # Haiku is plenty for curation
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _find_claude_exe() -> str:
    """Locate the claude executable across platforms and install methods."""
    import shutil
    for candidate in ("claude", "claude.exe", "claude.cmd"):
        found = shutil.which(candidate)
        if found:
            return found
    candidates = [
        Path.home() / ".local" / "bin" / "claude",
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / "AppData" / "Local" / "AnthropicClaude" / "claude.exe",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "claude.exe",
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["cmd", "/c", "where", "claude"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                p = line.strip()
                if p and Path(p).exists():
                    return p
        except Exception:
            pass
    raise FileNotFoundError(
        "claude executable not found (checked PATH + known install dirs). "
        "Install: https://docs.claude.com/en/docs/claude-code"
    )


def _call_claude_cli(prompt: str) -> str:
    """
    Use the claude CLI (subscription). Pipes the prompt via stdin because
    Windows command-line args are capped at ~32KB and the briefing prompt can
    exceed that. Strips ANTHROPIC_API_KEY so the CLI uses subscription auth.
    """
    claude_exe = _find_claude_exe()
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    result = subprocess.run(
        [claude_exe, "--print", "--output-format", "text",
         "--model", "claude-haiku-4-5-20251001",
         "--max-turns", "1",
         "--dangerously-skip-permissions"],
        input=prompt,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exit {result.returncode}: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def generate_briefing() -> dict:
    # Fetch all sources and apply per-format post-processing
    source_contents = []
    for src in SOURCES:
        raw = fetch(src["url"], 800000)  # generous initial cap; per-format trims after parsing
        if src.get("format") == "github-search":
            content = format_github_search(raw, src["max_chars"])
        else:
            content = raw[:src["max_chars"]]
        source_contents.append((src, content))
        print(f"[briefing] {src['name']}: {len(content)} chars")

    if not any(c for _, c in source_contents):
        return {"error": "All sources failed to fetch"}

    prompt = _build_prompt(source_contents)
    print(f"[briefing] sending {len(prompt)} chars to the model...")

    api_key = _load_api_key()
    raw = ""
    last_err = None

    # Try API first if a key is configured
    if api_key:
        try:
            raw = _call_claude_api(prompt, api_key)
            print("[briefing] used API mode (Haiku)")
        except Exception as e:
            last_err = e
            print(f"[briefing] API failed: {e} — falling back to CLI", file=sys.stderr)

    # Normal path: CLI (subscription)
    if not raw:
        try:
            raw = _call_claude_cli(prompt)
            print("[briefing] used CLI mode (subscription)")
        except FileNotFoundError:
            return {"error": "Anthropic API unavailable and claude CLI not found on PATH"}
        except Exception as e:
            return {"error": f"Both API and CLI failed. API: {last_err} | CLI: {e}"}

    raw = raw.strip()
    # Strip markdown fences if the model added any
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

    try:
        new_items = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[briefing] could not parse the model's response: {e}", file=sys.stderr)
        print(f"[briefing] raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
        return {"error": "Model returned malformed JSON", "raw": raw[:500]}

    # Merge with existing briefing — preserve status of items we've seen before (by URL)
    existing = load_existing()
    existing_by_url = {it.get("url", ""): it for it in existing.get("items", [])}

    now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    merged = []
    for it in new_items:
        url = it.get("url", "")
        if url in existing_by_url:
            # Carry forward existing status and id
            prev = existing_by_url[url]
            it["id"] = prev["id"]
            it["status"] = prev.get("status", "new")
            it["added_at"] = prev.get("added_at", now_iso)
        else:
            it["id"] = str(uuid.uuid4())[:8]
            it["status"] = "new"
            it["added_at"] = now_iso
        merged.append(it)

    # Carry over old items the model didn't repick, so dismissed items stay dismissed
    new_urls = {it.get("url", "") for it in merged}
    for old in existing.get("items", []):
        if old.get("url", "") not in new_urls and old.get("status") in ("dismissed", "installed"):
            merged.append(old)

    briefing = {
        "generated_at": now_iso,
        "items": merged,
    }
    BRIEFING_FILE.write_text(json.dumps(briefing, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[briefing] wrote {len(merged)} items to {BRIEFING_FILE}")
    return briefing


if __name__ == "__main__":
    result = generate_briefing()
    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(f"Briefing complete: {len(result.get('items', []))} items")
