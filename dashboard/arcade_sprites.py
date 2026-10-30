"""Build the Void Patrol sprites in dashboard/arcade/assets from source artwork.

Usage (from the repository root, with Pillow, NumPy, and SciPy installed):
    python -I dashboard/arcade_sprites.py <source-dir> dashboard/arcade/assets <preview-dir>

<source-dir> must contain ship.png, interceptor.png, manta.png, fortress.png, and
stars.png: top-down artwork on a pure black backdrop. The script keys the backdrop
to real alpha (border-connected black plus large enclosed gaps, such as the
fortress frame openings), crops, and resizes with premultiplied alpha so edges
stay clean. Sprite geometry in renderer.mjs and engine.mjs (engine nozzles, pod
centers, barrel tips, the fortress core pivot) is measured against these crops,
so replacement artwork needs those numbers re-measured. Keyed previews on a
checkerboard are written to <preview-dir> for inspection.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

SRC, OUT, PREVIEW = (Path(p) for p in sys.argv[1:4])
OUT.mkdir(parents=True, exist_ok=True)
PREVIEW.mkdir(parents=True, exist_ok=True)

LOW, HIGH = 3, 16  # max-channel values: <=LOW is pure backdrop, >=HIGH is opaque


def key(path, hole_area):
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    v = rgb.max(axis=2)
    dark = v < HIGH
    labels, count = ndimage.label(dark, structure=np.ones((3, 3)))
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    areas = ndimage.sum(np.ones_like(v), labels, range(count + 1))
    keyed = np.zeros(count + 1, bool)
    keyed[edge] = True
    keyed[areas >= hole_area] = True
    keyed[0] = False
    backdrop = keyed[labels]
    alpha = np.where(backdrop, np.clip((v - LOW) / (HIGH - LOW), 0, 1), 1.0)
    safe = np.maximum(alpha, 1e-3)[..., None]
    color = np.where(alpha[..., None] > 0, np.clip(rgb / safe, 0, 255), 0)
    rgba = np.dstack([color, alpha * 255]).round().astype(np.uint8)
    holes = int(((areas >= hole_area) & ~np.isin(np.arange(count + 1), edge))[1:].sum())
    return Image.fromarray(rgba, "RGBA"), holes


def resize(im, width):
    height = round(im.height * width / im.width)
    return im.convert("RGBa").resize((width, height), Image.LANCZOS).convert("RGBA")


def preview(im, name):
    bg = Image.new("RGBA", im.size, (88, 94, 104, 255))
    tile = 24
    px = bg.load()
    for y in range(im.height):
        for x in range(im.width):
            if ((x // tile) + (y // tile)) % 2:
                px[x, y] = (70, 75, 84, 255)
    Image.alpha_composite(bg, im).convert("RGB").save(PREVIEW / f"{name}_keyed.png")


meta = {}
specs = {
    "ship": dict(hole=900, width=1024, crop=True),
    "interceptor": dict(hole=900, width=384, crop=True),
    "manta": dict(hole=900, width=448, crop=True),
    "fortress": dict(hole=1200, width=1024, crop=False),
}
for name, spec in specs.items():
    im, holes = key(SRC / f"{name}.png", spec["hole"])
    a = np.asarray(im)[..., 3]
    ys, xs = np.nonzero(a > 0)
    if spec["crop"]:
        m = 6
        box = (max(0, xs.min() - m), max(0, ys.min() - m), min(im.width, xs.max() + 1 + m), min(im.height, ys.max() + 1 + m))
        im = im.crop(box)
    else:
        box = (0, 0, im.width, im.height)
    out = resize(im, spec["width"])
    out.save(OUT / f"{name}.webp", "WEBP", quality=90, alpha_quality=100, method=6)
    small = resize(im, 640) if im.width > 640 else im
    preview(small, name)
    meta[name] = {"crop": box, "cropSize": [im.width, im.height], "out": list(out.size), "keyedHoles": holes,
                  "bytes": (OUT / f"{name}.webp").stat().st_size}

# Deep star layer: true black point, and an empty margin so tiles join without cut stars.
stars = np.asarray(Image.open(SRC / "stars.png").convert("RGB")).astype(np.int16)
v = stars.max(axis=2)
stars[v <= 6] = 0
margin = 8
stars[:margin] = 0; stars[-margin:] = 0; stars[:, :margin] = 0; stars[:, -margin:] = 0
Image.fromarray(stars.astype(np.uint8), "RGB").save(OUT / "stars.webp", "WEBP", lossless=True, method=6)
meta["stars"] = {"size": [stars.shape[1], stars.shape[0]], "bytes": (OUT / "stars.webp").stat().st_size}
print(json.dumps(meta, indent=1, default=int))
