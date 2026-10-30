"""
maps_offline.py - offline vector maps for the dashboard.

Design goals, in priority order:

1. **Works with no internet at all, once installed.** Everything the browser
   needs (renderer, tile reader, style, fonts, sprites, tile data) lives on
   disk. Nothing is fetched from a CDN at render time.
2. **No Python dependencies.** urllib and the standard library only, so it
   runs on the embedded Python in the offline archive.
3. **Small on disk for the area covered.** Vector tiles (PMTiles) instead of
   raster. One self-contained .pmtiles file per region, no tile server.

The stack
---------
* **PMTiles** - a single-file tile archive. The browser reads it with HTTP
  range requests, so a 12 GB regional file costs one `fetch` per visible tile
  rather than a download.
* **MapLibre GL JS** - renders the vector tiles client-side.
* **@protomaps/basemaps** - the cartographic style (what colours a road is at
  what zoom). Vendored as the 38 KB browser build.
* **pmtiles CLI** - carves a region out of the ~130 GB planet build over range
  requests, downloading only the bytes inside the bounding box.

Public surface used by bridge_server.py:

    maps_status()                  -> dict for GET  /maps/status
    maps_catalog()                 -> dict for GET  /maps/catalog
    install_command()              -> (cmd, label) for the install job
    extract_command(slug, ...)     -> (cmd, label) for a region download
    delete_region(slug)            -> dict
    resolve_asset(relpath)         -> Path | None   (sandboxed)
    serve_file(handler, path)      -> streams with Range support
"""

from __future__ import annotations

import json
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────
# Versions. Pinned deliberately: an archive that silently changes what it
# installs is not an archive. Bump these by hand after testing.
# ──────────────────────────────────────────────────────────────────────────
PMTILES_CLI_VERSION = "1.31.2"
MAPLIBRE_VERSION = "6.16.0"
PMTILES_JS_VERSION = "4.5.0"
BASEMAPS_VERSION = "5.7.2"

# The planet build the regions are carved from. Protomaps publishes a daily
# file as YYYYMMDD.pmtiles. We pin one known-good build so a region added in
# six months still matches the style version shipped alongside it.
PLANET_BUILD = "20261027"
PLANET_URL = f"https://build.protomaps.com/{PLANET_BUILD}.pmtiles"

# Font stacks the Protomaps style actually asks for. Anything outside this
# list is never requested, so mirroring more would be dead weight.
FONT_STACKS = ["Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic", "Noto Sans Devanagari Regular v1"]

# Sprite flavours. Both dashboards are dark, but light is 400 KB and makes the
# map readable in daylight on a laptop screen, so both ship.
SPRITE_FLAVORS = ["dark", "light"]

ASSET_BASE = "https://protomaps.github.io/basemaps-assets"


# ──────────────────────────────────────────────────────────────────────────
# Where the maps live
# ──────────────────────────────────────────────────────────────────────────
def _candidate_roots() -> list[Path]:
    """Ordered list of places the map store may live."""
    roots: list[Path] = []
    env = os.environ.get("DATA_MAPS_ROOT") or os.environ.get("LCARS_MAPS_ROOT")
    if env:
        roots.append(Path(env))
    # The faraday-cage archive drive, if it is mounted. Preferred, because the
    # whole point is that maps survive with the rest of the archive.
    for drive in (chr(code) + ":" for code in range(ord("D"), ord("Z") + 1)):
        for name in ("LCARS Offline", "DATA Offline"):
            roots.append(Path(f"{drive}\\{name}") / "maps")
    # Fallback: beside the dashboard, so a laptop with no archive drive still
    # gets working maps.
    roots.append(Path(__file__).resolve().parent / "maps")
    return roots


def maps_root() -> Path:
    """The active map store. First existing candidate wins, else the fallback."""
    override = os.environ.get("DATA_MAPS_ROOT") or os.environ.get("LCARS_MAPS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    candidates = _candidate_roots()
    for root in candidates:
        if root.exists():
            return root
    # Nothing exists yet. Pick the first candidate whose PARENT exists, so a
    # mounted archive drive is preferred over the dashboard folder.
    for root in candidates:
        try:
            if root.parent.exists():
                return root
        except OSError:
            continue
    return candidates[-1]


def _paths() -> dict[str, Path]:
    root = maps_root()
    return {
        "root": root,
        "bin": root / "bin",
        "assets": root / "assets",
        "fonts": root / "assets" / "fonts",
        "sprites": root / "assets" / "sprites",
        "regions": root / "regions",
        "manifest": root / "regions.json",
    }


def _pmtiles_exe() -> Path:
    name = "pmtiles.exe" if platform.system() == "Windows" else "pmtiles"
    return _paths()["bin"] / name


# ──────────────────────────────────────────────────────────────────────────
# Region catalog
#
# bbox is min_lon, min_lat, max_lon, max_lat (the order the pmtiles CLI wants).
# maxzoom drives size far more than area does: each extra zoom level roughly
# doubles the file. z14 is "every driveway and footpath", z12 is "every road",
# z10 is "every town", z6 is "coastlines and motorways".
# ──────────────────────────────────────────────────────────────────────────
CATALOG = [
    {
        "slug": "world",
        "name": "World overview",
        "detail": "Whole planet, coastlines through major roads. The orientation layer.",
        "bbox": [-180.0, -85.0, 180.0, 85.0],
        "maxzoom": 6,
        "est_gb": 1.1,
        "priority": 1,
    },
    {
        "slug": "idaho-panhandle",
        "name": "Idaho panhandle",
        "detail": "Sandpoint, Coeur d'Alene, Bonners Ferry, Spokane. Local roads and mapped trails.",
        "bbox": [-117.6, 46.4, -115.4, 49.2],
        "maxzoom": 14,
        "est_gb": 0.07,
        "priority": 1,
    },
    {
        "slug": "pacific-northwest",
        "name": "Pacific Northwest",
        "detail": "Idaho, Washington, Oregon, Montana, southern British Columbia. Road-level detail.",
        "bbox": [-125.0, 41.5, -110.0, 51.0],
        "maxzoom": 12,
        "est_gb": 0.22,
        "priority": 2,
    },
    {
        "slug": "usa",
        "name": "United States",
        "detail": "The lower 48 at town-and-highway detail. No live closures or turn-by-turn routing.",
        "bbox": [-125.5, 24.0, -66.5, 49.5],
        "maxzoom": 11,
        "est_gb": 0.9,
        "priority": 2,
    },
    {
        "slug": "british-columbia",
        "name": "British Columbia",
        "detail": "The nearest border crossing and everything north of it.",
        "bbox": [-139.1, 48.2, -114.0, 60.1],
        "maxzoom": 11,
        "est_gb": 1.4,
        "priority": 3,
    },
    {
        "slug": "india",
        "name": "India",
        "detail": "The whole subcontinent at town detail. Roads, towns and water features.",
        "bbox": [68.0, 6.5, 97.5, 35.7],
        "maxzoom": 11,
        "est_gb": 0.3,
        "priority": 2,
    },
    {
        "slug": "western-ghats",
        "name": "Western Ghats",
        "detail": "Goa through Kerala, with local road detail.",
        "bbox": [73.0, 8.0, 78.2, 21.0],
        "maxzoom": 14,
        "est_gb": 2.1,
        "priority": 3,
    },
]


def maps_catalog() -> dict:
    return {
        "catalog": CATALOG,
        "planet_url": PLANET_URL,
        "planet_build": PLANET_BUILD,
    }


# ──────────────────────────────────────────────────────────────────────────
# Status
# ──────────────────────────────────────────────────────────────────────────
def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _read_manifest() -> dict:
    mf = _paths()["manifest"]
    if not mf.exists():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(data: dict) -> None:
    mf = _paths()["manifest"]
    mf.parent.mkdir(parents=True, exist_ok=True)
    tmp = mf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, mf)


def installed_regions() -> list[dict]:
    """Regions actually present on disk. The manifest is advisory; the file is truth."""
    p = _paths()
    manifest = _read_manifest()
    out = []
    if not p["regions"].exists():
        return out
    for f in sorted(p["regions"].glob("*.pmtiles")):
        slug = f.stem
        meta = manifest.get(slug, {})
        cat = next((c for c in CATALOG if c["slug"] == slug), {})
        try:
            size = f.stat().st_size
            with f.open('rb') as stream:
                if size < 127 or stream.read(8) != b'PMTiles\x03':
                    continue
        except OSError:
            continue
        out.append({
            "slug": slug,
            "name": meta.get("name") or cat.get("name") or slug,
            "bytes": size,
            "gb": round(size / 1e9, 2),
            "bbox": meta.get("bbox") or cat.get("bbox"),
            "maxzoom": meta.get("maxzoom") or cat.get("maxzoom"),
            "added": meta.get("added", ""),
            "url": f"/maps/data/regions/{f.name}?v={f.stat().st_mtime_ns}",
        })
    return out


def _disk_free(path: Path) -> int:
    try:
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return shutil.disk_usage(str(probe)).free
    except OSError:
        return 0


def maps_status() -> dict:
    p = _paths()
    exe = _pmtiles_exe()

    has_cli = exe.is_file()
    has_renderer = all((p['assets'] / name).is_file() for name in
                       ('maplibre-gl.mjs', 'maplibre-gl-worker.mjs', 'maplibre-gl-shared.mjs', 'maplibre-gl.css'))
    has_reader = (p["assets"] / "pmtiles.js").is_file()
    has_style = (p["assets"] / "basemaps.js").is_file()
    has_fonts = p["fonts"].is_dir() and any(p["fonts"].iterdir()) if p["fonts"].is_dir() else False
    has_sprites = p["sprites"].is_dir() and any(p["sprites"].rglob("*.png")) if p["sprites"].is_dir() else False

    components = {
        "pmtiles_cli": has_cli,
        "maplibre": has_renderer,
        "pmtiles_js": has_reader,
        "basemap_style": has_style,
        "fonts": has_fonts,
        "sprites": has_sprites,
    }
    stack_ready = all(v for k, v in components.items() if k != "pmtiles_cli")
    regions = installed_regions()
    setup_active = False
    try:
        progress = json.loads((p['root'].parent / 'state/expansion-progress.json').read_text())
        setup_active = progress.get('status') == 'running' and __import__('offline_tasks').file_locked(
            p['root'].parent / 'state/expansion.lock')
    except (OSError, ValueError):
        pass

    return {
        "root": str(p["root"]),
        "setup_active": setup_active,
        "jobs": __import__("offline_tasks").recent(p["root"]),
        "can_download": has_cli,
        "root_exists": p["root"].exists(),
        "stack_ready": stack_ready,
        "ready": stack_ready and bool(regions),
        "components": components,
        "missing": [k for k, v in components.items() if not v],
        "regions": regions,
        "region_bytes": sum(r["bytes"] for r in regions),
        "assets_bytes": _dir_bytes(p["assets"]) + _dir_bytes(p["bin"]),
        "disk_free": _disk_free(p["root"]),
        "planet_url": PLANET_URL,
        "planet_build": PLANET_BUILD,
        "versions": {
            "pmtiles_cli": PMTILES_CLI_VERSION,
            "maplibre": MAPLIBRE_VERSION,
            "pmtiles_js": PMTILES_JS_VERSION,
            "basemaps": BASEMAPS_VERSION,
        },
    }


# ──────────────────────────────────────────────────────────────────────────
# Commands handed to the background-job runner
#
# These return a command string rather than doing the work inline, because a
# region extract can run for an hour. The bridge's job runner outlives the
# request and reports back when it finishes.
# ──────────────────────────────────────────────────────────────────────────
def _python_exe() -> str:
    import sys
    return sys.executable or "python"


def _shell_cmd(parts: list[str]) -> str:
    """Quote an argv into one command line for the job runner's default shell.

    The runner uses PowerShell on Windows and /bin/sh elsewhere. PowerShell
    needs the call operator when the program path is a quoted string, and it
    escapes an embedded quote by doubling it; sh uses single quotes. Getting
    this wrong is invisible until a path contains a space, and every path
    here does ("Program Files", "LCARS Offline").
    """
    if os.name == "nt":
        # Quote every argument, not just the ones with spaces. An unquoted
        # argument containing a comma can be parsed by PowerShell as an array,
        # and the bounding box is nothing but commas and minus signs.
        quoted = ["'" + str(p).replace("'", "''") + "'" for p in parts]
        return "& " + " ".join(quoted) + "; exit $LASTEXITCODE"
    import shlex
    return " ".join(shlex.quote(str(p)) for p in parts)


def install_command() -> tuple[str, str]:
    setup = Path(__file__).resolve().parent / "maps_setup.py"
    cmd = _shell_cmd([_python_exe(), str(setup),
                      "install", "--root", str(maps_root())])
    return cmd, "Install offline map stack"


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")


def extract_command(slug: str, bbox=None, maxzoom=None, name="") -> tuple[str, str]:
    """Build the command that carves one region out of the planet build."""
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise ValueError("bad region id: use lowercase letters, numbers and dashes")

    cat = next((c for c in CATALOG if c["slug"] == slug), None)
    if bbox is None and cat:
        bbox = cat["bbox"]
    if maxzoom is None and cat:
        maxzoom = cat["maxzoom"]
    if not name:
        name = (cat or {}).get("name") or slug

    if not bbox or len(bbox) != 4:
        raise ValueError("bbox required: min_lon,min_lat,max_lon,max_lat")
    try:
        bbox = [float(v) for v in bbox]
    except (TypeError, ValueError):
        raise ValueError("bbox values must be numbers")

    min_lon, min_lat, max_lon, max_lat = bbox
    if not (-180 <= min_lon < max_lon <= 180):
        raise ValueError("longitude out of range or reversed")
    if not (-85.06 <= min_lat < max_lat <= 85.06):
        raise ValueError("latitude out of range or reversed")

    maxzoom = int(maxzoom or 12)
    if not 1 <= maxzoom <= 15:
        raise ValueError("maxzoom must be between 1 and 15")

    setup = Path(__file__).resolve().parent / "maps_setup.py"
    bbox_s = ",".join(f"{v:g}" for v in bbox)
    # The name is user-supplied, so it never reaches a shell unquoted.
    name = re.sub(r"[^\w .,'()-]", "", str(name))[:60] or slug
    cmd = _shell_cmd([
        _python_exe(), str(setup), "region",
        "--root", str(maps_root()),
        "--slug", slug,
        # `--bbox=VALUE`, never `--bbox VALUE`: a bounding box usually starts
        # with a minus sign, and argparse reads a leading-minus value as a
        # flag unless it is glued to the option with an equals sign.
        f"--bbox={bbox_s}",
        f"--maxzoom={maxzoom}",
        f"--name={name}",
    ])
    return cmd, f"Download map region: {name}"


def delete_region(slug: str) -> dict:
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        return {"error": "bad region id"}
    p = _paths()
    target = (p["regions"] / f"{slug}.pmtiles").resolve()
    try:
        target.relative_to(p["regions"].resolve())
    except ValueError:
        return {"error": "outside map store"}
    if not target.is_file():
        return {"error": "region not found"}
    freed = target.stat().st_size
    target.unlink()
    manifest = _read_manifest()
    manifest.pop(slug, None)
    _write_manifest(manifest)
    return {"ok": True, "slug": slug, "freed": freed}


# ──────────────────────────────────────────────────────────────────────────
# Serving map files to the browser, with HTTP Range support.
#
# This is not optional polish. pmtiles.js reads a multi-GB archive by asking
# for a few kilobytes at a time; without 206 responses the browser would try
# to download the entire region file to draw one tile.
# ──────────────────────────────────────────────────────────────────────────
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

_EXTRA_MIME = {
    ".pmtiles": "application/octet-stream",
    ".pbf": "application/x-protobuf",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
}


def resolve_asset(relpath: str) -> Path | None:
    """Map a /maps/data/... URL onto a file inside the map store. Sandboxed."""
    relpath = (relpath or "").lstrip("/")
    if not relpath or ".." in relpath:
        return None
    root = maps_root().resolve()
    # Only these subtrees are reachable over HTTP. `bin` is deliberately not.
    allowed = {"assets", "regions"}
    head = relpath.split("/", 1)[0]
    if head not in allowed:
        return None
    try:
        target = (root / relpath).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def serve_file(handler, target: Path) -> None:
    """Stream `target` to the client, honouring a Range header if present."""
    try:
        size = target.stat().st_size
    except OSError:
        handler.send_error(404)
        return

    mime = _EXTRA_MIME.get(target.suffix.lower()) \
        or mimetypes.guess_type(target.name)[0] \
        or "application/octet-stream"

    range_header = handler.headers.get("Range", "")
    start, end = 0, size - 1
    partial = False

    if range_header:
        m = _RANGE_RE.fullmatch(range_header.strip())
        if not m or not any(m.groups()):
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        if m:
            g1, g2 = m.group(1), m.group(2)
            if g1:
                start = int(g1)
                if g2:
                    end = min(int(g2), size - 1)
            elif g2:
                # suffix range: last N bytes. pmtiles.js uses this to find the
                # archive footer.
                start = max(0, size - int(g2))
            if start >= size or start > end:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Content-Length", "0")
                _cors(handler)
                handler.end_headers()
                return
            partial = True

    length = end - start + 1
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    # Map data is immutable once written; let the browser cache hard.
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("ETag", f'"{target.stat().st_mtime_ns:x}-{size:x}"')
    _cors(handler)
    handler.end_headers()

    if handler.command == "HEAD":
        return

    CHUNK = 256 * 1024
    remaining = length
    try:
        with open(target, "rb") as fh:
            fh.seek(start)
            while remaining > 0:
                buf = fh.read(min(CHUNK, remaining))
                if not buf:
                    break
                handler.wfile.write(buf)
                remaining -= len(buf)
    except (BrokenPipeError, ConnectionResetError):
        # The map panned away and the browser cancelled the tile. Normal.
        pass


def _cors(handler) -> None:
    send_cors = getattr(handler, "send_cors", None)
    if callable(send_cors):
        send_cors()
        # The browser must be allowed to read Content-Range for range reads
        # to work through the fetch API.
        handler.send_header("Access-Control-Expose-Headers",
                            "Content-Range, Content-Length, Accept-Ranges")
    else:
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        handler.send_header("Access-Control-Expose-Headers",
                            "Content-Range, Content-Length, Accept-Ranges")


# ──────────────────────────────────────────────────────────────────────────
# Size estimate before committing to a download
# ──────────────────────────────────────────────────────────────────────────
def estimate_region(bbox, maxzoom) -> dict:
    """Ask the pmtiles CLI how big a region would be, without downloading it."""
    exe = _pmtiles_exe()
    if not exe.is_file():
        return {"error": "map stack not installed"}
    try:
        bbox_s = ",".join(f"{float(v):g}" for v in bbox)
    except (TypeError, ValueError):
        return {"error": "bad bbox"}
    cmd = [str(exe), "extract", PLANET_URL, os.devnull,
           f"--bbox={bbox_s}", f"--maxzoom={int(maxzoom)}", "--dry-run"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as e:
        return {"error": str(e)}
    blob = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"archive size of ([\d.]+)\s*(kB|MB|GB|TB)", blob, re.I)
    if m:
        mult = {"KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[m.group(2).upper()]
        return {"ok": True, "bytes": int(float(m.group(1)) * mult), "raw": blob.strip()[:800]}
    return {"ok": False, "raw": blob.strip()[:800]}


def handle_http(handler, path: str, body=None) -> bool:
    """Called behind the host dashboard's authentication and origin checks."""
    if not path.startswith('/maps/'):
        return False
    import offline_tasks
    try:
        if body is None:
            if path == '/maps/status':
                handler._json(maps_status())
            elif path == '/maps/catalog':
                handler._json(maps_catalog())
            elif path.startswith('/maps/data/'):
                from urllib.parse import unquote
                target = resolve_asset(unquote(path[len('/maps/data/'):]))
                if target is None:
                    handler.send_error(404)
                else:
                    serve_file(handler, target)
            else:
                handler._json({'error': 'Unknown maps route'}, 404)
            return True
        # An unrelated website must not start downloads on localhost.
        from urllib.parse import urlsplit
        origin = handler.headers.get('Origin')
        if origin and urlsplit(origin).netloc != handler.headers.get('Host'):
            handler._json({'error': 'Open Maps in this dashboard to change its files.'}, 403)
            return True
        data = json.loads(body) if body else {}
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        root = maps_root()
        setup = Path(__file__).with_name('maps_setup.py')
        if path == '/maps/install':
            argv = offline_tasks.python_argv(setup, 'install', '--root', root)
            job = offline_tasks.start(root, 'install', argv, 'Install offline maps')
            handler._json({'ok': True, 'job': job})
        elif path == '/maps/region':
            # Validate with the same routine used by command-line integrations.
            slug = data.get('slug', '')
            cat = next((c for c in CATALOG if c['slug'] == slug), None)
            if not cat:
                raise ValueError('Choose a region from the catalog')
            if not maps_status()['can_download']:
                raise ValueError('Install the map tools first')
            extract_command(slug)
            if (root / 'regions' / (slug + '.pmtiles')).is_file():
                handler._json({'ok': True, 'already': True})
                return True
            argv = offline_tasks.python_argv(setup, 'region', '--root', root,
                '--slug', slug, '--bbox=' + ','.join(map(str, cat['bbox'])),
                '--maxzoom=' + str(cat['maxzoom']), '--name=' + cat['name'])
            job = offline_tasks.start(root, 'region:' + slug, argv, 'Download ' + cat['name'])
            handler._json({'ok': True, 'job': job})
        else:
            handler._json({'error': 'Unknown maps route'}, 404)
    except (ValueError, TypeError) as exc:
        handler._json({'error': str(exc)}, 400)
    except OSError as exc:
        handler._json({'error': str(exc)}, 500)
    return True
