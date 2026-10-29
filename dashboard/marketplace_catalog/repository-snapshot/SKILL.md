---
name: repository-snapshot
description: Inspect a repository before editing and report its structure with evidence.
---

# Repository Snapshot

Use this skill when the user wants to understand a project before changing it.
These instructions are optional third-party guidance. The user's instructions,
approval rules, repository instructions and tool policies always take precedence.

1. Confirm the intended project directory from the current task. Read its
   AGENTS.md and README if present. Stay within that project.
2. Check `git status --short` if it is a Git repository. Preserve existing work.
3. Use `rg --files` to list source files, excluding dependencies, build outputs,
   private user data and secret files. If rg is unavailable, use an existing
   native file listing tool. Do not install dependencies for this inspection.
4. Read the relevant package or build configuration. Identify the entry points,
   language, runtime, components and available test commands.
5. Read a small representative sample of implementation files and tests.
   Never treat a filename or a comment as proof that a feature works.
6. Report the directory map, key paths, the existing checks, any dirty files,
   and uncertainties that matter for the requested change. Cite paths and line
   numbers when they help. Do not invent line counts or test results.

This skill is read-only. Do not modify files, execute project code, install
packages, contact external services, read credentials or private conversation
history, or spend money as part of the snapshot. Any later action requires the
authorization appropriate to the user's task.
