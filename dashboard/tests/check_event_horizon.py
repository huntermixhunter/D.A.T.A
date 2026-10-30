"""Exercise the real reward flow with disposable browser profiles and local fixtures."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = """async () => {
  const {Game} = await import('/arcade/engine.mjs');
  const start = Game.prototype.start;
  Game.prototype.start = function() { window.testFlight = this; return start.call(this); };
}"""
CLEAR = """() => {
  const g = testFlight;
  g.player.invulnerable = 1e8; g.enemies = []; g.rocks = []; g.mines = []; g.spawnTimer = 1e8;
  for (const base of g.bases) {
    for (const pod of base.pods) g.hitBase(base, pod, 1000);
    g.hitBase(base, null, 1000);
  }
}"""


def ready(page, base):
    page.goto(base + '/#arcade', wait_until='domcontentloaded')
    page.wait_for_function("window.RewardThemes && window.getArcadeProfile?.() === 'arcade-preview'")
    frame = page.frame_locator('#arcade-frame')
    expect(frame.locator('#launch')).to_be_enabled()
    return frame


def run_flow(page, base, out, checks):
    frame = ready(page, base)
    assert page.evaluate("applyTheme('event-horizon')") is False
    expect(page.locator('[data-theme="event-horizon"]')).to_be_disabled()
    # Forged messages from the parent window or a different origin must be ignored.
    page.evaluate("""() => {
      dispatchEvent(new MessageEvent('message', {origin: location.origin, source: window,
        data: {type: 'data-arcade-progress', profile: 'arcade-preview', clearedSector: 1}}));
      dispatchEvent(new MessageEvent('message', {origin: 'https://invalid.example', source: document.querySelector('#arcade-frame').contentWindow,
        data: {type: 'data-arcade-progress', profile: 'arcade-preview', clearedSector: 1}}));
    }""")
    assert page.evaluate('RewardThemes.unlocked()') is False
    flight = page.frame(url=lambda url: '/arcade/index.html' in url)
    flight.evaluate(CAPTURE)
    frame.locator('#sound').click()
    frame.locator('#launch').click()
    assert page.evaluate('RewardThemes.unlocked()') is False
    flight.evaluate(CLEAR)
    expect(frame.locator('#upgrade-screen')).to_be_visible(timeout=6000)
    assert flight.evaluate('testFlight.clearedSector') == 1
    expect(frame.locator('#victory-reward')).to_be_visible()
    expect(page.locator('[data-theme="event-horizon"]')).to_be_enabled()
    assert page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:arcade-preview')).clearedSector") == 1
    page.screenshot(path=str(out / 'victory.png'))
    frame.locator('#equip-reward').click()
    page.wait_for_function("currentTheme() === 'event-horizon'")
    expect(frame.locator('#equip-reward')).to_have_text('EVENT HORIZON EQUIPPED')
    frame.locator('.upgrade-card').first.click()
    assert flight.evaluate('testFlight.wave') == 2
    page.evaluate("showPanel('chat')")
    flight.wait_for_function("testFlight.mode === 'paused'")
    page.evaluate("matrixNodes = SEED_NODES.map(node => ({...node})); matrixLinks = SEED_LINKS.map(link => ({...link})); renderMiniMatrix();")
    page.evaluate("appendMessage('data', 'Event Horizon is ready. Your workspace stays with you.');")
    page.evaluate("document.fonts.ready")
    page.screenshot(path=str(out / 'event-horizon-dashboard.png'))
    surfaces = page.evaluate("""() => Object.fromEntries(['body', '.data-sidebar', '.data-right', '.ship-holo-frame', '#mini-matrix', '#mini-matrix svg']
      .map(selector => [selector, document.querySelector(selector) ? getComputedStyle(document.querySelector(selector)).backgroundColor : null]))""")
    assert all(value == 'rgb(0, 0, 0)' for value in surfaces.values()), surfaces
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for side in ['left', 'right']:
        page.evaluate('side => toggleRail(side)', side)
        assert page.locator('#rail-' + side).evaluate("node => getComputedStyle(node).display") == 'none'
        page.evaluate('side => toggleRail(side)', side)
    for theme in ['minimal', 'cyber', 'event-horizon']:
        assert page.evaluate('theme => applyTheme(theme)', theme)
        assert page.evaluate('currentTheme()') == theme
    ready(page, base)
    page.wait_for_function("currentTheme() === 'event-horizon'")
    page.evaluate("_activeCaptain = 'reward-test-second'; ArcadeHost.reset();")
    assert page.evaluate('currentTheme()') == 'minimal'
    assert page.evaluate('RewardThemes.unlocked()') is False
    page.evaluate("_activeCaptain = 'arcade-preview'; ArcadeHost.reset();")
    assert page.evaluate('currentTheme()') == 'event-horizon'
    page.evaluate("showPanel('chat'); openSettings();")
    expect(page.locator('[data-theme="event-horizon"]')).to_be_visible()
    page.screenshot(path=str(out / 'appearance.png'))
    page.evaluate('closeSettings()')
    for width in [390, 320]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(out / f'dashboard-{width}.png'))
    assert page.locator('.orbit-motion').evaluate('node => getComputedStyle(node).animationName') == 'none'
    checks.extend(['locked before victory; foreign messages ignored', 'first-sector victory and immediate saving',
                   'equip without leaving the run; continue to Sector 2; leaving pauses',
                   'pure-black artwork and scan backgrounds', 'all themes and rail controls',
                   'reload persistence and profile isolation', 'Appearance, 390px and 320px layouts, reduced motion'])
    return surfaces


def run_edge_cases(browser, base, out, checks, allow):
    context = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
    context.route('**/*', allow)
    context.add_init_script("""localStorage.setItem('data-void-patrol-v1:arcade-preview', JSON.stringify({sector: 2}));
      localStorage.setItem('data-theme:arcade-preview', 'event-horizon');""")
    page = context.new_page()
    ready(page, base)
    page.wait_for_function("currentTheme() === 'event-horizon'")
    page.evaluate("showPanel('chat'); openSettings()")
    for theme in ['minimal', 'cyber', 'event-horizon']:
        page.locator('[data-theme="' + theme + '"]').click()
        assert page.evaluate('currentTheme()') == theme
    page.evaluate('closeSettings()')
    for width in [390, 320]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.locator('#mobile-vitals-toggle').evaluate('node => node.getBoundingClientRect().right <= innerWidth')
        page.locator('#mobile-vitals-toggle').click()
        assert page.locator('#rail-right').evaluate('node => node.getBoundingClientRect().left < innerWidth')
        page.locator('#mobile-vitals-toggle').click()
        page.screenshot(path=str(out / f'dashboard-{width}.png'))
    context.close()
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    context.route('**/*', allow)
    context.add_init_script("""Storage.prototype.setItem = () => { throw new Error('storage blocked for test'); };""")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    frame = ready(page, base)
    flight = page.frame(url=lambda url: '/arcade/index.html' in url)
    flight.evaluate(CAPTURE)
    frame.locator('#sound').click()
    frame.locator('#launch').click()
    # Real first-sector combat and banking must work even when storage is blocked.
    flight.evaluate(CLEAR)
    expect(frame.locator('#victory-reward')).to_be_visible(timeout=6000)
    frame.locator('#equip-reward').click()
    page.wait_for_function("currentTheme() === 'event-horizon'")
    expect(frame.locator('#storage-notice')).to_contain_text('only for this visit')
    page.screenshot(path=str(out / 'victory-mobile.png'))
    assert flight.evaluate('document.documentElement.scrollWidth <= innerWidth')
    frame.locator('#victory-hangar').click()
    expect(frame.locator('#hangar')).to_be_visible()
    assert not errors, errors
    context.close()
    checks.extend(['legacy Sector 2 unlock migration', 'blocked storage still permits this-visit unlock and equip',
                   'mobile victory controls and finish-run action'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://localhost:7831')
    parser.add_argument('--edges', action='store_true')
    parser.add_argument('--artifacts', type=Path, default=ROOT.parent / 'build/event-horizon-checks')
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors, requests = [], [], []
    def allow(route):
        url = route.request.url
        host = urlsplit(url).hostname
        if url.startswith(args.base + '/') or host in {'fonts.googleapis.com', 'fonts.gstatic.com', 'd3js.org'}:
            route.continue_()
        else:
            route.abort()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        surfaces = {}
        if args.edges:
            run_edge_cases(browser, args.base, args.artifacts, checks, allow)
        else:
            context = browser.new_context(viewport={'width': 1920, 'height': 1080}, reduced_motion='reduce')
            context.route('**/*', allow)
            context.on('request', lambda request: requests.append(request.url))
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            surfaces = run_flow(page, args.base, args.artifacts, checks)
            assert not errors, errors
        browser.close()
    assert not any(urlsplit(url).port == 7777 for url in requests)
    result = {'result': 'PASS', 'checks': checks, 'errors': errors, 'surfaces': surfaces}
    (args.artifacts / ('edge-results.json' if args.edges else 'results.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
