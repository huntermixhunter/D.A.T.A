"""Subscription model capabilities, verified 2026-10-03. No network calls.

Codex's local catalog reflects account rollout. Only promote Sol to 6.1 when
that catalog advertises it. Claude aliases stay with the installed CLI.
"""
import json
import os
from pathlib import Path


EFFORT_LEVELS = [
    {"id": "", "label": "Effort: Auto (CLI default)", "short": "AUTO"},
    {"id": "low", "label": "Effort: Low", "short": "LOW"},
    {"id": "medium", "label": "Effort: Medium", "short": "MEDIUM"},
    {"id": "high", "label": "Effort: High", "short": "HIGH"},
    {"id": "xhigh", "label": "Effort: Extra high", "short": "XHIGH"},
    {"id": "max", "label": "Effort: Max", "short": "MAX"},
    {"id": "ultra", "label": "Effort: Ultra (parallel agents)", "short": "ULTRA"},
]
EFFORT_IDS = {level["id"] for level in EFFORT_LEVELS}


def read_codex_catalog():
    """Read model metadata only, never credentials or chat history."""
    root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((root / "models_cache.json").read_text(encoding="utf-8"))
        return {m["slug"]: m for m in data.get("models", [])
                if isinstance(m, dict) and isinstance(m.get("slug"), str)
                and m.get("visibility") == "list"}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def codex_sol_model(catalog):
    return "gpt-6.1-sol" if "gpt-6.1-sol" in catalog else "gpt-6-sol"


def effort_levels(provider_id, model, catalog):
    """Return only supported levels, ordered from Auto through deepest."""
    allowed = {""}
    if provider_id.startswith("codex"):
        metadata = catalog.get(model, {})
        advertised = metadata.get("supported_reasoning_levels")
        if isinstance(advertised, list) and advertised:
            allowed.update(item.get("effort") for item in advertised if isinstance(item, dict))
        elif model in {"gpt-6-astra", "gpt-6-sol", "gpt-6.1-sol"}:
            allowed.update({"low", "medium", "high", "xhigh", "max", "ultra"})
        elif model == "gpt-6-luna":
            allowed.update({"low", "medium", "high", "xhigh", "max"})
        else:
            allowed.update({"low", "medium", "high", "xhigh"})
    elif provider_id.startswith("claude-cli") and model in {
        "opus", "sonnet", "fable", "claude-opus-5-5", "claude-opus-5",
        "claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5-5",
        "claude-sonnet-5", "claude-fable-5-1", "claude-fable-5",
    }:
        allowed.update({"low", "medium", "high", "xhigh", "max"})
    elif provider_id.startswith("claude-cli") and model in {"claude-opus-4-6", "claude-sonnet-4-6"}:
        allowed.update({"low", "medium", "high", "max"})
    return [dict(level) for level in EFFORT_LEVELS if level["id"] in allowed]


def effective_effort(requested, levels):
    """Keep a saved preference usable after switching to a smaller model."""
    if requested not in EFFORT_IDS:
        return ""
    allowed = {level["id"] for level in levels}
    ceiling = next(i for i, level in enumerate(EFFORT_LEVELS) if level["id"] == requested)
    return next(level["id"] for level in reversed(EFFORT_LEVELS[:ceiling + 1])
                if level["id"] in allowed)
