---
name: awesome-claude-skills
description: Search and install skills from the local ComposioHQ awesome-claude-skills catalog cloned at C:\Users\mixma\src\awesome-claude-skills.
---

# Awesome Claude Skills Catalog

Use this skill when the user asks to find, inspect, or install a skill from ComposioHQ's `awesome-claude-skills` repository.

## Local Source

- Repository: `https://github.com/ComposioHQ/awesome-claude-skills`
- Local clone: `C:\Users\mixma\src\awesome-claude-skills`
- Installed Claude Code skills directory: `C:\Users\mixma\.claude\skills`
- Hermes skills directory: `C:\Users\mixma\AppData\Local\hermes\skills`

## Search Workflow

1. Search the local catalog before browsing:

```powershell
rg -n "query terms" "C:\Users\mixma\src\awesome-claude-skills\README.md" "C:\Users\mixma\src\awesome-claude-skills\**\SKILL.md"
```

2. Prefer direct top-level skills and `document-skills/*` when they fit.
3. Treat `composio-skills/*-automation` as app-specific automation skills. These often need Composio credentials, API keys, or a connected Composio account.
4. Do not bulk-install all `composio-skills`; install only the specific app automation skill requested.
5. Validate any plugin with `claude plugin validate <path>` before using it.

## Install Workflow

To install a specific skill folder into Claude Code:

```powershell
$source = "C:\Users\mixma\src\awesome-claude-skills\path\to\skill"
$target = "C:\Users\mixma\.claude\skills\skill-name"
if (-not (Test-Path -LiteralPath $target)) {
  Copy-Item -LiteralPath $source -Destination $target -Recurse
}
```

Do not overwrite an existing skill without explicit user approval.

## Connect Apps Plugin

The repository includes a local Claude Code plugin at:

```text
C:\Users\mixma\src\awesome-claude-skills\connect-apps-plugin
```

It is valid with a warning about a missing version field. Use it with:

```powershell
claude --plugin-dir "C:\Users\mixma\src\awesome-claude-skills\connect-apps-plugin"
```

Full app actions require Composio setup and credentials.
