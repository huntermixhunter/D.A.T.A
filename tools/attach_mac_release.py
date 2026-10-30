#!/usr/bin/env python3
"""Attach the built Mac disk images to the one GitHub Release carrying a tag.

Why this exists as a script instead of inline workflow shell:

`gh release view <tag>` resolves through the GitHub *tags* API, and that API does
not return draft releases. On v1.0.58 the Mac job therefore saw "no release" while
the Windows draft existed, took its create branch, produced a second untagged
draft, and uploaded every disk image to that orphan. The release the public could
see shipped Windows only. Listing `repos/{repo}/releases` does include drafts, so
that is what this script uses.

Guarantees:
  * Exactly one release may carry the tag. Two means a split, so it refuses.
  * Attaching is additive only. An asset name already attached is never replaced,
    whether the release is a draft or published. Corrections ship as a new version.
  * Checksums are verified before anything is uploaded.

Usage:
    python3 tools/attach_mac_release.py <tag> <directory> [--dry-run]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys


NOTES = (
    "Mac downloads for Apple Silicon and Intel.\n"
    "Includes Python and system vitals. Unsigned and not notarized.\n"
    "Optional voice and desktop-control dependencies are not bundled.\n"
)


def run(args, **kw):
    """Run a command, echo it, and fail loudly."""
    print("+ " + " ".join(args), flush=True)
    return subprocess.run(args, check=True, text=True, **kw)


def capture(args):
    print("+ " + " ".join(args), flush=True)
    done = subprocess.run(args, check=True, text=True, capture_output=True)
    return done.stdout


def fail(message):
    print("ERROR: " + message, file=sys.stderr, flush=True)
    raise SystemExit(1)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksums(directory, tag):
    """Confirm both images exist and match the digests their builders recorded."""
    expected = {}
    for sidecar in sorted(glob.glob(os.path.join(directory, "*.sha256"))):
        with open(sidecar, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                digest, _, name = line.partition(" ")
                expected[name.lstrip("*").strip()] = digest.strip()

    required = ["DATA-%s-arm64.dmg" % tag, "DATA-%s-x86_64.dmg" % tag]
    for name in required:
        if not os.path.isfile(os.path.join(directory, name)):
            fail("missing build output %s" % name)
        if name not in expected:
            fail("no recorded checksum for %s" % name)
        actual = sha256(os.path.join(directory, name))
        if actual != expected[name]:
            fail("checksum mismatch for %s: recorded %s, got %s"
                 % (name, expected[name], actual))
        print("  %s OK" % name, flush=True)

    manifest = os.path.join(directory, "SHA256SUMS-mac.txt")
    with open(manifest, "w", encoding="utf-8", newline="\n") as handle:
        for name in required:
            handle.write("%s  %s\n" % (expected[name], name))
    return manifest


def payload(directory, tag):
    """Every file this job is responsible for attaching."""
    names = ["DATA-%s-arm64.dmg" % tag, "DATA-%s-x86_64.dmg" % tag]
    names += [os.path.basename(p)
              for p in sorted(glob.glob(os.path.join(directory, "*.build.json")))]
    names.append("SHA256SUMS-mac.txt")
    missing = [n for n in names if not os.path.isfile(os.path.join(directory, n))]
    if missing:
        fail("expected files are absent: %s" % ", ".join(missing))
    return names


def releases_for_tag(repo, tag):
    raw = capture(["gh", "api", "repos/%s/releases?per_page=100" % repo, "--paginate"])
    # --paginate concatenates JSON arrays when a repo has many releases.
    decoder = json.JSONDecoder()
    index, found = 0, []
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        chunk, index = decoder.raw_decode(raw, index)
        found.extend(chunk)
    return [r for r in found if r.get("tag_name") == tag]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("directory")
    parser.add_argument("--repo", default=os.environ.get("GH_REPO", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.repo:
        fail("set --repo or GH_REPO")

    verify_checksums(args.directory, args.tag)
    names = payload(args.directory, args.tag)

    matches = releases_for_tag(args.repo, args.tag)
    if len(matches) > 1:
        for release in matches:
            print("  id=%s draft=%s assets=%s"
                  % (release["id"], release["draft"],
                     [a["name"] for a in release["assets"]]), file=sys.stderr)
        fail("more than one release carries %s; resolve the split by hand" % args.tag)

    if matches:
        attached = {a["name"] for a in matches[0]["assets"]}
        clash = sorted(attached.intersection(names))
        if clash:
            fail("already attached to %s: %s. Use a new version for corrections."
                 % (args.tag, ", ".join(clash)))
        print("Attaching to existing release id=%s draft=%s"
              % (matches[0]["id"], matches[0]["draft"]), flush=True)
    else:
        print("No release carries %s yet; creating a draft." % args.tag, flush=True)

    if args.dry_run:
        print("DRY RUN, would upload: %s" % ", ".join(names))
        return

    if not matches:
        notes = os.path.join(args.directory, "notes.txt")
        with open(notes, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(NOTES)
        run(["gh", "release", "create", args.tag, "--verify-tag", "--draft",
             "--repo", args.repo, "--title", "DATA %s" % args.tag,
             "--notes-file", notes])

    run(["gh", "release", "upload", args.tag, "--repo", args.repo]
        + [os.path.join(args.directory, n) for n in names])
    print("Attached %d files to %s" % (len(names), args.tag), flush=True)


if __name__ == "__main__":
    main()
