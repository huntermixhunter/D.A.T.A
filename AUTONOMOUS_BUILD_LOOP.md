# The autonomous build loop

DATA writes features for itself.

A scheduled job picks one improvement to the dashboard, implements it, proves the
result still boots, and opens a pull request describing what it changed and why.
It never merges its own work. Every change waits for a person to read the diff and
decide.

This document exists because that design has a visible consequence: a queue of open
pull requests on this repository. The queue is the gate doing its job, not a
backlog nobody noticed. What follows is how the loop runs, what stops it, and the
honest state of the queue today.

## The cycle

Each run is the same seven stages. Any stage can end the run, and most endings
produce nothing at all rather than something unreviewed.

1. **Sync.** A dedicated clone, separate from any working copy, is fetched and
   reset hard to the latest `main`, then cleaned. Every run starts from a pristine
   base. A half-finished local edit can never leak into a generated branch.
2. **Context.** The job reads its own ledger of previously built features and the
   full list of prior pull requests, so it does not rebuild something it has
   already built and does not fight a change already in review.
3. **Branch.** Work happens on a dated `auto/` branch cut from `main`. Nothing is
   ever committed to `main` directly.
4. **Build.** A coding agent edits the dashboard source to implement the selected
   feature.
5. **Verify.** The result must compile and the server module must import cleanly.
   This is a boot test, not a formality: a change that cannot start the application
   does not reach a human.
6. **Ship.** On a pass, the branch is committed, renamed to a descriptive slug,
   pushed, and opened as a pull request tagged
   [`auto-feature`](https://github.com/huntermixhunter/D.A.T.A/pulls?q=is%3Apr+label%3Aauto-feature).
   On a failure, or when the agent produced no change, the branch is discarded and
   **no pull request is opened.**
7. **Record.** The run appends to a feature ledger and writes a run report, then
   sends the pull request link for review.

Stage 6 is the part worth stating plainly. Failed runs are common in autonomous
systems: the agent times out, the API refuses a connection, a usage limit is hit
mid-build. Those runs are recorded in the ledger as no-build and produce no branch
and no pull request. The repository history only shows work that compiled.

## The gate

Nothing in this loop has permission to merge. There is no auto-merge rule, no
scheduled merge job, and no branch protection exception. The agent's authority
stops at "opened a pull request."

That is deliberate. An agent that can write code and also approve its own code has
no meaningful review step, and the blast radius of a bad run is the whole product.
Splitting generation from approval keeps a person on the only decision that is hard
to reverse.

Every machine-authored change carries the `auto-feature` label, so agent work stays
separable from hand-written work at every point in the history. You can always ask
this repository which commits a human chose to accept.

## Current state

Verified against the GitHub API on 2026-10-31.

| | |
|---|---|
| Opened by the loop | 38 |
| Reviewed and merged | 7 |
| Open, awaiting review | 31 |
| Closed without merging | 0 |

Of the 31 open pull requests, **19 still apply to `main` cleanly and 12 now
conflict.** The conflicts are not defects in those branches. Each was cut from
`main` on its own date, and as reviewed work merged, later branches touching the
same chat and dashboard files drifted out of date. Clearing a conflicting one means
rebasing it before merge, which is the ordinary cost of generating in parallel and
reviewing in series.

The loop has been **paused since 2026-08-15**, by owner decision, so review can
catch up with generation. It did not break and it was not abandoned. The scheduled
trigger is disabled on both the machine and the server that ran it, and resuming is
a one line change in either place.

The underlying constraint is simple arithmetic. One daily generator against one
part-time human reviewer produces a queue, every time. The gate exists precisely to
make that pressure visible instead of letting it merge itself away.

## Reviewing one

Open any pull request carrying the
[`auto-feature`](https://github.com/huntermixhunter/D.A.T.A/pulls?q=is%3Apr+label%3Aauto-feature)
label. Each body states what changed, which files it touched, and the `main` commit
it branched from. The diffs are small and scoped to a single feature by design,
usually one or two files.

If you want to see what an agent-authored change actually looks like before you
trust one, that label is the fastest honest answer this repository can give you.
