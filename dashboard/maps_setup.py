#!/usr/bin/env python3
"""
maps_setup.py - installs the offline map stack and downloads map regions.

Run by the dashboard's background-job runner so a long region download
survives the request that started it. Also usable by hand:

    python maps_setup.py install --root "D:\\LCARS Offline\\maps"
    python maps_setup.py region  --root "..." --slug idaho-panhandle \\
                                 --bbox=-117.6,46.4,-115.4,49.2 --maxzoom 14

Standard library only. Prints plain progress lines, because the job runner
pipes stdout straight into the dashboard task log.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PMTILES_CLI_VERSION = "1.31.2"
MAPLIBRE_VERSION = "6.16.0"
PMTILES_JS_VERSION = "4.5.0"
BASEMAPS_VERSION = "5.7.2"
PLANET_BUILD = "20261027"
PLANET_URL = f"https://build.protomaps.com/{PLANET_BUILD}.pmtiles"

ASSETS_ZIP = "https://codeload.github.com/protomaps/basemaps-assets/zip/028c18f713baecad011301ff7a69acc39bcc2ae7"
FONT_STACKS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic", "Noto Sans Devanagari Regular v1"]

UA = {"User-Agent": "data-offline-maps/1.0"}


def say(msg: str) -> None:
    print(msg, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


# ──────────────────────────────────────────────────────────────────────────
# Download helper: resumable, progress-reporting, atomic.
# ──────────────────────────────────────────────────────────────────────────
def download(url: str, dest: Path, label: str = "", tries: int = 3,
             sha256: str = "", expected_size: int = 0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    label = label or dest.name

    for attempt in range(1, tries + 1):
        have = part.stat().st_size if part.exists() else 0
        # A previous run may have finished writing and stopped before rename.
        if sha256 and expected_size and have == expected_size:
            with part.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest == sha256:
                os.replace(part, dest)
                say(f'  ok  {label} resumed at verified completion')
                return dest
            part.unlink(); have = 0
        headers = dict(UA)
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                if have and resp.status == 206 and not resp.headers.get('Content-Range', '').startswith(f'bytes {have}-'):
                    raise OSError('Download server returned the wrong byte range')
                if have and resp.status != 206:
                    # Server ignored the range. Start over rather than corrupt.
                    have = 0
                    part.unlink(missing_ok=True)
                total = int(resp.headers.get("Content-Length", 0)) + have
                mode = "ab" if have else "wb"
                last_report = time.time()
                with open(part, mode) as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        if time.time() - last_report > 5:
                            pct = f" ({have * 100 // total}%)" if total else ""
                            say(f"    {label}: {human(have)}{pct}")
                            last_report = time.time()
            if total and have != total:
                raise OSError(f'Incomplete download: {have} of {total} bytes')
            if expected_size and have != expected_size:
                raise OSError(f'Unexpected file size: {have}, expected {expected_size}')
            if sha256:
                with part.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != sha256:
                    part.unlink()
                    raise OSError('SHA-256 mismatch; rejected downloaded file')
            os.replace(part, dest)
            say(f"  ok  {label}  {human(dest.stat().st_size)}")
            return dest
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code == 416:
                part.unlink(missing_ok=True)
            if attempt == tries:
                raise
            wait = 3 * attempt
            say(f"  !   {label}: {e}. retry {attempt}/{tries - 1} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"download failed: {url}")


# ──────────────────────────────────────────────────────────────────────────
# install
# ──────────────────────────────────────────────────────────────────────────
def cli_asset_name() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    if system == "Windows":
        return f"go-pmtiles_{PMTILES_CLI_VERSION}_Windows_{arch}.zip"
    if system == "Darwin":
        return f"go-pmtiles_{PMTILES_CLI_VERSION}_Darwin_{arch}.zip"
    return f"go-pmtiles_{PMTILES_CLI_VERSION}_Linux_{arch}.tar.gz"


def install_cli(root: Path) -> None:
    bin_dir = root / "bin"
    exe_name = "pmtiles.exe" if platform.system() == "Windows" else "pmtiles"
    exe = bin_dir / exe_name
    if exe.is_file():
        say(f"  ok  pmtiles CLI already present ({human(exe.stat().st_size)})")
        return

    asset = cli_asset_name()
    url = (f"https://github.com/protomaps/go-pmtiles/releases/download/"
           f"v{PMTILES_CLI_VERSION}/{asset}")
    say(f"  .. pmtiles CLI v{PMTILES_CLI_VERSION} ({asset})")

    # Untrusted archive: unpack in its own scratch dir, copy out only the one
    # binary we asked for, never execute anything else from it.
    with tempfile.TemporaryDirectory(prefix="pmtiles-dl-") as td:
        tmp = Path(td)
        # GitHub publishes the digest for each immutable release asset.
        release_url = f'https://api.github.com/repos/protomaps/go-pmtiles/releases/tags/v{PMTILES_CLI_VERSION}'
        with urllib.request.urlopen(urllib.request.Request(release_url, headers=UA), timeout=30) as response:
            release = json.load(response)
        entry = next(item for item in release['assets'] if item['name'] == asset)
        digest = entry.get('digest', '')
        if not digest.startswith('sha256:'):
            raise RuntimeError('Release has no SHA-256 digest for ' + asset)
        pkg = download(url, tmp / asset, "pmtiles CLI", sha256=digest[7:], expected_size=entry['size'])
        out = tmp / "unpack"
        out.mkdir()
        if asset.endswith(".zip"):
            with zipfile.ZipFile(pkg) as zf:
                for member in zf.namelist():
                    if ".." in member or Path(member).is_absolute():
                        continue
                    zf.extract(member, out)
        else:
            import tarfile
            with tarfile.open(pkg) as tf:
                for member in tf.getmembers():
                    if ".." in member.name or member.name.startswith("/"):
                        continue
                    if member.isfile():
                        tf.extract(member, out)

        found = next((p for p in out.rglob("*")
                      if p.is_file() and p.name in ("pmtiles", "pmtiles.exe")), None)
        if not found:
            raise RuntimeError("pmtiles binary not found inside the release archive")
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, exe)

    if platform.system() != "Windows":
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    say(f"  ok  pmtiles CLI -> {exe}")


def install_browser_libs(root: Path) -> None:
    assets = root / "assets"
    targets = [
        (f"https://unpkg.com/maplibre-gl@{MAPLIBRE_VERSION}/dist/maplibre-gl.mjs",
         assets / "maplibre-gl.mjs", "maplibre-gl.mjs"),
        (f"https://unpkg.com/maplibre-gl@{MAPLIBRE_VERSION}/dist/maplibre-gl.css",
         assets / "maplibre-gl.css", "maplibre-gl.css"),
        (f"https://unpkg.com/pmtiles@{PMTILES_JS_VERSION}/dist/pmtiles.js",
         assets / "pmtiles.js", "pmtiles.js"),
        (f"https://unpkg.com/@protomaps/basemaps@{BASEMAPS_VERSION}/dist/basemaps.js",
         assets / "basemaps.js", "basemaps.js"),
    ]
    for extra in ('maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs'):
        targets.append((f'https://unpkg.com/maplibre-gl@{MAPLIBRE_VERSION}/dist/{extra}', assets / extra, extra))
    for url, dest, label in targets:
        if dest.is_file() and dest.stat().st_size > 1000:
            say(f"  ok  {label} already present")
            continue
        download(url, dest, label)


def install_fonts_and_sprites(root: Path) -> None:
    assets = root / "assets"
    fonts_dir = assets / "fonts"
    sprites_dir = assets / "sprites"

    have_fonts = all((fonts_dir / stack / '0-255.pbf').is_file() for stack in FONT_STACKS)
    have_sprites = all((sprites_dir / 'v4' / (flavor + scale + ext)).is_file()
                       for flavor in ('light', 'dark') for scale in ('', '@2x') for ext in ('.png', '.json'))
    if have_fonts and have_sprites:
        say("  ok  fonts and sprites already present")
        return

    say("  .. map fonts and sprites (one archive, about 15 MB)")
    with tempfile.TemporaryDirectory(prefix="basemaps-assets-") as td:
        tmp = Path(td)
        pkg = download(ASSETS_ZIP, tmp / "basemaps-assets.zip", "basemaps-assets")
        out = tmp / "unpack"
        out.mkdir()
        with zipfile.ZipFile(pkg) as zf:
            for member in zf.namelist():
                if ".." in member or Path(member).is_absolute():
                    continue
                # Only the two data subtrees we actually render with.
                if "/fonts/" not in member and "/sprites/v4/" not in member:
                    continue
                if not member.lower().endswith((".pbf", ".json", ".png")):
                    continue
                zf.extract(member, out)

        src_root = next((p for p in out.iterdir() if p.is_dir()), None)
        if not src_root:
            raise RuntimeError("assets archive was empty")

        n_fonts = 0
        for stack in FONT_STACKS:
            src = src_root / "fonts" / stack
            if not src.is_dir():
                raise RuntimeError(f'Font stack missing in archive: {stack}')
            dst = fonts_dir / stack
            dst.mkdir(parents=True, exist_ok=True)
            for pbf in src.glob("*.pbf"):
                shutil.copy2(pbf, dst / pbf.name)
                n_fonts += 1
        say(f"  ok  fonts: {n_fonts} glyph ranges across {len(FONT_STACKS)} stacks")

        src_sprites = src_root / "sprites" / "v4"
        if src_sprites.is_dir():
            dst = sprites_dir / "v4"
            dst.mkdir(parents=True, exist_ok=True)
            n = 0
            for f in src_sprites.iterdir():
                if f.is_file():
                    shutil.copy2(f, dst / f.name)
                    n += 1
            say(f"  ok  sprites: {n} files")
        else:
            raise RuntimeError("sprites/v4 missing in archive")


def cmd_install(args) -> int:
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    say(f"Installing offline map stack into {root}")
    say("")
    say("[1/3] Tile extraction tool")
    install_cli(root)
    say("")
    say("[2/3] Browser renderer, tile reader and basemap style")
    install_browser_libs(root)
    say("")
    say("[3/3] Map fonts and icons")
    install_fonts_and_sprites(root)
    say("")

    (root / "regions").mkdir(parents=True, exist_ok=True)
    manifest = root / "regions.json"
    if not manifest.exists():
        manifest.write_text("{}", encoding="utf-8")

    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    say(f"Map stack ready. {human(total)} on disk at {root}")
    say("No regions downloaded yet. Pick one from the Maps page to add map data.")
    return 0


# ──────────────────────────────────────────────────────────────────────────
# region
# ──────────────────────────────────────────────────────────────────────────
def cmd_region(args) -> int:
    root = Path(args.root)
    exe_name = "pmtiles.exe" if platform.system() == "Windows" else "pmtiles"
    exe = root / "bin" / exe_name
    if not exe.is_file():
        say(f"ERROR: map stack not installed ({exe} missing). Install it first.")
        return 2

    slug = args.slug.strip().lower()
    import maps_offline
    maps_offline.extract_command(slug, [float(v) for v in args.bbox.split(',')], args.maxzoom, args.name)
    regions = root / "regions"
    regions.mkdir(parents=True, exist_ok=True)
    dest = regions / f"{slug}.pmtiles"
    work = regions / f"{slug}.pmtiles.partial"

    bbox = args.bbox.strip()
    maxzoom = int(args.maxzoom)
    name = args.name or slug
    if dest.is_file():
        say(f'Already installed: {dest}')
        return 0
    budget_path = root / 'budget.json'
    reserve = 5 * 1024**3
    if budget_path.is_file():
        reserve = max(reserve, int(json.loads(budget_path.read_text())['reserve_bytes']))

    free = shutil.disk_usage(str(regions)).free
    if free <= reserve + 1024**3:
        raise RuntimeError('Not enough free space above the configured reserve')
    say(f"Region      : {name}  [{slug}]")
    say(f"Bounding box: {bbox}")
    say(f"Max zoom    : {maxzoom}")
    say(f"Source      : planet build {PLANET_BUILD}")
    say(f"Destination : {dest}")
    say(f"Free space  : {human(free)}")
    say("")
    say("Carving the region out of the planet archive. Only the tiles inside")
    say("the box are downloaded, so this transfers far less than the 130 GB")
    say("planet file. Large regions can take a while.")
    say("")

    if work.exists():
        work.unlink()

    cmd = [str(exe), "extract", PLANET_URL, str(work),
           f"--bbox={bbox}", f"--maxzoom={maxzoom}"]
    started = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
            creationflags=0x08000000 if os.name == 'nt' else 0,
        )
    except OSError as e:
        say(f"ERROR: could not run pmtiles: {e}")
        return 3

    stopped_for_space = threading.Event()
    finished = threading.Event()
    def protect_reserve():
        while not finished.wait(1):
            if shutil.disk_usage(str(regions)).free < reserve + 256 * 1024**2:
                stopped_for_space.set()
                proc.terminate()
                break
    threading.Thread(target=protect_reserve, daemon=True).start()

    last = ""
    last_report = 0.0
    for line in proc.stdout:
        line = line.rstrip()
        if line and line != last and (time.monotonic() - last_report > 2 or 'extract.go:' in line):
            say(f"  {line}")
            last = line
            last_report = time.monotonic()
    rc = proc.wait()
    finished.set()
    if stopped_for_space.is_set():
        work.unlink(missing_ok=True)
        raise RuntimeError('Download stopped to preserve the free-space reserve')

    if rc != 0 or not work.exists():
        say(f"ERROR: extraction failed (exit {rc})")
        work.unlink(missing_ok=True)
        return rc or 4

    size = work.stat().st_size
    if size < 1024:
        say("ERROR: produced file is empty. Check the bounding box.")
        work.unlink(missing_ok=True)
        return 5

    with work.open('rb') as stream:
        if stream.read(8) != b'PMTiles\x03':
            raise RuntimeError('Download did not produce a valid PMTiles v3 header')
    # Structural verification must pass before this region becomes visible.
    subprocess.run([str(exe), 'verify', str(work)], check=True)
    os.replace(work, dest)
    elapsed = time.time() - started

    # Record it. The file on disk is the source of truth; this adds the
    # metadata the map page needs to fly to the right place.
    manifest_path = root / "regions.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, ValueError):
        manifest = {}
    manifest[slug] = {
        "name": name,
        "bbox": [float(v) for v in bbox.split(",")],
        "maxzoom": maxzoom,
        "bytes": size,
        "build": PLANET_BUILD,
        "added": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, manifest_path)

    say("")
    say(f"Done. {name}: {human(size)} in {int(elapsed // 60)}m {int(elapsed % 60)}s")
    say(f"Free space now: {human(shutil.disk_usage(str(regions)).free)}")
    return 0


def main() -> int:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description="Offline map stack installer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_i = sub.add_parser("install", help="download the map stack")
    p_i.add_argument("--root", required=True)
    p_i.set_defaults(func=cmd_install)

    p_r = sub.add_parser("region", help="download one map region")
    p_r.add_argument("--root", required=True)
    p_r.add_argument("--slug", required=True)
    p_r.add_argument("--bbox", required=True,
                     help="min_lon,min_lat,max_lon,max_lat")
    p_r.add_argument("--maxzoom", required=True)
    p_r.add_argument("--name", default="")
    p_r.set_defaults(func=cmd_region)

    args = ap.parse_args()
    try:
        # One writer per store across multiple dashboards. OS locks are
        # released automatically if the process crashes or the drive is removed.
        root = Path(args.root)
        root.mkdir(parents=True, exist_ok=True)
        with (root / '.install.lock').open('a+b') as lock:
            lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError('Another map download is running. Retry after it finishes.')
            return args.func(args)
    except KeyboardInterrupt:
        say("Cancelled.")
        return 130
    except Exception as e:  # noqa: BLE001 - job runner needs the message, not a traceback
        say(f"ERROR: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
