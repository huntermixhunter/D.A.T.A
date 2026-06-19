"""Screenshot each .page of a Field Debrief HTML to PNGs (2x scale, for chat preview / DM).

Usage:
    python shot.py <work_dir> [html_name]

Writes page_1.png ... page_N.png into <work_dir>.
"""
import sys
import pathlib
from playwright.sync_api import sync_playwright

work = pathlib.Path(sys.argv[1]).resolve()
html_name = sys.argv[2] if len(sys.argv) > 2 else "debrief.html"
src = (work / html_name).as_uri()

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=2)
    pg.goto(src, wait_until="networkidle")
    pg.wait_for_timeout(1500)
    pages = pg.query_selector_all(".page")
    print("page count:", len(pages))
    for i, el in enumerate(pages, start=1):
        el.screenshot(path=str(work / f"page_{i}.png"))
        print("wrote page_%d.png" % i)
    b.close()
print("done")
