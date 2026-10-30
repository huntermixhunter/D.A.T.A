"""Verify the arcade in a local browser with synthetic profiles and no external traffic."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_GAME = """async () => {
  const {Game} = await import('/arcade/engine.mjs');
  const start = Game.prototype.start;
  Game.prototype.start = function() { window.fixtureGame = this; return start.call(this); };
}"""
CLEAR_BASES = """() => {
  const g = window.fixtureGame;
  for (const base of g.bases) {
    for (const pod of base.pods) g.hitBase(base, pod, 100);
    g.hitBase(base, null, 100);
  }
}"""
# Keep the run safe and quiet so input checks are deterministic.
CALM = """() => { const g = window.fixtureGame; g.spawnTimer = 1e9; g.enemies = []; g.mines = []; g.rocks = [];
  for (const base of g.bases) { for (const pod of base.pods) pod.shotTimer = 1e9; } g.player.invulnerable = 1e9; }"""
PLAYER_SHOTS = "fixtureGame.shots.filter(shot => shot.owner === 'player').length"
ASSETS = [("index.html", "text/html"), ("game.mjs", "javascript"), ("engine.mjs", "javascript"),
          ("renderer.mjs", "javascript"), ("host.js", "javascript"), ("host.css", "text/css"), ("style.css", "text/css"),
          ("assets/ship.webp", "image/webp"), ("assets/interceptor.webp", "image/webp"), ("assets/manta.webp", "image/webp"),
          ("assets/fortress.webp", "image/webp"), ("assets/stars.webp", "image/webp"), ("assets/cutaway.webp", "image/webp"),
          ("assets/sounds/laser-shot.wav", "audio/"), ("assets/sounds/explosion.wav", "audio/"),
          ("assets/sounds/engine-loop.wav", "audio/"), ("assets/sounds/boost-loop.wav", "audio/"),
          ("assets/sounds/pulse.wav", "audio/")]


# Regressions found in review: gamepad loops and upgrade choice, keys held across screens,
# sub-frame taps, fullscreen exits, resize while paused, and two tabs sharing one profile.
REVIEW_HOOK = """async () => { const {Game} = await import('/arcade/engine.mjs'); const s = Game.prototype.start;
  Game.prototype.start = function(){ window.g = this; return s.call(this); }; }"""
REVIEW_CLEAR = "() => { for (const b of g.bases) { for (const p of b.pods) g.hitBase(b, p, 99); g.hitBase(b, null, 999); } }"
REVIEW_CALM = "() => { g.spawnTimer = 1e9; g.enemies = []; g.mines = []; g.rocks = []; for (const b of g.bases) for (const p of b.pods) p.shotTimer = 1e9; g.player.invulnerable = 1e9; }"
# A fake gamepad the test can drive, plus a counter of animation-frame callbacks.
PAD_AND_RAF = """
  window.__pad = { connected: true, axes: [0, 0, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, value: 0 })) };
  Object.defineProperty(Navigator.prototype, 'getGamepads', { configurable: true, value: () => [window.__pad] });
  window.__rafCalls = 0;
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = callback => { window.__rafCalls++; return raf(callback); };
"""
PRESS = "i => { window.__pad.buttons[i].pressed = true; window.__pad.buttons[i].value = 1; }"
RELEASE = "i => { window.__pad.buttons[i].pressed = false; window.__pad.buttons[i].value = 0; }"
RAF_RATE = """() => new Promise(resolve => { const start = window.__rafCalls; setTimeout(() => resolve(window.__rafCalls - start), 1000); })"""


def review_regressions(browser, base):
    result, errors = {}, []
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
    context.add_init_script(PAD_AND_RAF)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base + "/arcade/", wait_until="networkidle")
    page.evaluate(REVIEW_HOOK)

    # Gamepad A launches from the title screen.
    page.evaluate(PRESS, 0); page.wait_for_timeout(200); page.evaluate(RELEASE, 0)
    page.wait_for_function("window.g && g.mode === 'playing'", timeout=5000)
    page.evaluate(REVIEW_CALM)
    # Shallow analog angles and light deflections must survive the circular dead zone.
    for axes in [[.9, .12], [.12, -.9], [.24, .04]]:
        page.evaluate("axes => { window.__pad.axes = [...axes, 0, 0]; }", axes)
        page.wait_for_function("() => Math.abs(Math.atan2(Math.sin(g.player.angle - Math.atan2(__pad.axes[1], __pad.axes[0])), Math.cos(g.player.angle - Math.atan2(__pad.axes[1], __pad.axes[0])))) < .025", timeout=3000)
    heading = page.evaluate("() => { window.__pad.axes = [.07, .05, 0, 0]; return g.player.angle; }")
    page.wait_for_timeout(180)
    assert abs(page.evaluate("g.player.angle") - heading) < .001, "small stick drift must not steer"
    page.evaluate("() => { window.__pad.axes = [0, 0, 0, 0]; }")
    result["analog_steering"] = "shallow angles and light deflections remain precise; centered stick drift is ignored"
    baseline = page.evaluate(RAF_RATE)
    # Start pauses, A resumes, three times over. The loop count must not grow.
    for _ in range(3):
        page.evaluate(PRESS, 9); page.wait_for_timeout(120); page.evaluate(RELEASE, 9); page.wait_for_timeout(120)
        expect(page.locator("#paused")).to_be_visible()
        page.evaluate(PRESS, 0); page.wait_for_timeout(120); page.evaluate(RELEASE, 0); page.wait_for_timeout(150)
        page.wait_for_function("g.mode === 'playing'")
    after = page.evaluate(RAF_RATE)
    result["gamepad_pause_resume_frames_per_second"] = {"before": baseline, "after": after}
    assert after < baseline * 1.4, "pause and resume from the pad must not add render loops"

    # Gamepad on the upgrade screen: the stick moves focus, A installs the focused card.
    page.evaluate(REVIEW_CLEAR)
    expect(page.locator("#upgrade-screen")).to_be_visible(timeout=5000)
    page.evaluate("() => { window.__pad.axes[0] = 1; }"); page.wait_for_timeout(150)
    page.evaluate("() => { window.__pad.axes[0] = 0; }"); page.wait_for_timeout(150)
    page.evaluate("() => { window.__pad.axes[0] = 1; }"); page.wait_for_timeout(150)
    page.evaluate("() => { window.__pad.axes[0] = 0; }"); page.wait_for_timeout(150)
    focused = page.evaluate("document.activeElement.dataset.choice")
    expected = page.locator(".upgrade-card").nth(2).get_attribute("data-choice")
    assert focused == expected, (focused, expected)
    page.evaluate(PRESS, 0); page.wait_for_timeout(150); page.evaluate(RELEASE, 0)
    page.wait_for_function("g.wave === 2 && g.mode === 'playing'")
    assert page.evaluate("g.upgrades[" + json.dumps(expected) + "]") == 1
    result["gamepad_upgrade_choice"] = "stick moved to the third card and A installed it"
    page.evaluate(REVIEW_CALM)

    # A trigger held through the sector-clear transition must not choose an upgrade.
    page.keyboard.down("Space"); page.wait_for_timeout(150)
    page.evaluate(REVIEW_CLEAR)
    expect(page.locator("#upgrade-screen")).to_be_visible(timeout=5000)
    for _ in range(6):
        page.keyboard.down("Space"); page.wait_for_timeout(40)
    page.keyboard.up("Space"); page.wait_for_timeout(300)
    assert page.evaluate("document.body.dataset.state") == "upgrade" and page.evaluate("g.wave") == 2
    result["held_space_through_clear"] = "upgrade screen waits for a real choice"
    page.locator(".upgrade-card").first.click(); page.wait_for_function("g.mode === 'playing'")
    page.evaluate(REVIEW_CALM)

    # A tap that presses and releases inside one frame still fires.
    page.wait_for_timeout(900)
    page.evaluate("""() => { const o = { code: 'Space', key: ' ', bubbles: true };
      window.dispatchEvent(new KeyboardEvent('keydown', o)); window.dispatchEvent(new KeyboardEvent('keyup', o)); }""")
    page.wait_for_timeout(120)
    result["sub_frame_tap_shots"] = page.evaluate("g.shots.filter(s => s.owner === 'player').length")
    assert result["sub_frame_tap_shots"] >= 2

    # Releasing a held trigger must not spend its old buffered tap on another volley.
    released = page.evaluate("""async () => {
      const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
      const key = { code: 'Space', key: ' ', bubbles: true };
      window.dispatchEvent(new KeyboardEvent('keydown', key));
      await nextFrame(); await nextFrame();
      const held = g.player.trigger;
      window.dispatchEvent(new KeyboardEvent('keyup', key));
      g.player.shotTimer = 0; // Exercise release exactly when the cannons are ready.
      const fire = g.fire;
      let volleys = 0;
      g.fire = function() { volleys++; return fire.call(this); };
      await nextFrame(); await nextFrame();
      g.fire = fire;
      return { held, volleys, trigger: g.player.trigger };
    }""")
    assert released["held"] and released["volleys"] == 0 and not released["trigger"], released
    result["trigger_release"] = "release stops firing without replaying the buffered press"

    # Leaving fullscreen without the game asking (the browser's Escape) pauses the run.
    page.keyboard.press("KeyF"); page.wait_for_function("!!document.fullscreenElement")
    page.evaluate("document.exitFullscreen()"); page.wait_for_function("!document.fullscreenElement")
    expect(page.locator("#paused")).to_be_visible()
    page.locator("#resume").click(); page.wait_for_function("g.mode === 'playing'")
    page.keyboard.press("KeyF"); page.wait_for_function("!!document.fullscreenElement")
    page.keyboard.press("KeyF"); page.wait_for_function("!document.fullscreenElement")
    page.wait_for_timeout(200)
    assert page.evaluate("g.mode") == "playing", "the F key exits fullscreen without pausing"
    result["fullscreen_escape_pauses"] = "browser exit pauses; F key exit keeps flying"

    # Earlier edge cases: pause during the clear delay, Escape during the death delay, resize while paused.
    page.evaluate(REVIEW_CLEAR); page.keyboard.press("KeyP"); page.keyboard.press("Escape")
    expect(page.locator("#upgrade-screen")).to_be_visible(timeout=5000)
    page.locator(".upgrade-card").first.click(); page.wait_for_function("g.mode === 'playing'")
    page.evaluate("() => { g.player.invulnerable = 0; g.damagePlayer(9999); }")
    page.keyboard.press("Escape")
    expect(page.locator("#gameover")).to_be_visible(timeout=5000)
    page.locator("#retry").click(); page.wait_for_function("g.mode === 'playing'")
    page.evaluate(REVIEW_CALM)
    page.keyboard.press("KeyP"); expect(page.locator("#paused")).to_be_visible()
    page.set_viewport_size({"width": 1000, "height": 700}); page.wait_for_timeout(400)
    lit = page.evaluate("""() => { const c = document.querySelector('#field'), d = c.getContext('2d').getImageData(c.width / 2 - 70, c.height / 2 - 70, 140, 140).data;
      let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 90) n++; return n; }""")
    assert lit > 200, "the paused frame is redrawn after a resize"
    result["resize_while_paused_lit_pixels"] = lit
    page.locator("#resume").click(); page.wait_for_function("g.mode === 'playing'")

    # Two tabs: a purchase in one is kept when the other banks salvage.
    page.keyboard.press("KeyP"); page.locator("#return-hangar").click(); expect(page.locator("#hangar")).to_be_visible()
    other = context.new_page()
    other.goto(base + "/arcade/", wait_until="networkidle")
    other.evaluate("() => { const p = JSON.parse(localStorage.getItem('data-void-patrol-v1:solo')); p.salvage += 5000; localStorage.setItem('data-void-patrol-v1:solo', JSON.stringify(p)); }")
    page.wait_for_timeout(300)
    wallet_seen = page.locator("#wallet").inner_text()
    page.locator('[data-upgrade="reactor"]').click()
    other.evaluate(REVIEW_HOOK); other.locator("#launch").click(); other.wait_for_timeout(300)
    other.evaluate("() => { g.salvage += 40; }"); other.keyboard.press("KeyP")
    saved = page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:solo'))")
    assert saved["hangar"]["reactor"] >= 1, "the purchase from the first tab survived the second tab's bank"
    result["two_tabs"] = {"wallet_after_other_tab_update": wallet_seen, "reactor_level": saved["hangar"]["reactor"], "salvage": saved["salvage"]}
    other.close()

    result["errors"] = errors
    assert not errors, errors
    return result


def run(base, artifacts):
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors, dashboard_errors = [], [], []
    for name, mime in ASSETS:
        with urlopen(base + "/arcade/" + name) as response:
            assert response.status == 200 and mime in response.headers["Content-Type"], name
            assert hashlib.sha256(response.read()).digest() == hashlib.sha256((ROOT / "arcade" / name).read_bytes()).digest(), name
    with urlopen(base + "/arcade") as response:
        assert response.url.endswith("/arcade/")
    checks.append("production routes, MIME types, exact content for code and artwork, and trailing-slash redirect")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/arcade/", wait_until="networkidle")
        expect(page.locator("#launch")).to_be_enabled()
        page.wait_for_function("document.querySelector('#field').width > 1000")
        page.wait_for_timeout(400)
        page.screenshot(path=str(artifacts / "void-patrol-title.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#open-hangar").click(); expect(page.locator("#hangar")).to_be_visible()
        assert page.locator(".hangar-item").count() == 4
        page.screenshot(path=str(artifacts / "void-patrol-hangar.png"))
        page.locator("#hangar-back").click(); expect(page.locator("#menu")).to_be_visible()
        checks.append("title screen, threat briefing, hangar screen, and desktop layout")

        page.evaluate(CAPTURE_GAME)
        page.locator("#launch").click()
        page.wait_for_function("fixtureGame.mode === 'playing' && fixtureGame.time > .15")
        page.evaluate(CALM)
        start = page.evaluate("({x: fixtureGame.player.x, y: fixtureGame.player.y})")
        page.wait_for_timeout(500)
        moved = page.evaluate(f"Math.hypot(fixtureGame.player.x - {start['x']}, fixtureGame.player.y - {start['y']})")
        assert moved > 40, "the ship keeps flying with no input"
        initial = page.evaluate("fixtureGame.player.x")
        page.keyboard.down("KeyD"); page.wait_for_timeout(600); page.keyboard.up("KeyD")
        assert page.evaluate("fixtureGame.player.x") > initial + 40
        assert page.evaluate("Math.abs(fixtureGame.player.angle)") < .3
        checks.append("constant forward flight and keyboard steering")

        box = page.locator("#field").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2 + 300, box["y"] + box["height"] / 2 + 110)
        page.wait_for_function("Math.abs(fixtureGame.player.angle - Math.atan2(110, 300)) < .035", timeout=3000)
        assert page.evaluate(PLAYER_SHOTS) == 0, "moving the mouse steers without firing"
        page.keyboard.down("KeyW")
        page.wait_for_function("Math.abs(fixtureGame.player.angle + Math.PI / 2) < .015", timeout=3000)
        page.keyboard.up("KeyW"); page.wait_for_timeout(180)
        assert page.evaluate("Math.abs(fixtureGame.player.angle + Math.PI / 2)") < .015, "the old mouse target cannot take control after keyboard release"
        page.mouse.move(box["x"] + box["width"] / 2 + 300, box["y"] + box["height"] / 2 + 125)
        page.wait_for_function("Math.abs(fixtureGame.player.angle - Math.atan2(125, 300)) < .035", timeout=3000)
        page.mouse.move(15, 15)
        heading = page.evaluate("fixtureGame.player.angle"); page.wait_for_timeout(180)
        assert abs(page.evaluate("fixtureGame.player.angle") - heading) < .001, "leaving the playfield keeps the last heading"
        checks.append("360-degree mouse steering, keyboard takeover, mouse reactivation, and coast on pointer exit")

        page.wait_for_timeout(900)
        assert page.evaluate(PLAYER_SHOTS) == 0, "no trigger, no fire"
        page.keyboard.down("Space"); page.wait_for_timeout(300)
        assert page.evaluate(PLAYER_SHOTS) >= 2
        page.keyboard.up("Space"); page.wait_for_timeout(950)
        assert page.evaluate(PLAYER_SHOTS) == 0, "releasing the trigger stops fire"
        box = page.locator("#field").bounding_box()
        page.mouse.move(box["x"] + box["width"] * .5, box["y"] + box["height"] * .6)
        page.mouse.down(); page.wait_for_timeout(300)
        assert page.evaluate(PLAYER_SHOTS) >= 2, "holding the mouse button fires"
        page.mouse.up(); page.wait_for_timeout(950)
        assert page.evaluate(PLAYER_SHOTS) == 0
        checks.append("trigger fire on Space and mouse, with no constant fire")

        page.keyboard.press("KeyP"); expect(page.locator("#paused")).to_be_visible()
        time = page.evaluate("fixtureGame.time"); page.wait_for_timeout(140)
        assert page.evaluate("fixtureGame.time") == time
        page.locator("#resume").click(); page.keyboard.press("KeyE")
        page.wait_for_function("fixtureGame.player.pulses === 1")
        page.locator("#sound").click(); expect(page.locator("#sound")).to_have_attribute("aria-pressed", "false")
        page.locator("#sound").click(); expect(page.locator("#sound")).to_have_attribute("aria-pressed", "true")
        page.keyboard.press("KeyF"); page.wait_for_function("!!document.fullscreenElement")
        expect(page.locator("#fullscreen")).to_have_attribute("aria-pressed", "true")
        page.wait_for_function("document.querySelector('#field').getBoundingClientRect().height > 800")
        full_box = page.locator("#field").bounding_box()
        page.mouse.move(full_box["x"] + full_box["width"] / 2 - 260, full_box["y"] + full_box["height"] / 2 + 135)
        page.wait_for_function("Math.abs(fixtureGame.player.angle - Math.atan2(135, -260)) < .04", timeout=3000)
        page.keyboard.press("KeyP"); expect(page.locator("#paused")).to_be_visible()
        heading = page.evaluate("fixtureGame.player.angle")
        page.keyboard.press("KeyP"); page.wait_for_timeout(180)
        assert abs(page.evaluate("fixtureGame.player.angle") - heading) < .001, "resuming clears the old mouse steering target"
        page.keyboard.press("KeyF"); page.wait_for_function("!document.fullscreenElement")
        expect(page.locator("#fullscreen")).to_have_attribute("aria-pressed", "false")
        checks.append("pause/resume, pulse, sound controls, fullscreen on the F key, and mouse steering after fullscreen resize")

        page.evaluate("() => { const g = fixtureGame, b = g.bases[0]; g.player.x = b.x - 430; g.player.y = b.y + 240; g.player.angle = -.5; g.hitBase(b, b.pods[0], 99); }")
        page.wait_for_timeout(500)
        page.screenshot(path=str(artifacts / "void-patrol-flight.png"))
        page.evaluate(CLEAR_BASES)
        expect(page.locator("#upgrade-screen")).to_be_visible()
        assert page.locator(".upgrade-card").count() == 3
        page.screenshot(path=str(artifacts / "void-patrol-upgrades.png"))
        choice = page.locator(".upgrade-card").first.get_attribute("data-choice")
        page.locator(".upgrade-card").first.click()
        page.wait_for_function("fixtureGame.wave === 2 && fixtureGame.mode === 'playing'")
        assert page.evaluate("fixtureGame.upgrades[" + json.dumps(choice) + "]") == 1
        page.keyboard.press("KeyP"); page.locator("#return-hangar").click()
        expect(page.locator("#hangar")).to_be_visible()
        page.locator('[data-upgrade="armor"]').click()
        saved = page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:solo'))")
        assert saved["hangar"]["armor"] == 1 and saved["best"] > 4000 and saved["salvage"] >= 0
        assert "autofire" not in saved
        page.locator("#hangar-launch").click()
        page.wait_for_function("fixtureGame.mode === 'playing'")
        assert page.evaluate("fixtureGame.maxHull") == 115
        checks.append("station destruction, sector upgrade, next sector, banking, and permanent hangar upgrade")

        page.evaluate("() => { fixtureGame.player.invulnerable = 0; fixtureGame.damagePlayer(9999); }")
        expect(page.locator("#gameover")).to_be_visible()
        page.locator("#retry").click(); page.wait_for_function("fixtureGame.mode === 'playing'")
        assert page.evaluate("fixtureGame.maxHull") == 115
        page.reload(wait_until="networkidle")
        assert page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:solo')).hangar.armor") == 1
        expect(page.locator("#menu")).to_be_visible()
        checks.append("game over, retry, reload persistence, and new run reset")

        for width in [390, 320]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            expect(page.locator("#launch")).to_be_visible()
            page.locator("#open-hangar").click(); expect(page.locator("#hangar")).to_be_visible()
            for card in page.locator(".hangar-item").all():
                card.scroll_into_view_if_needed()
                bounds = card.bounding_box(); assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            page.locator("#hangar-back").click(); expect(page.locator("#menu")).to_be_visible()
        page.evaluate(CAPTURE_GAME); page.locator("#launch").click()
        expect(page.locator("#joystick")).to_be_visible()
        page.evaluate(CALM)
        stick = page.locator("#joystick").bounding_box()
        x = stick["x"] + stick["width"] / 2; y = stick["y"] + stick["height"] / 2
        old_x = page.evaluate("fixtureGame.player.x")
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 36, y); page.wait_for_timeout(500); page.mouse.up()
        assert page.evaluate("fixtureGame.player.x") > old_x + 30
        page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 30, y + 7)
        page.wait_for_function("Math.abs(fixtureGame.player.angle - Math.atan2(7, 30)) < .025", timeout=3000)
        page.mouse.up()
        heading = page.evaluate("fixtureGame.player.angle"); page.wait_for_timeout(180)
        assert abs(page.evaluate("fixtureGame.player.angle") - heading) < .001, "releasing the touch stick keeps its precise heading"
        fire = page.locator("#touch-fire").bounding_box()
        page.mouse.move(fire["x"] + fire["width"] / 2, fire["y"] + fire["height"] / 2)
        page.wait_for_timeout(900)
        assert page.evaluate(PLAYER_SHOTS) == 0
        page.mouse.down(); page.wait_for_timeout(300)
        assert page.evaluate(PLAYER_SHOTS) >= 2, "the touch fire button fires while held"
        page.mouse.up()
        page.locator("#touch-pulse").click(); page.wait_for_function("fixtureGame.player.pulses === 1")
        page.screenshot(path=str(artifacts / "void-patrol-mobile.png"))
        page.evaluate(CLEAR_BASES); expect(page.locator("#upgrade-screen")).to_be_visible()
        page.locator(".upgrade-card").last.scroll_into_view_if_needed(); page.locator(".upgrade-card").last.click()
        page.wait_for_function("fixtureGame.wave === 2")
        checks.append("390px and 320px layouts, touch stick, hold-to-fire button, touch pulse, and scrollable upgrade choice")

        blocked = browser.new_context(viewport={"width": 900, "height": 900})
        blocked.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
        blocked.add_init_script("Storage.prototype.getItem = () => { throw new Error('disabled'); }; Storage.prototype.setItem = () => { throw new Error('disabled'); };")
        page2 = blocked.new_page(); page2.on("pageerror", lambda error: errors.append(str(error)))
        page2.goto(base + "/arcade/", wait_until="networkidle")
        expect(page2.locator("#storage-notice")).to_be_visible()
        page2.locator("#launch").click(); expect(page2.locator("#play-hud")).to_be_visible()
        blocked.close()
        checks.append("blocked browser storage degrades to playable session-only progress")

        page.close()
        page = context.new_page()
        page.set_viewport_size({"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: dashboard_errors.append(str(error)))
        page.goto(base + "/#arcade", wait_until="domcontentloaded")
        expect(page.locator('[data-nav-panel="arcade"]')).to_be_visible()
        frame = page.frame_locator("#arcade-frame")
        expect(frame.locator("#launch")).to_be_enabled(timeout=15000)
        flight = page.frame(url=re.compile("/arcade/index.html"))
        flight.evaluate(CAPTURE_GAME); frame.locator("#launch").click()
        flight.wait_for_function("fixtureGame.mode === 'playing'")
        page.evaluate("showPanel('chat')")
        flight.wait_for_function("fixtureGame.mode === 'paused'")
        before = flight.evaluate("fixtureGame.time"); page.wait_for_timeout(150)
        assert flight.evaluate("fixtureGame.time") == before
        page.evaluate("showPanel('arcade')"); expect(frame.locator("#paused")).to_be_visible()
        frame.locator("#resume").click(); flight.wait_for_function("fixtureGame.mode === 'playing'")
        assert flight.evaluate("fixtureGame.maxHull") == 100
        frame.locator("#fullscreen").click(); flight.wait_for_function("!!document.fullscreenElement")
        frame.locator("#fullscreen").click(); flight.wait_for_function("!document.fullscreenElement")
        assert flight.evaluate("fixtureGame.mode") == "playing", "leaving fullscreen with the game's own control keeps flying"
        checks.append("dashboard navigation, iframe lifecycle, automatic pause, fullscreen from the embedded game, and a separate save namespace")
        flight.evaluate(CLEAR_BASES); expect(frame.locator("#upgrade-screen")).to_be_visible()
        assert page.evaluate("JSON.parse(localStorage.getItem('data-void-patrol-v1:arcade-preview')).salvage") >= 300
        page.evaluate("_activeCaptain = 'arcade-preview-b'; ArcadeHost.reset();")
        expect(frame.locator("#launch")).to_be_enabled(timeout=10000)
        expect(frame.locator("#wallet")).to_have_text("0")
        expect(frame.locator("#best")).to_have_text("000000")
        checks.append("profile switch unloads the old run and keeps progress separate")
        assert not errors, errors
        arcade_errors = [e for e in dashboard_errors if re.search(r'arcade|Game|Renderer|fixtureGame', e, re.I)]
        assert not arcade_errors, arcade_errors
        checks.append("no game JavaScript errors")
        review = review_regressions(browser, base)
        checks.append("review regressions: gamepad launch, pause, resume and upgrade choice without extra render loops; held trigger across screens; sub-frame taps; immediate trigger release; fullscreen exits; resize while paused; two tabs")
        browser.close()
    result = {"result": "PASS", "checks": checks, "game_errors": errors, "dashboard_fixture_errors": dashboard_errors, "regressions": review, "artifacts": str(artifacts)}
    (artifacts / "browser-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:7817")
    parser.add_argument("--artifacts", type=Path, default=ROOT.parent / "build" / "arcade-checks")
    args = parser.parse_args()
    run(args.base.rstrip("/"), args.artifacts)
