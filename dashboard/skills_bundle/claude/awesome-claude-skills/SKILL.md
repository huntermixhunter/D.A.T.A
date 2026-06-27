---
name: awesome-claude-skills
description: Search and install skills from ComposioHQ's awesome-claude-skills catalog. Clones the catalog on first use, then searches and installs individual skills on request.
---

# Awesome Claude Skills Catalog

Use this skill when the user asks to find, inspect, or install a skill from ComposioHQ's `awesome-claude-skills` repository.

## Source & local clone

The catalog is a public GitHub repo. It is **not bundled** with DATA — clone it on first use.

- Repository: `https://github.com/ComposioHQ/awesome-claude-skills`
- Local clone (created on first use): `%USERPROFILE%\src\awesome-claude-skills`
- Installed Claude Code skills directory: `%USERPROFILE%\.claude\skills`
- Hermes skills directory: `%LOCALAPPDATA%\hermes\skills`

If the local clone does not exist yet, create it first:

```powershell
$catalog = "$env:USERPROFILE\src\awesome-claude-skills"
if (-not (Test-Path -LiteralPath $catalog)) {
  New-Item -ItemType Directory -Force -Path (Split-Path $catalog) | Out-Null
  git clone https://github.com/ComposioHQ/awesome-claude-skills $catalog
} else {
  git -C $catalog pull --ff-only
}
```

## Search Workflow

1. Search the local catalog before browsing:

```powershell
rg -n "query terms" "$env:USERPROFILE\src\awesome-claude-skills\README.md" "$env:USERPROFILE\src\awesome-claude-skills\**\SKILL.md"
```

2. Prefer direct top-level skills and `document-skills/*` when they fit.
3. Treat `composio-skills/*-automation` as app-specific automation skills. These often need Composio credentials, API keys, or a connected Composio account.
4. Do not bulk-install all `composio-skills`; install only the specific app automation skill requested.
5. Validate any plugin with `claude plugin validate <path>` before using it.

## Install Workflow

To install a specific skill folder into Claude Code:

```powershell
$source = "$env:USERPROFILE\src\awesome-claude-skills\path\to\skill"
$target = "$env:USERPROFILE\.claude\skills\skill-name"
if (-not (Test-Path -LiteralPath $target)) {
  Copy-Item -LiteralPath $source -Destination $target -Recurse
}
```

Do not overwrite an existing skill without explicit user approval.

## Connect Apps Plugin

The repository includes a local Claude Code plugin at:

```text
%USERPROFILE%\src\awesome-claude-skills\connect-apps-plugin
```

It is valid with a warning about a missing version field. Use it with:

```powershell
claude --plugin-dir "$env:USERPROFILE\src\awesome-claude-skills\connect-apps-plugin"
```

Full app actions require Composio setup and credentials.
