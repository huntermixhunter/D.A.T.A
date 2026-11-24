# DATA-core skill bundle

A curated set of skills that **ships inside the repo** so a fresh DATA install
has its core toolset on first launch — instead of "whatever skills happen to be
on the machine already."

## Why this exists

The bridge discovers skills at the start of every request from two dirs that live
*outside* the repo:

- `~/.claude/skills/<name>/SKILL.md` (Claude Code skills)
- `<hermes>/skills/<category>/<name>/skill.md` (Hermes skills) — `<hermes>` is
  `%LOCALAPPDATA%\hermes` on Windows, `~/.local/share/hermes` elsewhere (or
  `$HERMES_DIR`).

A fresh clone/zip therefore had **zero** skills until the user hand-installed
them. This bundle fixes that.

## Layout

```
skills_bundle/
  manifest.json          <- the curated list (SOURCE OF TRUTH — edit this)
  claude/<name>/         <- bundled Claude Code skills
  hermes/<cat>/<name>/   <- bundled Hermes skills
  .installed             <- runtime marker (gitignored)
  install.log            <- last install output (gitignored)
```

## The two scripts (in `dashboard/`)

| Script | Side | Direction | When to run |
|--------|------|-----------|-------------|
| `bundle_skills.py`  | maintainer | live dirs → bundle | after editing `manifest.json`, to refresh the shipped files |
| `install_skills.py` | install    | bundle → live dirs | once on a fresh machine (auto-run by the installers) |

```bash
# maintainer: refresh the bundle from your live skill dirs
python bundle_skills.py            # or --clean / --dry-run

# fresh install: copy the bundle into the discovery dirs
python install_skills.py           # or --force / --dry-run
```

`install_skills.py` is **idempotent** and never clobbers a skill that is already
present (use `--force` to overwrite).

## Wired into both install paths

- **Windows** — `launch_data.bat` runs `install_skills.py` once (sentinel-guarded).
- **Droplet** — `deploy/setup_vps.sh` step 7b runs it after the clone.

## Adding / removing a skill

1. Edit the `claude` / `hermes` lists in `manifest.json`.
2. Run `python bundle_skills.py` to pull the new skill's files into the bundle
   (or drop the removed one).
3. Commit `manifest.json` + the changed `claude/` / `hermes/` folders.

## Platform-only skills

A manifest entry may carry `"platforms": ["darwin"]` (values are `sys.platform`
names). The files ship in every download, but `install_skills.py` installs the
entry only on those systems. The Mac skills (`apple/apple-notes`,
`apple/apple-reminders`, `apple/findmy`, `apple/imessage`,
`apple/mac-desktop-control`) use this.

## Bundle-maintained skills

Entries with `"source": "bundle"` are edited directly in this folder: either
written for DATA (`mac-desktop-control`) or adapted from Hermes to DATA's tools
(the Apple skills). `bundle_skills.py` never overwrites them from your live
folders, and `--clean` keeps them.

## Updates reach existing installs

The self-updater syncs everything under `dashboard/`, including this bundle.
When bundle files change, it runs `install_skills.py --sync`, and the bridge
runs the same sync once at startup on retail installs. Sync:

- installs skills that are new to the bundle;
- refreshes a skill only if the live copy is still exactly what DATA installed;
- never overwrites a skill the user edited, and never reinstalls one they deleted.

What DATA installed is fingerprinted in `.install_state.json` (gitignored).

## The optional Skill Library (not in this bundle)

About 70 more skills live in the repo at `skills_library/`, which
`.gitattributes` marks `export-ignore`, so no zip, installer or DMG contains
them and the updater never pulls them. Users add or remove them one at a time
in Settings > Upgrades > Skill Library (`dashboard/skill_library.py`), which
downloads only that skill from GitHub and checks every file against GitHub's
blob SHA. Installed copies carry a `.data-library.json` marker, so DATA only
ever removes skills it installed.

Maintainer: edit the `LIBRARY` list in `tools/build_skill_library.py`, run it
from the repo root, and commit `skills_library/` plus
`dashboard/skill_library_catalog.json`. A skill must be pushed to GitHub before
retail users can install it.
