#!/usr/bin/env python3
"""
seed_crew.py  —  populate the DATA crew personality files.

Retail DATA ships no crew .md files and the installer creates none, so on a
fresh machine ~/.claude/agents/ is empty and Settings -> Crew Personalities
shows "No officer personality files found" (and the crew cannot be spawned as
subagents). This script writes the 10 DATA officers (Atlas, Forge, Vector,
Sentinel, Probe, Relay, Sage, Echo, Pulse, Scout) into that folder, generated
from CREW_VOICES — the single source of truth already living in bridge_server.py.

The bridge also does this automatically on startup; this script exists so an
already-running machine can be repaired on demand without a restart.

Idempotent: a file that already exists is left untouched (your own edits are
never clobbered) unless you pass --force.

Usage:
    python seed_crew.py            # write any missing crew files
    python seed_crew.py --force    # overwrite even if already present
"""
import sys
from pathlib import Path

# Make stdout safe on any Windows console codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bridge_server  # noqa: E402  (server is __main__-guarded, import is side-effect free)


def main() -> int:
    force = "--force" in sys.argv[1:]
    agents_dir = bridge_server.crew_agents_dir()
    written = bridge_server.seed_crew_agents(force=force)
    if written:
        print(f"Seeded {len(written)} crew file(s) to {agents_dir}:")
        for cid in written:
            print(f"  + {cid}.md")
    else:
        print(f"Nothing to do — all crew files already present in {agents_dir}.")
        print("Pass --force to overwrite them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
