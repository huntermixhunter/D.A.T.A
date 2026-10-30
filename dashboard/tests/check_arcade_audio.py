"""Check real local sample playback, trigger timing, and audio lifecycle in Chromium."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

from check_arcade_browser import CALM, CAPTURE_GAME

ROOT = Path(__file__).resolve().parents[1]
PROBE = """() => {
  const NativeAudioContext = window.AudioContext;
  window.audioProbe = { contexts: [], decoded: [], started: [], active: new Set() };
  window.AudioContext = class extends NativeAudioContext {
    constructor(...args) { super(...args); audioProbe.contexts.push(this); }
    decodeAudioData(...args) {
      return super.decodeAudioData(...args).then(buffer => { audioProbe.decoded.push(buffer.duration); return buffer; });
    }
    createBufferSource() { return this.watch(super.createBufferSource(), 'sample'); }
    createOscillator() { return this.watch(super.createOscillator(), 'tone'); }
    watch(node, type) {
      const start = node.start.bind(node), disconnect = node.disconnect.bind(node);
      let record;
      node.start = (...args) => {
        record = { type, duration: node.buffer?.duration, loop: node.loop,
                   rate: node.playbackRate?.value, at: this.currentTime };
        audioProbe.started.push(record); audioProbe.active.add(record);
        return start(...args);
      };
      node.disconnect = (...args) => { audioProbe.active.delete(record); return disconnect(...args); };
      node.addEventListener('ended', () => audioProbe.active.delete(record));
      return node;
    }
  };
}"""
SAMPLES = "audioProbe.started.filter(s => s.type === 'sample' && !s.loop)"


def emit(page, kind, **data):
    page.evaluate("event => fixtureGame.emit(event.type, {x: fixtureGame.player.x, y: fixtureGame.player.y, ...event})", {"type": kind, **data})


def run(base, artifacts):
    checks, errors = [], []
    sources = json.loads((ROOT / "arcade/assets/sounds/sources.json").read_text(encoding="utf-8"))
    for clip in sources["clips"]:
        with urlopen(base + "/arcade/assets/sounds/" + clip["file"], timeout=5) as response:
            assert response.status == 200 and response.headers["Content-Type"].startswith("audio/")
            assert hashlib.sha256(response.read()).hexdigest() == clip["sha256"]
    checks.append("all five local WAV files served intact with audio MIME types")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        context.set_default_timeout(7000)
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
        context.add_init_script("(" + PROBE + ")()")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/arcade/", wait_until="networkidle")
        page.evaluate(CAPTURE_GAME)
        assert page.evaluate("audioProbe.contexts.length") == 0, "audio waits for the launch gesture"
        expect(page.locator("#sound")).to_have_attribute("aria-pressed", "true")
        page.locator("#launch").click()
        page.wait_for_function("audioProbe.decoded.length === 5 && audioProbe.contexts[0].state === 'running'")
        page.wait_for_function("fixtureGame.mode === 'playing'")
        page.evaluate(CALM)
        engine_duration = next(c["duration_seconds"] for c in sources["clips"] if c["file"] == "engine-loop.wav")
        boost_duration = next(c["duration_seconds"] for c in sources["clips"] if c["file"] == "boost-loop.wav")
        pulse_duration = next(c["duration_seconds"] for c in sources["clips"] if c["file"] == "pulse.wav")
        engine = f"[...audioProbe.active].filter(s => s.loop && Math.abs(s.duration - {engine_duration}) < .003)"
        boost = f"[...audioProbe.active].filter(s => s.loop && Math.abs(s.duration - {boost_duration}) < .003)"
        page.wait_for_function(engine + ".length === 1")
        page.keyboard.down("Shift")
        page.wait_for_function(boost + ".length === 1")
        page.keyboard.up("Shift")
        page.wait_for_function(boost + ".length === 0")
        for _ in range(8):
            page.keyboard.down("Shift"); page.wait_for_timeout(30); page.keyboard.up("Shift")
        page.wait_for_function(boost + ".length === 0")
        assert page.evaluate(engine + ".length") == 1
        page.keyboard.press("KeyE")
        page.wait_for_function(f"audioProbe.started.some(s => !s.loop && Math.abs(s.duration - {pulse_duration}) < .003)")
        checks.append("sound defaults on at launch; one engine loop, hold-only boost, and the pulse sample follow gameplay")
        page.evaluate("audioProbe.started.length = 0")
        page.wait_for_timeout(180)
        assert page.evaluate(SAMPLES + ".length") == 0, "coasting must not play the gun sample"
        page.keyboard.down("Space")
        page.wait_for_timeout(480)
        page.keyboard.up("Space")
        shots = page.evaluate(SAMPLES)
        assert len(shots) >= 2 and all(abs(s["duration"] - .519) < .003 for s in shots)
        count = len(shots)
        page.wait_for_timeout(260)
        assert page.evaluate(SAMPLES + ".length") == count, "release cannot start another gun sound"
        checks.append("decoded gun sample starts with actual trigger volleys and stops retriggering on release")

        page.evaluate("audioProbe.started.length = 0")
        emit(page, "explosion", size=36)
        page.wait_for_function("audioProbe.started.some(s => Math.abs((s.duration || 0) - 1.856) < .003)")
        small = page.evaluate(SAMPLES + ".at(-1)")
        page.evaluate("fixtureGame.destroyBase(fixtureGame.bases[0], true)")
        page.wait_for_function(SAMPLES + ".length === 2")
        fortress = page.evaluate(SAMPLES + ".at(-1)")
        assert fortress["rate"] < small["rate"], "fortress blast has the deeper pitch"
        page.evaluate("() => { fixtureGame.player.invulnerable = 0; fixtureGame.damagePlayer(9999); }")
        page.wait_for_function(SAMPLES + ".length === 3")
        expect(page.locator("#gameover")).to_be_visible()
        assert page.evaluate("[...audioProbe.active].filter(s => s.loop).length") == 0, "death stops the flight loops"
        page.locator("#retry").click()
        page.wait_for_function("fixtureGame.mode === 'playing'")
        page.evaluate(CALM)
        checks.append("the explosion sample plays for destruction, with deeper fortress and player-death variants")

        page.keyboard.press("KeyP")
        page.wait_for_function("audioProbe.contexts[0].state === 'suspended'")
        assert page.evaluate("audioProbe.active.size") == 0, "pause must discard every live tail"
        count = page.evaluate(SAMPLES + ".length")
        page.locator("#resume").click()
        page.wait_for_function("audioProbe.contexts[0].state === 'running'")
        page.wait_for_timeout(150)
        assert page.evaluate(SAMPLES + ".length") == count, "resume must not replay old effects"
        assert page.evaluate(engine + ".length") == 1, "resume starts exactly one fresh engine loop"
        page.evaluate("audioProbe.started.length = 0")
        for _ in range(14):
            emit(page, "explosion", size=72)
            page.wait_for_timeout(50)
        assert page.evaluate("[...audioProbe.active].filter(s => s.type === 'sample' && !s.loop).length") <= 10
        checks.append("pause clears all sounds; resume restarts only the engine; chain explosions have bounded overlap")

        page.keyboard.press("KeyM")
        page.wait_for_function("audioProbe.contexts[0].state === 'suspended'")
        assert page.evaluate("audioProbe.active.size") == 0
        count = page.evaluate("audioProbe.started.length")
        page.keyboard.press("Space")
        page.wait_for_timeout(100)
        assert page.evaluate("audioProbe.started.length") == count
        page.reload(wait_until="networkidle")
        expect(page.locator("#sound")).to_have_attribute("aria-pressed", "false")
        checks.append("mute stops active effects immediately, suppresses new sounds, and persists across reloads")

        # Both a missing asset and corrupt bytes must retain playable fallback sounds.
        for status, body in [(404, b"missing"), (200, b"not a WAV")]:
            fallback = context.new_page()
            fallback.add_init_script("localStorage.setItem('data-void-patrol-v1:solo', JSON.stringify({sound: false}))")
            fallback.on("pageerror", lambda error: errors.append(str(error)))
            fallback.route("**/assets/sounds/*.wav*", lambda route, request, s=status, b=body: route.fulfill(status=s, content_type="audio/wav", body=b))
            fallback.goto(base + "/arcade/", wait_until="networkidle")
            fallback.evaluate(CAPTURE_GAME)
            fallback.locator("#sound").click()
            fallback.locator("#launch").click()
            fallback.wait_for_function("fixtureGame.mode === 'playing' && audioProbe.contexts[0].state === 'running'")
            fallback.evaluate(CALM)
            fallback.evaluate("audioProbe.started.length = 0")
            fallback.keyboard.down("Space")
            fallback.wait_for_function("audioProbe.started.some(s => s.type === 'tone')")
            fallback.keyboard.up("Space")
            assert fallback.evaluate("audioProbe.decoded.length") == 0
            fallback.keyboard.press("KeyP")
            fallback.close()
        checks.append("missing and corrupt audio files use synthesized fallback without interrupting play")

        # The hidden dashboard frame must release every audio source.
        embedded = context.new_page()
        embedded.on("pageerror", lambda error: errors.append(str(error)) if "arcade" in str(error).lower() else None)
        embedded.goto(base + "/#arcade", wait_until="domcontentloaded")
        frame = embedded.frame_locator("#arcade-frame")
        expect(frame.locator("#launch")).to_be_enabled(timeout=15000)
        frame.locator("#launch").click()
        flight = embedded.locator("#arcade-frame").element_handle().content_frame()
        flight.wait_for_function("audioProbe.contexts.length === 1 && audioProbe.active.size > 0")
        embedded.evaluate("showPanel('chat')")
        flight.wait_for_function("audioProbe.contexts[0].state === 'suspended' && audioProbe.active.size === 0")
        embedded.evaluate("showPanel('arcade')")
        expect(frame.locator("#paused")).to_be_visible()
        assert flight.evaluate("audioProbe.active.size") == 0
        checks.append("leaving the dashboard Arcade stops every sound and returning waits for Resume")
        browser.close()
    assert not errors, errors
    result = {"result": "PASS", "checks": checks, "errors": errors}
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "audio-results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:7817")
    parser.add_argument("--artifacts", type=Path, default=ROOT.parent / "build/arcade-checks")
    args = parser.parse_args()
    run(args.base.rstrip("/"), args.artifacts)
