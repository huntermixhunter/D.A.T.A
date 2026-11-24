---
name: agent-teams
description: Use when coordinating multiple Claude Code instances as a team for parallel work - research, parallel feature development, debugging with competing hypotheses, or cross-layer coordination
---

# Orchestrate Teams of Claude Code Sessions

Coordinate multiple Claude Code instances working together as a team, with shared tasks, inter-agent messaging, and centralized management.

> **Warning:** Agent teams are experimental and disabled by default. Enable them by adding `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` to your settings.json or environment.

## When to Use Agent Teams

Agent teams are most effective for tasks where **parallel exploration adds real value**:

- **Research and review**: multiple teammates investigate different aspects simultaneously, share and challenge findings
- **New modules or features**: teammates each own a separate piece without stepping on each other
- **Debugging with competing hypotheses**: teammates test different theories in parallel and converge faster
- **Cross-layer coordination**: changes spanning frontend, backend, and tests, each owned by a different teammate

**Do NOT use agent teams for:** sequential tasks, same-file edits, or work with many dependencies. Use a single session or subagents instead.

### Agent Teams vs. Subagents

|                   | Subagents                                        | Agent Teams                                         |
| :---------------- | :----------------------------------------------- | :-------------------------------------------------- |
| **Context**       | Own context window; results return to caller     | Own context window; fully independent               |
| **Communication** | Report results back to main agent only           | Teammates message each other directly               |
| **Coordination**  | Main agent manages all work                      | Shared task list with self-coordination             |
| **Best for**      | Focused tasks where only the result matters      | Complex work requiring discussion and collaboration |
| **Token cost**    | Lower: results summarized back to main context   | Higher: each teammate is a separate Claude instance |

## Enable Agent Teams

Add to `settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Requires **Claude Code v2.1.32 or later**. Check: `claude --version`

## Starting a Team

Tell Claude to create an agent team and describe the task and team structure in natural language:

```
I'm designing a CLI tool that helps developers track TODO comments across
their codebase. Create an agent team to explore this from different angles: one
teammate on UX, one on technical architecture, one playing devil's advocate.
```

Claude creates a team with a shared task list, spawns teammates, coordinates work, and cleans up when finished.

## Controlling Your Team

### Display Modes

- **In-process** (default): all teammates run inside your main terminal. Use `Shift+Down` to cycle through teammates.
- **Split panes**: each teammate gets its own pane (requires tmux or iTerm2).

Set in `~/.claude.json`:
```json
{ "teammateMode": "in-process" }
```
Or per-session: `claude --teammate-mode in-process`

### Specify Teammates and Models

```
Create a team with 4 teammates to refactor these modules in parallel.
Use Sonnet for each teammate.
```

### Require Plan Approval

```
Spawn an architect teammate to refactor the authentication module.
Require plan approval before they make any changes.
```

### Talk to Teammates Directly

- **In-process**: `Shift+Down` to cycle, type to message. `Ctrl+T` to toggle task list.
- **Split-pane**: click into a teammate's pane.

### Shut Down and Clean Up

```
Ask the researcher teammate to shut down
Clean up the team
```

> **Warning:** Always use the lead to clean up. Never run cleanup from a teammate.

## Architecture

| Component     | Role                                                                              |
| :------------ | :-------------------------------------------------------------------------------- |
| **Team lead** | Main Claude Code session — creates team, spawns teammates, coordinates work       |
| **Teammates** | Separate Claude Code instances working on assigned tasks                          |
| **Task list** | Shared list of work items that teammates claim and complete                       |
| **Mailbox**   | Messaging system for communication between agents                                 |

Stored locally:
- Team config: `~/.claude/teams/{team-name}/config.json`
- Task list: `~/.claude/tasks/{team-name}/`

## Best Practices

### Give Teammates Enough Context
Teammates do NOT inherit the lead's conversation history. Include task-specific details in the spawn prompt:

```
Spawn a security reviewer teammate with the prompt: "Review the authentication module
at src/auth/ for security vulnerabilities. Focus on token handling, session
management, and input validation..."
```

### Choose Appropriate Team Size
- Start with **3–5 teammates** for most workflows
- Aim for **5–6 tasks per teammate**
- Scale up only when work genuinely benefits from true parallelism

### Size Tasks Appropriately
- **Too small**: coordination overhead exceeds benefit
- **Too large**: too long without check-ins, risk of wasted effort
- **Just right**: self-contained units with a clear deliverable (a function, a test file, a review)

### Avoid File Conflicts
Two teammates editing the same file leads to overwrites. Each teammate should own a **different set of files**.

### Monitor and Steer
If the lead starts implementing tasks itself instead of waiting for teammates:
```
Wait for your teammates to complete their tasks before proceeding
```

## Use Case Examples

### Parallel Code Review
```
Create an agent team to review PR #142. Spawn three reviewers:
- One focused on security implications
- One checking performance impact
- One validating test coverage
Have them each review and report findings.
```

### Competing Hypotheses Debugging
```
Users report the app exits after one message instead of staying connected.
Spawn 5 agent teammates to investigate different hypotheses. Have them talk to
each other to try to disprove each other's theories, like a scientific
debate. Update the findings doc with whatever consensus emerges.
```

## Quality Gates via Hooks

- `TeammateIdle`: runs when a teammate is about to go idle. Exit code 2 sends feedback and keeps them working.
- `TaskCreated`: runs when a task is being created. Exit code 2 prevents creation.
- `TaskCompleted`: runs when a task is being marked complete. Exit code 2 prevents completion.

## Known Limitations

- **No session resumption** with in-process teammates (`/resume` and `/rewind` don't restore them)
- **Task status can lag**: manually nudge stuck tasks if needed
- **Shutdown can be slow**: teammates finish current request before shutting down
- **One team per session**: clean up before starting a new team
- **No nested teams**: only the lead can manage the team
- **Lead is fixed**: can't promote a teammate to lead
- **Split panes** not supported in VS Code integrated terminal, Windows Terminal, or Ghostty

## Troubleshooting

| Problem | Fix |
| :------ | :-- |
| Teammates not appearing | Press `Shift+Down`; verify tmux is installed (`which tmux`) |
| Too many permission prompts | Pre-approve operations in permission settings before spawning |
| Teammates stopping on errors | Give direct instructions or spawn replacement teammates |
| Lead shuts down early | Tell it to wait for teammates; it may need prompting |
| Orphaned tmux sessions | `tmux ls` then `tmux kill-session -t <session-name>` |
