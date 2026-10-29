"""Screenshot the marketplace panel in both dashboard themes.

Local development helper. Expects marketplace_preview.py running on --port.
Usage:  python tests/marketplace_theme_shots.py --port 7801
"""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent.parent / "screenshots"


def shoot(page, theme, label, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate(
        """t => {
            document.body.classList.remove('theme-minimal', 'theme-cyberpunk');
            document.body.classList.add(t);
            localStorage.setItem('data-theme', t === 'theme-cyberpunk' ? 'cyber' : 'minimal');
        }""",
        theme,
    )
    page.wait_for_timeout(500)
    target = OUT / f"marketplace-{label}.png"
    page.screenshot(path=str(target), full_page=False)
    print(f"  wrote {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=7801)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        # The host app polls status forever and pulls d3 from a CDN. Block
        # off-origin requests except Google Fonts (Orbitron, Inter,
        # JetBrains Mono are the point of the themes) and settle on the DOM
        # instead of networkidle.
        allowed = ("localhost", "127.0.0.1", "fonts.googleapis.com", "fonts.gstatic.com")
        context.route(
            "**/*",
            lambda route: route.continue_()
            if urlsplit(route.request.url).hostname in allowed
            else route.abort(),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://localhost:{args.port}/#marketplace", wait_until="domcontentloaded")
        page.wait_for_selector(".mp-card", timeout=15000)

        for theme, label in (("theme-minimal", "minimal"), ("theme-cyberpunk", "cyberpunk")):
            shoot(page, theme, label, 1440, 1000)
            shoot(page, theme, f"{label}-mobile", 430, 900)

        # Dialog pass: open the first listing in cyberpunk, then in minimal.
        page.set_viewport_size({"width": 1440, "height": 1000})
        for theme, label in (("theme-cyberpunk", "cyberpunk"), ("theme-minimal", "minimal")):
            page.evaluate(
                "t => { document.body.classList.remove('theme-minimal','theme-cyberpunk'); document.body.classList.add(t); }",
                theme,
            )
            page.click(".mp-card-open")
            page.wait_for_selector(".mp-dialog[open]", timeout=5000)
            page.wait_for_timeout(400)
            target = OUT / f"marketplace-{label}-dialog.png"
            page.screenshot(path=str(target))
            print(f"  wrote {target}")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        print("page errors:", errors or "none")
        browser.close()


if __name__ == "__main__":
    main()
