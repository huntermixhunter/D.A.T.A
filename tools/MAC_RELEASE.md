# Mac downloads from Windows

The public repository builds on standard GitHub macOS runners. No Mac purchase,
paid build service, Apple account, or repository secret is needed for an unsigned
build. The workflow refuses to run when the repository is private.

## What ships

- DATA-vX.Y.Z-arm64.dmg for Apple Silicon (M-series Macs).
- DATA-vX.Y.Z-x86_64.dmg for Intel Macs.
- Each contains a writable DATA folder to copy into the user's home folder,
  Start DATA.command, Python 3.12.15, and psutil 7.2.2.
- Both dependency downloads have pinned SHA256 digests. Build provenance records
  the source commit, version, processor, and bundled dependency versions.
- Voice and desktop-control dependencies are optional and are not bundled here.
  An AI provider must be installed and authenticated separately for chat.
- This is a folder-based distribution, not a signed/notarized DATA.app.

## Preview the files on Windows

Commit the intended source first. From a clean checkout, run:

```powershell
.\tools\stage_mac.ps1 1.0.57 -Architecture arm64
.\tools\stage_mac.ps1 1.0.57 -Architecture x86_64
```

Git Bash downloads and verifies the Mac runtime and unpacks the payload under a
unique build/mac-* directory. It does not execute macOS or create a DMG. A failed
stage leaves its files for diagnosis and is never treated as a verified release.
Verified runtime downloads are cached under build/mac-runtime-cache; interrupted
downloads resume on the next attempt and still require a matching SHA256.

## Build on real Macs

The Build Mac downloads workflow runs on ci/mac-* branch pushes, version tags,
and manual workflow dispatch. Branch/manual builds use 0.0.<run number> and only
produce temporary Actions artifacts. Download both artifacts from the run page.
After the workflow has been merged into main, it can be triggered from Windows:

```powershell
gh workflow run build-mac.yml --repo huntermixhunter/D.A.T.A --ref main
gh run list --repo huntermixhunter/D.A.T.A --workflow build-mac.yml
gh run download RUN_ID --repo huntermixhunter/D.A.T.A --dir dist/mac-preview
```

Each architecture is executed on its matching runner. Verification covers offline
installation in a path with spaces, the bundled interpreter, a local HTTP server,
update notification detection/application, preservation of private state, hdiutil
verification, and executing the runtime from the mounted image. Test state lives
in a separate copy and does not enter the package. This is not a manual Finder or
Gatekeeper usability test and does not establish support for every older macOS.

## Prepare a release

1. Review and commit only the source intended for customers. Never include an
   unfinished feature merely to clean the working tree. Use a separate worktree.
2. Create a new vX.Y.Z tag at that exact commit, then push the tag. The workflow
   builds both DMGs, verifies both checksums, and attaches them to a draft release.
   It stops if that release is already public or any asset would be overwritten.
3. Download and inspect both DMGs, their build manifests, and SHA256SUMS-mac.txt.
   Confirm both manifests contain the tagged commit. The workflow does not publish.
4. When publication is authorized, publish the reviewed draft. Do not replace
   existing public assets in place. Use a new version for corrections.
5. In the Magimatix site checkout, run the asset-checking helper:

```powershell
node scripts/update-data-mac.mjs v1.0.57
```

The helper verifies that the release is public and both processor-specific DMGs
and their checksum manifest exist before changing the Mac website constants.
It leaves the Windows/ZIP version alone. Run the site checks, review the diff,
then commit and push only when website activation is authorized.

## Updates after installation

The dashboard already checks for changes and offers Update now / Not now.
Its automatic daily update order is disabled by default. Updates replace only
dashboard files, with backups and content verification. Python, the launcher,
optional dependencies, and this DMG are not replaced by that updater. Install a
new Mac download to update those components, preserving the old DATA folder and
its users directory until migration is verified. Do not overwrite private state.
