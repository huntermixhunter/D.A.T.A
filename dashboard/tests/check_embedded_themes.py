"""Check live iframe themes with disposable profiles and the local fixture server."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def palette(page):
    return page.evaluate("""() => {
      const style = getComputedStyle(document.body);
      return Object.fromEntries(['orange', 'bg', 'bg2', 'text', 'text-dim'].map(key =>
        [key, style.getPropertyValue('--data-' + key).trim()]));
    }""")


def matches(frame, colors):
    frame.wait_for_function("""colors => {
      const style = getComputedStyle(document.documentElement);
      const aliases = {orange: 'orange', bg: 'bg', bg2: 'panel', text: 'text', 'text-dim': 'dim'};
      return Object.entries(aliases).every(([key, name]) =>
        style.getPropertyValue('--' + name).trim() === colors[key]);
    }""", arg=colors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://localhost:7832')
    parser.add_argument('--artifacts', type=Path, default=ROOT.parent / 'build/embedded-theme-checks')
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    errors, checks = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080}, reduced_motion='reduce')
        def allow(route):
            url = route.request.url
            if url.startswith(args.base + '/') or urlsplit(url).hostname in {'fonts.googleapis.com', 'fonts.gstatic.com', 'd3js.org'}:
                route.continue_()
            else:
                route.abort()
        context.route('**/*', allow)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.base + '/#arcade', wait_until='domcontentloaded')
        page.wait_for_function("window.RewardThemes && getArcadeProfile() === 'arcade-preview'")
        page.evaluate("localStorage.setItem('data-void-patrol-v1:arcade-preview', JSON.stringify({clearedSector: 1})); RewardThemes.paint();")
        for panel in ['arcade', 'crew-deck']:
            page.evaluate('name => showPanel(name)', panel)
            iframe = page.locator('#' + panel + '-frame')
            expect(iframe).to_be_visible()
            frame = iframe.element_handle().content_frame()
            frame.wait_for_function("document.documentElement.dataset.dashboardTheme")
            if panel == 'crew-deck':
                frame.wait_for_function('window.__crewDeck && window.CrewControls?.state')
            frame.evaluate("window.themeTestIdentity = 'same-document'")
            for theme in ['minimal', 'cyber', 'event-horizon']:
                assert page.evaluate('theme => applyTheme(theme)', theme)
                matches(frame, palette(page))
                frame.wait_for_function('theme => document.documentElement.dataset.dashboardTheme === theme', arg=theme)
                assert frame.evaluate('themeTestIdentity') == 'same-document'
                expected_font = page.evaluate("getComputedStyle(document.body).getPropertyValue('--font-main').trim()")
                assert frame.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--font-main').trim()") == expected_font
                frame.evaluate('document.fonts.ready')
                page.screenshot(path=str(args.artifacts / f'{panel}-{theme}.png'))
                if panel == 'crew-deck':
                    frame.locator('#crew-settings-open').click()
                    expect(frame.locator('#crew-settings-dialog')).to_be_visible()
                    assert frame.locator('#crew-settings-dialog').evaluate("node => getComputedStyle(node).backgroundColor") == frame.locator('.roster').evaluate("node => getComputedStyle(node).backgroundColor")
                    assert page.evaluate("applyTheme('minimal' === currentTheme() ? 'cyber' : 'minimal')")
                    matches(frame, palette(page))
                    expect(frame.locator('#crew-settings-dialog')).to_be_visible()
                    frame.locator('#crew-settings-cancel').click()
                    page.evaluate('theme => applyTheme(theme)', theme)
                    matches(frame, palette(page))
            # Marketplace palettes are inline token overrides and do not change a theme class.
            page.evaluate("""() => {
              for (const [name, value] of Object.entries({orange:'#ed9bd6', bg:'#120f16', bg2:'#211425', text:'#f6e8f5', 'text-dim':'#bbaabc'}))
                document.body.style.setProperty('--data-' + name, value);
            }""")
            matches(frame, palette(page))
            page.evaluate("['orange','bg','bg2','text','text-dim'].forEach(name => document.body.style.removeProperty('--data-' + name))")
            matches(frame, palette(page))
            for theme in ['minimal', 'cyber', 'event-horizon']:
                page.evaluate('theme => applyTheme(theme)', theme)
                for width in [390, 320]:
                    page.set_viewport_size({'width': width, 'height': 844})
                    matches(frame, palette(page))
                    assert frame.evaluate('document.documentElement.scrollWidth <= innerWidth'), (panel, theme, width)
                    if panel == 'crew-deck':
                        assert frame.locator('#mode-demo').evaluate("""node => {
                          const button = node.getBoundingClientRect(), group = node.parentElement.getBoundingClientRect();
                          return button.right <= group.right && button.left >= group.left;
                        }"""), (theme, width, 'Demo button clipped')
                        assert frame.locator('#crew-settings-open').evaluate('node => node.getBoundingClientRect().right <= innerWidth'), (theme, width)
                    page.screenshot(path=str(args.artifacts / f'{panel}-{theme}-{width}.png'))
            page.set_viewport_size({'width': 1920, 'height': 1080})
            checks.append(panel + ': all themes, live updates, fonts, marketplace override/removal, 390px and 320px')
        # A retained game receives theme updates while another panel is open.
        arcade = page.locator('#arcade-frame').element_handle().content_frame()
        page.evaluate("applyTheme('cyber')")
        matches(arcade, palette(page))
        assert arcade.evaluate('themeTestIdentity') == 'same-document'
        page.evaluate("showPanel('arcade')")
        matches(arcade, palette(page))
        checks.append('hidden game stays in sync without reloading; crew dialogs stay open during a switch')
        assert not errors, errors
        browser.close()
    result = {'result': 'PASS', 'checks': checks, 'errors': errors}
    (args.artifacts / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
