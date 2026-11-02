"""Verify difficulty selection, the dreadnought fight, red and blue lasers, and the wider view in a real browser."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = """async () => {
  const {Game} = await import('/arcade/engine.mjs');
  const start = Game.prototype.start;
  Game.prototype.start = function() { window.fixtureGame = this; return start.call(this); };
}"""
CALM = """() => { const g = fixtureGame; g.spawnTimer = 1e9; g.enemies = []; g.mines = []; g.rocks = []; g.player.invulnerable = 1e9;
  for (const base of g.bases) for (const pod of base.pods) pod.shotTimer = 1e9; }"""
CLEAR = """() => { const g = fixtureGame; for (const base of g.bases) { for (const pod of base.pods) g.hitBase(base, pod, 1e4); g.hitBase(base, null, 1e4); } }"""
# Park the dreadnought just ahead of the ship, broadside on, with its weapons held.
PARK_BOSS = """() => { const g = fixtureGame, b = g.boss, p = g.player;
  p.angle = 0; p.vx = p.vy = 0; b.x = (p.x + 620) % 4800; b.y = p.y; b.angle = Math.PI / 2; g.placeBoss(b);
  b.ringTimer = b.launchTimer = b.coreTimer = 1e9; for (const gun of b.guns) gun.timer = 1e9; }"""
FREEZE = "() => { fixtureGame.pause(); }"


def selected(page):
    return page.locator('#difficulty-options [aria-checked="true"]').get_attribute('data-difficulty')


def run(base, out):
    out.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base + '/arcade/', wait_until='networkidle')
        expect(page.locator('#launch')).to_be_enabled()

        # Difficulty: four levels, Pilot by default, chosen by click, arrow keys, Home and End, and saved.
        options = page.locator('#difficulty-options [role="radio"]')
        assert options.count() == 4 and options.all_inner_texts() == ['CADET', 'PILOT', 'VETERAN', 'ACE']
        assert selected(page) == 'pilot'
        page.locator('[data-difficulty="ace"]').click()
        assert selected(page) == 'ace'
        expect(page.locator('#difficulty-detail')).to_contain_text('Score x2')
        page.locator('[data-difficulty="ace"]').focus(); page.keyboard.press('ArrowLeft')
        assert selected(page) == 'veteran' and page.evaluate("document.activeElement.dataset.difficulty") == 'veteran'
        page.keyboard.press('Home'); assert selected(page) == 'cadet'
        page.keyboard.press('End'); assert selected(page) == 'ace'
        assert page.evaluate("document.body.dataset.state") == 'menu', 'arrow keys never launch or leave the title'
        assert page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:solo')).difficulty") == 'ace'
        page.reload(wait_until='networkidle')
        assert selected(page) == 'ace', 'the choice survives a reload'
        assert page.evaluate("[...document.querySelectorAll('#difficulty-options [role=radio]')].map(b => b.tabIndex).join()") == '-1,-1,-1,0'
        page.screenshot(path=str(out / 'title-difficulty.png'))
        # Enter launches straight from a focused level, as the controls panel promises.
        page.evaluate(CAPTURE); page.locator('[data-difficulty="ace"]').focus(); page.keyboard.press('Enter')
        page.wait_for_function("fixtureGame.mode === 'playing'")
        assert page.evaluate("fixtureGame.difficulty.id") == 'ace'
        assert page.evaluate("fixtureGame.viewRadius") > 900 and page.evaluate("fixtureGame.spawnRadius") >= 1350
        page.keyboard.press('KeyP'); expect(page.locator('#pause-meta')).to_have_text('FLIGHT ON HOLD / ACE')
        page.locator('#return-hangar').click(); page.locator('#hangar-back').click()
        page.locator('[data-difficulty="pilot"]').click(); assert selected(page) == 'pilot'
        checks.append('difficulty radio group: click, arrow keys, Home/End, Enter launches from a focused level, saved per profile, applied to the run, shown on pause')

        # Lasers: red at launch, a preview on the upgrade card, and blue after the second weapon upgrade.
        page.evaluate(CAPTURE); page.locator('#launch').click()
        page.wait_for_function("fixtureGame.mode === 'playing' && fixtureGame.difficulty.id === 'pilot'")
        page.evaluate(CALM)
        expect(page.locator('#laser-name')).to_have_text('RED LASERS')
        page.keyboard.down('Space'); page.wait_for_timeout(250)
        assert page.evaluate("fixtureGame.shots.filter(s => s.owner === 'player').every(s => s.color === 'red')")
        page.screenshot(path=str(out / 'laser-red.png'))
        page.keyboard.up('Space')
        page.evaluate("() => { fixtureGame.upgrades.rapid = 1; }")
        page.evaluate(CLEAR)
        page.wait_for_function("fixtureGame.mode === 'upgrade'")
        page.evaluate("() => { fixtureGame.choices = ['power', 'hull', 'spread']; }")
        expect(page.locator('#upgrade-screen')).to_be_visible(timeout=5000)
        assert page.locator('.laser-note').count() == 2, 'both weapon cards preview the blue lasers'
        assert page.locator('[data-choice="hull"] .laser-note').count() == 0
        page.screenshot(path=str(out / 'upgrade-laser-preview.png'))
        page.locator('[data-choice="power"]').click()
        page.wait_for_function("fixtureGame.mode === 'playing' && fixtureGame.laser === 'blue'")
        expect(page.locator('#toast')).to_have_text('BLUE LASERS ONLINE')
        expect(page.locator('#laser-name')).to_have_text('BLUE LASERS')
        assert page.locator('#weapons').get_attribute('data-laser') == 'blue'
        page.evaluate(CALM)
        page.keyboard.down('Space'); page.wait_for_timeout(250)
        assert page.evaluate("fixtureGame.shots.filter(s => s.owner === 'player').every(s => s.color === 'blue')")
        page.screenshot(path=str(out / 'laser-blue.png'))
        page.keyboard.up('Space')
        checks.append('red lasers at launch, blue-laser preview on weapon cards, blue lasers and callout after the second weapon upgrade')

        # The dreadnought: every fifth sector, with warning, HUD, shield, charge, ring, collapse, kill, and full repair.
        page.evaluate("() => { fixtureGame.wave = 4; }")
        page.evaluate(CLEAR)
        expect(page.locator('#upgrade-screen')).to_be_visible(timeout=5000)
        expect(page.locator('#upgrade-detail')).to_contain_text('A dreadnought guards the next sector.')
        page.locator('.upgrade-card').first.click()
        page.wait_for_function("fixtureGame.wave === 5 && fixtureGame.boss && fixtureGame.mode === 'playing'")
        expect(page.locator('#toast')).to_have_text('WARNING / DREADNOUGHT SIGHTED')
        page.wait_for_timeout(400)
        expect(page.locator('#toast')).to_have_text('WARNING / DREADNOUGHT SIGHTED')  # condition alerts cannot bury it
        expect(page.locator('#boss-bar')).to_be_visible()
        expect(page.locator('#boss-state')).to_have_text('SHIELD UP / 4 BATTERIES')
        expect(page.locator('#objective')).to_have_text('DESTROY 4 BATTERIES')
        expect(page.locator('#radar-bases')).to_have_text('BOSS')
        assert page.locator('.key-boss').is_visible() and not page.locator('.key-station').is_visible()
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / 'boss-arrival.png'))
        page.evaluate(CALM); page.evaluate(PARK_BOSS); page.wait_for_timeout(700)
        page.screenshot(path=str(out / 'boss-shielded.png'))
        page.evaluate("() => { fixtureGame.boss.ringTimer = 0; }"); page.wait_for_timeout(560)
        assert page.evaluate("fixtureGame.boss.charge") > .3
        page.screenshot(path=str(out / 'boss-charging.png'))
        page.wait_for_timeout(500)
        assert page.evaluate("fixtureGame.shots.filter(s => s.owner === 'enemy' && s.kind === 'plasma').length") >= 14, 'the plasma ring fired'
        page.screenshot(path=str(out / 'boss-ring.png'))
        page.evaluate("() => { const b = fixtureGame.boss; b.ringTimer = 1e9; b.charge = 0; fixtureGame.shots = []; for (const gun of b.guns) fixtureGame.hitGun(b, gun, 1e4); b.coreTimer = 1e9; }")
        expect(page.locator('#toast')).to_have_text('SHIELD DOWN. HIT THE HULL.')
        expect(page.locator('#boss-bar')).to_have_attribute('data-phase', '2')
        expect(page.locator('#objective')).to_have_text('DESTROY THE HULL')
        page.evaluate("() => { const b = fixtureGame.boss; fixtureGame.damageBoss(b, Math.floor(b.maxHp * .55)); }")
        page.wait_for_timeout(900)
        expect(page.locator('#boss-state')).to_contain_text('SHIELD DOWN / HULL')
        page.screenshot(path=str(out / 'boss-burning.png'))
        page.evaluate("() => { fixtureGame.player.hull = 9; fixtureGame.damageBoss(fixtureGame.boss, 1e6); }")
        page.wait_for_timeout(900)
        page.screenshot(path=str(out / 'boss-destroyed.png'))
        expect(page.locator('#upgrade-screen')).to_be_visible(timeout=5000)
        expect(page.locator('#upgrade-title')).to_have_text('Dreadnought destroyed. Choose one system upgrade')
        page.screenshot(path=str(out / 'boss-upgrade.png'))
        page.locator('.upgrade-card').first.click()
        page.wait_for_function("fixtureGame.wave === 6 && fixtureGame.mode === 'playing'")
        assert page.evaluate("fixtureGame.player.hull === fixtureGame.maxHull"), 'full repair after the dreadnought'
        assert page.evaluate("fixtureGame.boss") is None and page.locator('#boss-bar').is_hidden()
        assert not page.locator('.key-boss').is_visible()
        checks.append('dreadnought warning, status bar, radar key, shield, charge-up, plasma ring, shield collapse, kill sequence, and full repair')

        # Results show the difficulty. A dreadnought callout on the tick the ship is lost stays silent.
        toast_before = page.locator('#toast').text_content()
        assert toast_before != 'SHIELD DOWN. HIT THE HULL.', 'the check needs a different toast showing first'
        page.evaluate("""() => { const g = fixtureGame; g.player.invulnerable = 0; g.damagePlayer(1e6);
          g.emit('bossShield', { x: g.player.x, y: g.player.y }); }""")
        page.wait_for_timeout(300)
        assert page.locator('#toast').text_content() == toast_before, 'no callout once the ship is lost'
        expect(page.locator('#gameover')).to_be_visible(timeout=5000)
        expect(page.locator('#gameover-meta')).to_have_text('FLIGHT RECORD / PILOT')
        checks.append('flight record shows the difficulty, and no callout talks over the ship being lost')

        # Phone layouts keep the selector and the dreadnought bar inside the screen.
        for width in [390, 320]:
            page.set_viewport_size({'width': width, 'height': 844})
            page.locator('#gameover-hangar').click() if page.locator('#gameover-hangar').is_visible() else None
            if page.locator('#hangar').is_visible(): page.locator('#hangar-back').click()
            expect(page.locator('#menu')).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            box = page.locator('#difficulty-options').bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= width
            page.screenshot(path=str(out / f'title-{width}.png'))
        page.evaluate(CAPTURE); page.locator('#launch').click()
        page.wait_for_function("fixtureGame.mode === 'playing'")
        page.evaluate("() => { fixtureGame.wave = 4; }"); page.evaluate(CLEAR)
        expect(page.locator('#upgrade-screen')).to_be_visible(timeout=5000)
        page.locator('.upgrade-card').first.scroll_into_view_if_needed(); page.locator('.upgrade-card').first.click()
        page.wait_for_function("fixtureGame.boss && fixtureGame.mode === 'playing'")
        page.evaluate(CALM); page.evaluate(PARK_BOSS); page.wait_for_timeout(500)
        bar, radar = page.locator('#boss-bar').bounding_box(), page.locator('.radar-panel').bounding_box()
        assert bar['x'] >= 0 and bar['x'] + bar['width'] <= 320 and bar['x'] + bar['width'] <= radar['x'], (bar, radar)
        page.screenshot(path=str(out / 'boss-320.png'))
        checks.append('390px and 320px title layouts, and the dreadnought bar clear of the radar on a phone')
        assert not errors, errors
        checks.append('no game JavaScript errors')
        browser.close()
    result = {'result': 'PASS', 'checks': checks, 'errors': errors, 'artifacts': str(out)}
    (out / 'feature-results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://localhost:7817')
    parser.add_argument('--artifacts', type=Path, default=ROOT.parent / 'build' / 'arcade-checks' / 'features')
    args = parser.parse_args()
    run(args.base.rstrip('/'), args.artifacts)
