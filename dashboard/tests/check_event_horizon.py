"""Verify Event Horizon is available by default, with disposable browser profiles and local fixtures."""
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
    # A brand-new profile with no Void Patrol progress can wear Event Horizon right away.
    assert page.evaluate("localStorage.getItem('data-void-patrol-v1:arcade-preview')") is None
    assert page.evaluate('currentTheme()') == 'minimal', 'Minimal stays the default selection'
    page.evaluate("showPanel('chat'); openSettings();")
    button = page.locator('[data-theme="event-horizon"]')
    expect(button).to_be_enabled()
    expect(button).to_have_text('EVENT HORIZON')
    expect(page.locator('#theme-status')).to_contain_text('Every theme is available')
    assert 'LOCKED' not in page.locator('#settings-theme-row').inner_text()
    page.screenshot(path=str(out / 'appearance.png'))
    button.click()
    assert page.evaluate('currentTheme()') == 'event-horizon'
    expect(button).to_have_attribute('aria-pressed', 'true')
    assert page.evaluate("localStorage.getItem('data-theme:arcade-preview')") == 'event-horizon'
    page.evaluate('closeSettings()')
    checks.append('available on a fresh profile with no arcade progress; selected from Settings > Appearance and saved')

    # The theme toggle cycles through all three themes.
    seen = []
    for _ in range(3):
        page.evaluate('toggleTheme()'); seen.append(page.evaluate('currentTheme()'))
    assert seen == ['minimal', 'cyber', 'event-horizon'], seen
    checks.append('the header theme toggle cycles Minimal, Cyber, and Event Horizon')

    # The arcade no longer carries any unlock or reward interface; clearing Sector 1 is an ordinary clear.
    page.evaluate("showPanel('arcade')")
    flight = page.frame(url=lambda url: '/arcade/index.html' in url)
    assert flight.evaluate("!document.querySelector('#reward-progress, #victory-reward, #equip-reward')")
    flight.evaluate(CAPTURE)
    frame.locator('#sound').click()
    frame.locator('#launch').click()
    flight.evaluate(CLEAR)
    expect(frame.locator('#upgrade-screen')).to_be_visible(timeout=6000)
    expect(frame.locator('#upgrade-title')).to_have_text('Choose one system upgrade')
    assert page.evaluate('currentTheme()') == 'event-horizon'
    page.screenshot(path=str(out / 'arcade-in-event-horizon.png'))
    frame.locator('.upgrade-card').first.click()
    assert flight.evaluate('testFlight.wave') == 2
    checks.append('no unlock or reward screens in the arcade; the embedded game wears the theme')

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
    checks.append('pure-black surfaces and rail controls')

    # The choice survives a reload, and each dashboard profile keeps its own.
    ready(page, base)
    page.wait_for_function("currentTheme() === 'event-horizon'")
    page.evaluate("_activeCaptain = 'theme-test-second'; ArcadeHost.reset();")
    assert page.evaluate('currentTheme()') == 'minimal'
    assert page.evaluate("applyTheme('event-horizon')") is True, 'a second profile can choose it immediately too'
    page.evaluate("applyTheme('cyber')")
    page.evaluate("_activeCaptain = 'arcade-preview'; ArcadeHost.reset();")
    assert page.evaluate('currentTheme()') == 'event-horizon'
    page.evaluate("_activeCaptain = 'theme-test-second'; ArcadeHost.reset();")
    assert page.evaluate('currentTheme()') == 'cyber'
    page.evaluate("_activeCaptain = 'arcade-preview'; ArcadeHost.reset();")
    for width in [390, 320]:
        page.set_viewport_size({'width': width, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        # The phone vitals toggle stays on screen and still opens and closes the right rail.
        assert page.locator('#mobile-vitals-toggle').evaluate('node => node.getBoundingClientRect().right <= innerWidth')
        page.locator('#mobile-vitals-toggle').click()
        assert page.locator('#rail-right').evaluate('node => node.getBoundingClientRect().left < innerWidth')
        page.locator('#mobile-vitals-toggle').click()
        page.screenshot(path=str(out / f'dashboard-{width}.png'))
    assert page.locator('.orbit-motion').evaluate('node => getComputedStyle(node).animationName') == 'none'
    checks.extend(['reload persistence and per-profile selection', '390px and 320px layouts with the phone vitals toggle, and reduced motion'])
    return surfaces


def run_edge_cases(browser, base, out, checks, allow):
    # Blocked storage: the theme still applies for this visit, with a clear notice.
    context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    context.route('**/*', allow)
    context.add_init_script("""Storage.prototype.setItem = () => { throw new Error('storage blocked for test'); };""")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    ready(page, base)
    page.evaluate("showPanel('chat'); openSettings();")
    page.locator('[data-theme="event-horizon"]').click()
    assert page.evaluate('currentTheme()') == 'event-horizon'
    expect(page.locator('#theme-status')).to_contain_text('for this visit')
    page.screenshot(path=str(out / 'blocked-storage.png'))
    assert not errors, errors
    context.close()
    # Unknown or legacy saved values fall back safely.
    context = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
    context.route('**/*', allow)
    context.add_init_script("""localStorage.setItem('data-theme:arcade-preview', 'cyberpunk');""")
    page = context.new_page()
    ready(page, base)
    assert page.evaluate('currentTheme()') == 'cyber'
    assert page.evaluate("applyTheme('not-a-theme')") is False and page.evaluate('currentTheme()') == 'cyber'
    context.close()
    checks.extend(['blocked storage still applies Event Horizon for the visit', 'legacy and unknown theme values fall back safely'])


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
