#!/usr/bin/env python3
"""
DATA — brand icon generator
----------------------------
Draws the DATA launcher/favicon icon from scratch with PIL: a dark rounded
tile with an electric-blue dart (the DATA-Class silhouette motif) and
thruster trail. Original geometry, no sourced artwork.

Run:  python tools/make_icon.py
Writes:
  dashboard/favicon.ico            (16/32/48/64/128/256 multi-size)
  dashboard/assets/icon-256.png    (for desktop shortcuts on Linux/mac)
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent / "dashboard"
S = 1024  # master canvas; downscaled for crispness


def rounded(draw, box, r, **kw):
    draw.rounded_rectangle(box, radius=r, **kw)


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ── Tile: near-black rounded square with hairline border ──
    m = 64                                   # outer margin
    rounded(d, (m, m, S - m, S - m), r=200, fill=(11, 13, 16, 255))
    rounded(d, (m, m, S - m, S - m), r=200, outline=(42, 51, 64, 255), width=10)
    # inner hairline for depth
    rounded(d, (m + 26, m + 26, S - m - 26, S - m - 26), r=176,
            outline=(32, 38, 46, 255), width=4)

    # ── Dart motif (stylised DATA-Class silhouette, pointing right) ──
    cx, cy = S * 0.56, S * 0.5
    dart = [
        (cx - 230, cy - 180),   # top-left wing tip
        (cx + 270, cy),         # nose
        (cx - 230, cy + 180),   # bottom-left wing tip
        (cx - 120, cy),         # tail notch
    ]

    # soft blue glow behind the dart
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(dart, fill=(77, 159, 255, 190))
    glow = glow.filter(ImageFilter.GaussianBlur(46))
    img.alpha_composite(glow)

    # dart body + darker core facet for dimension
    d = ImageDraw.Draw(img)
    d.polygon(dart, fill=(77, 159, 255, 255))
    d.polygon([(cx - 120, cy), (cx + 270, cy),
               (cx - 230, cy + 180)], fill=(46, 124, 214, 255))

    # ── Thruster trail: three fading ticks behind the tail, inside the tile ──
    for i, alpha in enumerate((230, 150, 80)):
        x1 = cx - 280 - i * 62
        d.rounded_rectangle((x1 - 40, cy - 20, x1, cy + 20),
                            radius=20, fill=(77, 159, 255, alpha))

    # ── Export ──
    (ROOT / "assets").mkdir(parents=True, exist_ok=True)
    png256 = img.resize((256, 256), Image.LANCZOS)
    png256.save(ROOT / "assets" / "icon-256.png")
    img.resize((256, 256), Image.LANCZOS).save(
        ROOT / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ROOT / "favicon.ico")
    print("wrote", ROOT / "assets" / "icon-256.png")


if __name__ == "__main__":
    main()
