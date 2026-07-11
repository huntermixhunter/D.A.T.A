"""Render a Field Debrief HTML to a print-perfect, FAST-rendering PDF.

Usage:
    python render.py <work_dir> [html_name] [pdf_name]

Defaults: html_name=debrief.html, pdf_name=DATA_Debrief.pdf
The HTML must use the .page (794x1123) layout from the skill template.

Why this file flattens the PDF
------------------------------
The HUD look is built from hundreds of semi-transparent vector shapes,
gradients, and blend layers. A normal `page.pdf()` keeps all of that as live
vector art, so browser PDF viewers (Chrome and Edge both use CPU software
rasterizers) must re-composite every layer on every scroll frame. Result: a
741 KB file that takes ~1.8 s to paint all six pages and scrolls unusably.

The fix: rasterize each .page to a single flat image at 2x, then rebuild the
PDF from those images. The viewer now decodes one bitmap per page instead of
compositing hundreds of transparent vectors. Same pixels, ~15x faster paint
(~120 ms for six pages). Costs a little file size (~1.5 MB) and text
selectability, which is the right trade for a purely visual HUD artifact.

Pure Playwright, no extra dependencies (no PyMuPDF / Pillow / img2pdf), so it
runs on the droplet render-service venv as-is.
"""
import base64
import sys
import pathlib
from playwright.sync_api import sync_playwright

work = pathlib.Path(sys.argv[1]).resolve()
html_name = sys.argv[2] if len(sys.argv) > 2 else "debrief.html"
pdf_name = sys.argv[3] if len(sys.argv) > 3 else "DATA_Debrief.pdf"

PAGE_W, PAGE_H = 794, 1123      # CSS px of one .page (A4 @ 96 dpi)
SCALE = 2                        # capture at 2x for crisp text/edges

src = (work / html_name).as_uri()
out = str(work / pdf_name)

with sync_playwright() as p:
    b = p.chromium.launch()

    # 1) Load the real HUD HTML and screenshot each .page as a flat PNG.
    pg = b.new_page(viewport={"width": PAGE_W, "height": PAGE_H},
                    device_scale_factor=SCALE)
    pg.goto(src, wait_until="networkidle")
    pg.wait_for_timeout(1200)  # let webfonts settle

    pages = pg.query_selector_all(".page")
    if not pages:
        raise SystemExit("render.py: no .page elements found in " + html_name)

    shots = []
    for i, el in enumerate(pages):
        png = el.screenshot(type="png")
        shots.append("data:image/png;base64," + base64.b64encode(png).decode())

    # 2) Build a flat HTML: one full-bleed image per page, nothing else.
    imgs = "".join(
        f'<div class="page"><img src="{d}"></div>' for d in shots
    )
    flat_html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
      * {{ margin:0; padding:0; box-sizing:border-box; }}
      .page {{ width:{PAGE_W}px; height:{PAGE_H}px; overflow:hidden;
               page-break-after:always; break-after:page; }}
      .page:last-child {{ page-break-after:auto; break-after:auto; }}
      img {{ width:{PAGE_W}px; height:{PAGE_H}px; display:block; }}
    </style></head><body>{imgs}</body></html>"""

    flat_path = work / "_flat_render.html"
    flat_path.write_text(flat_html, encoding="utf-8")

    # 3) Render the flat HTML to the final PDF.
    pg2 = b.new_page()
    pg2.goto(flat_path.as_uri(), wait_until="networkidle")
    pg2.pdf(path=out, width=f"{PAGE_W}px", height=f"{PAGE_H}px",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})

    b.close()

print("PDF written (flattened):", out)
