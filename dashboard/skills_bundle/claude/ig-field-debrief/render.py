"""Render a Field Debrief HTML to a print-perfect PDF.

Usage:
    python render.py <work_dir> [html_name] [pdf_name]

Defaults: html_name=debrief.html, pdf_name=DATA_Debrief.pdf
The HTML must use the .page (794x1123) layout from the skill template.
"""
import sys
import pathlib
from playwright.sync_api import sync_playwright

work = pathlib.Path(sys.argv[1]).resolve()
html_name = sys.argv[2] if len(sys.argv) > 2 else "debrief.html"
pdf_name = sys.argv[3] if len(sys.argv) > 3 else "DATA_Debrief.pdf"

src = (work / html_name).as_uri()
out = str(work / pdf_name)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page()
    pg.goto(src, wait_until="networkidle")
    pg.wait_for_timeout(1200)  # let webfonts settle
    pg.pdf(path=out, width="794px", height="1123px", print_background=True,
           margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
    b.close()
print("PDF written:", out)
