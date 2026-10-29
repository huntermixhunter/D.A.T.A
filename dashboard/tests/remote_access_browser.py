"""Exercise real Settings UI with local Tailscale and Telegram fixtures.

Run: python dashboard/tests/remote_access_browser.py
No external connections, real bot tokens, or provider requests are used.
"""
import base64
import json
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import PropertyMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
import remote_access as ra
import telegram_bot as tg
from remote_access_preview import make_handler
from test_remote_access import FakeTailscale, TOKEN


def check_remote_transport(page, origin, image_requests):
    def echo_headers(route):
        route.fulfill(status=200, content_type="application/json",
                      headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps(route.request.headers))
    page.route("**/auth-boundary-check", echo_headers)
    results = page.evaluate("""async () => {
        const results = [];
        for (const origin of [location.origin, 'https://outside.test']) {
            const url = origin + '/auth-boundary-check';
            for (const input of [url, new URL(url), new Request(url)]) {
                results.push({ origin, headers: await (await fetch(input)).json() });
            }
        }
        return results;
    }""")
    for result in results:
        if result["origin"] == origin:
            assert result["headers"]["x-data-token"] == "browser-access-key"
            assert result["headers"]["x-data-request"] == "1"
        else:
            assert "x-data-token" not in result["headers"], result
            assert "x-data-request" not in result["headers"], result
    page.unroute("**/auth-boundary-check")

    # The fixture really rejects normal img requests without the access header.
    assert page.evaluate("""() => new Promise(resolve => {
        const img = new Image();
        img.onload = () => resolve(false);
        img.onerror = () => resolve(true);
        img.src = '/file?path=/unauthenticated.png';
    })""")
    assert image_requests[-1]["token"] is None
    page.evaluate("""() => {
        window.__previewUrls = { created: [], revoked: [] };
        const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = blob => {
            const url = create(blob);
            __previewUrls.created.push(url);
            return url;
        };
        URL.revokeObjectURL = url => { __previewUrls.revoked.push(url); revoke(url); };
        const host = document.createElement('div');
        host.id = 'remote-image-test';
        host.innerHTML = renderMarkdown('![Local "preview"](/protected.png)');
        host.querySelector('img').loading = 'eager';
        document.body.appendChild(host);
    }""")
    page.wait_for_function("""() => {
        const img = document.querySelector('#remote-image-test img');
        return img && img.complete && img.naturalWidth === 1;
    }""")
    image = page.locator("#remote-image-test img")
    expect(image).to_have_attribute("alt", 'Local "preview"')
    image_url = image.get_attribute("src")
    assert image_url.startswith("blob:")
    assert image_requests[-1]["token"] == "browser-access-key"
    assert all("access=" not in request["url"] and "browser-access-key" not in request["url"]
               for request in image_requests)
    page.locator("#remote-image-test").evaluate("node => node.remove()")
    page.wait_for_function("url => __previewUrls.revoked.includes(url)", arg=image_url)

    # An already-resolving response must not recreate a discarded preview.
    stale = page.evaluate("""async () => {
        const fetchBefore = window.fetch;
        let release, signal, markStarted;
        const started = new Promise(resolve => { markStarted = resolve; });
        const before = __previewUrls.created.length;
        window.fetch = (input, init) => {
            if (new URL(input, location.href).searchParams.get('path') === '/stale.png') {
                signal = init.signal;
                markStarted();
                return new Promise(resolve => {
                    release = () => resolve(new Response(new Blob(['stale'])));
                });
            }
            return fetchBefore(input, init);
        };
        try {
            const host = document.createElement('div');
            host.innerHTML = renderMarkdown('![Stale](/stale.png)');
            const img = host.querySelector('img');
            document.body.appendChild(host);
            await started;
            host.remove();
            await Promise.resolve();
            release();
            await new Promise(resolve => setTimeout(resolve, 0));
            return { aborted: signal.aborted, src: img.getAttribute('src'),
                     created: __previewUrls.created.length - before };
        } finally {
            window.fetch = fetchBefore;
        }
    }""")
    assert stale == {"aborted": True, "src": None, "created": 0}, stale
    assert page.evaluate("""() => {
        const host = document.createElement('div');
        host.innerHTML = renderMarkdown('![Unsafe](https://outside.test/a.png"onload="alert(1))');
        return !host.querySelector('img').hasAttribute('onload');
    }""")


def main():
    shots = Path(__file__).resolve().parents[2] / "screenshots"
    shots.mkdir(exist_ok=True)
    calls = []

    def telegram(_token, method, data=None):
        calls.append(method)
        if method == "getMe":
            return {"is_bot": True, "username": "data_test_bot"}
        if method == "getWebhookInfo":
            return {"url": ""}
        if method == "sendMessage":
            return {"message_id": 1}
        raise AssertionError(method)

    with tempfile.TemporaryDirectory() as temp, patch.object(ra, "_tailscale_binary", return_value="tailscale"), patch.object(tg, "api", side_effect=telegram), patch.object(tg.TelegramBot, "start"), patch.object(tg.TelegramBot, "running", new_callable=PropertyMock, return_value=True):
        manager = ra.RemoteAccess(temp, profile=lambda: "browser-test")
        fake = FakeTailscale()
        manager._run = fake.run
        image_requests = []
        class ProtectedImageHandler(make_handler(manager)):
            def do_GET(self):
                if urlsplit(self.path).path != "/file":
                    return super().do_GET()
                token = self.headers.get("X-Data-Token")
                image_requests.append({"url": self.path, "token": token})
                authorized = token == "browser-access-key"
                png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRZkAAAAASUVORK5CYII="
                body = base64.b64decode(png) if authorized else b'{"error":"auth_required"}'
                self.send_response(200 if authorized else 401)
                self.send_header("Content-Type", "image/png" if authorized else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProtectedImageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://localhost:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
                context.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in ("localhost", "127.0.0.1") else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.set_default_timeout(6000)
                page.goto(origin + "/#remote-access", wait_until="domcontentloaded")
                expect(page.locator("#remote-title")).to_be_visible()
                expect(page.locator("#remote-feedback")).to_contain_text("Choose an option")
                expect(page.locator("#remote-mobile-enable")).to_be_enabled()
                assert not manager.path.exists(), "Opening Settings must not activate services"
                page.screenshot(path=str(shots / "remote-access-desktop.png"))

                page.locator("#remote-mobile-enable").click()
                expect(page.locator("#remote-mobile-state")).to_have_text("Connected")
                expect(page.locator("#remote-keep-running")).to_be_checked()
                page.locator("#remote-phone-copy").click()
                expect(page.locator("#remote-phone-link")).to_have_value(__import__("re").compile(r"https://.*#access=.+"))
                copied = page.evaluate("navigator.clipboard.readText()")
                assert copied == manager.phone_link()["link"]
                assert "access=" not in page.url
                page.locator("#remote-mobile-disable").click()
                expect(page.locator("#remote-mobile-state")).to_have_text("Not connected")
                expect(page.locator("#remote-phone-link")).to_have_value("")

                fake.state = "NeedsLogin"
                page.locator("#remote-refresh").click()
                expect(page.locator("#remote-mobile-message")).to_contain_text("sign in")
                expect(page.locator("#remote-mobile-enable")).to_be_disabled()
                fake.state = "Running"
                page.locator("#remote-refresh").click()
                expect(page.locator("#remote-mobile-enable")).to_be_enabled()

                page.locator("#remote-choose-telegram").click()
                expect(page.locator("#remote-choose-telegram")).to_have_attribute("aria-pressed", "true")
                page.locator("#remote-bot-token").fill("bad-token")
                page.locator("#remote-telegram-connect").click()
                expect(page.locator("#remote-feedback")).to_contain_text("complete bot token")
                assert calls == []
                page.locator("#remote-bot-token").fill(TOKEN)
                page.locator("#remote-telegram-connect").click()
                expect(page.locator("#remote-pair-open")).to_be_visible()
                expect(page.locator("#remote-bot-token")).to_have_value("")
                assert TOKEN not in page.evaluate("JSON.stringify(localStorage)")
                first_pair = page.locator("#remote-pair-open").get_attribute("href")
                page.locator("#remote-pair-new").click()
                expect(page.locator("#remote-pair-open")).not_to_have_attribute("href", first_pair)
                pair_link = page.locator("#remote-pair-open").get_attribute("href")
                code = parse_qs(urlsplit(pair_link).query)["start"][0]
                bot = manager.bot
                bot.process_update(bot._config(), {"update_id": 1, "message": {"date": time.time(),
                    "chat": {"id": 123, "type": "private"}, "from": {"id": 123, "first_name": "<img src=x onerror=alert(1)>"},
                    "text": "/start " + code}})
                page.locator("#remote-refresh").click()
                expect(page.locator("#remote-telegram-state")).to_have_text("Connected")
                expect(page.locator("#remote-pair-status")).to_contain_text("<img src=x")
                assert page.locator("#remote-pair-status img").count() == 0
                expect(page.locator("#remote-pair-open")).to_be_hidden()
                manager.profile = lambda: "another-profile"
                page.locator("#remote-refresh").click()
                expect(page.locator("#remote-telegram-state")).to_have_text("Paused")
                expect(page.locator("#remote-telegram-test")).to_be_hidden()
                manager.profile = lambda: "browser-test"
                page.locator("#remote-refresh").click()
                expect(page.locator("#remote-telegram-state")).to_have_text("Connected")
                page.locator("#remote-telegram-test").click()
                expect(page.locator("#remote-feedback")).to_contain_text("Test message sent")
                assert calls.count("sendMessage") == 2
                page.locator("#remote-telegram-disconnect").click()
                expect(page.locator("#remote-telegram-state")).to_have_text("Not connected")
                expect(page.locator("#remote-telegram-form")).to_be_visible()
                expect(page.locator("#remote-pair-open")).to_be_hidden()

                for theme in ("minimal", "light", "cyberpunk"):
                    page.evaluate("theme => document.body.className = 'theme-' + theme", theme)
                    page.locator(".settings-content").evaluate("node => node.scrollTop = 0")
                    page.screenshot(path=str(shots / f"remote-access-telegram-{theme}.png"))
                page.evaluate("document.body.className = 'theme-minimal'")
                page.set_viewport_size({"width": 390, "height": 844})
                page.locator("#remote-choose-mobile").click()
                page.locator(".settings-content").evaluate("node => node.scrollTop = 0")
                page.screenshot(path=str(shots / "remote-access-mobile.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator(".settings-content").evaluate("node => node.scrollWidth <= node.clientWidth"), "Settings overflows on phones"
                for selector in ("#remote-mobile-enable", "#remote-choose-mobile", "#remote-choose-telegram", "#remote-refresh"):
                    assert page.locator(selector).bounding_box()["height"] >= 44
                page.locator("#remote-keep-running").uncheck()
                expect(page.locator("#remote-feedback")).to_contain_text("preference saved")
                assert not manager.keep_running
                page.route("**/remote-access/keep-running", lambda route: route.fulfill(
                    status=503, content_type="application/json", body=json.dumps({"error": "Could not save preference."})))
                page.locator("#remote-keep-running").click()
                expect(page.locator("#remote-feedback")).to_contain_text("Could not save preference")
                expect(page.locator("#remote-keep-running")).not_to_be_checked()
                assert not manager.keep_running
                page.unroute("**/remote-access/keep-running")

                # A phone fragment is imported before any API request and removed from the address bar.
                phone = context.new_page()
                phone.on("pageerror", lambda error: errors.append(str(error)))
                phone.goto(origin + "/#access=browser-access-key", wait_until="domcontentloaded")
                phone.wait_for_function("localStorage.getItem('data-bridge-token') === 'browser-access-key' && !location.hash")
                assert phone.url == origin + "/"
                with phone.expect_request("**/tunnel_url") as request:
                    phone.evaluate("fetch('/tunnel_url')")
                assert request.value.headers["x-data-token"] == "browser-access-key"
                assert "access=" not in request.value.url
                check_remote_transport(phone, origin, image_requests)
                assert not errors, errors
                browser.close()
                print("PASS: desktop/mobile, three themes, activation/revocation, clipboard, sign-in guidance, bot validation/pairing/disconnect, profile pause, secret handling, escaping, lifecycle preference and save failure recovery, authenticated image previews and cleanup, stale image responses, string/URL/Request auth boundaries")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
