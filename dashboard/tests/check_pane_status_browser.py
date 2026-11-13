"""Browser check for the per-window status badges (pane_status.js).

Loads the real index.html / app.js / pane_status.js from disk in headless
Chromium. Every request to the bridge origin is answered by a stub, so this
never runs a model turn, never drains a live dashboard's UI events, and never
writes conversation history. /chat_stream is faked per message:
  message containing "ASKQ"  -> reply ending with a question
  message containing "FAIL"  -> HTTP 500
  message containing "LOGIN" -> signed-out login card (needs_login event)
  anything else              -> plain finished reply

Run:  python tests/check_pane_status_browser.py
"""
import json
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'http://localhost:7777'
STATIC_EXT = {'.html', '.js', '.css', '.png', '.jpg', '.webp', '.svg', '.mp3', '.ico', '.woff', '.woff2', '.mjs'}

failures = []


def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond:
        failures.append(label)


def sse(*events):
    out = ''
    for ev, data in events:
        out += f'event: {ev}\ndata: {json.dumps(data)}\n\n'
    return out


def handle(route):
    req = route.request
    url = urlsplit(req.url)
    if url.netloc != 'localhost:7777':
        # Let d3 / fonts through; block anything else external.
        if 'd3js.org' in url.netloc or 'fonts.g' in url.netloc:
            return route.continue_()
        return route.abort()
    path = url.path
    if path == '/chat_stream':
        body = json.loads(req.post_data or '{}')
        msg = body.get('message', '')
        if 'FAIL' in msg:
            return route.fulfill(status=500, body='boom')
        if 'LOGIN' in msg:
            return route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                                 body=sse(('token', {'text': 'Please sign in.'}),
                                          ('needs_login', {'text': json.dumps({'provider': 'claude-cli'})}),
                                          ('done', {})))
        text = 'Which option do you want?' if 'ASKQ' in msg else 'All finished.'
        return route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                             body=sse(('token', {'text': text}), ('done', {})))
    target = (ROOT / ('index.html' if path in ('/', '') else path.lstrip('/'))).resolve()
    if req.method == 'GET' and target.suffix in STATIC_EXT and target.is_file() and target.is_relative_to(ROOT):
        return route.fulfill(status=200, body=target.read_bytes(),
                             headers={'Content-Type': mimetypes.guess_type(target.name)[0] or 'application/octet-stream'})
    if path == '/ui_events':
        return route.fulfill(status=200, json={'events': []})
    if path == '/jobs/pending':
        return route.fulfill(status=200, json={'reports': []})
    if path == '/project':
        return route.fulfill(status=200, json={'nodes': []})
    return route.fulfill(status=200, json={})


def badge(page, key):
    sel = '#pane-main-header .pane-status' if key == 'main' else f'#pane-{key}-header .pane-status'
    return page.evaluate("""(sel) => {
        const b = document.querySelector(sel);
        if (!b) return null;
        return { state: b.dataset.state, hidden: b.hidden, text: b.textContent, title: b.title };
    }""", sel)


def summary(page):
    return page.evaluate("document.querySelector('#pane-status-summary .pss-label')?.textContent || ''")


def settle(page, ms=400):
    page.wait_for_timeout(ms)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', handle)
        page.goto(ORIGIN + '/')
        page.wait_for_function('window.PaneStatus && document.getElementById("pane-status-summary")')
        settle(page, 1200)
        page.evaluate("document.querySelectorAll('.boot-overlay, #boot-screen, .modal-overlay').forEach(e => e.remove())")
        page.evaluate("window.focus()")

        check('CHATS QUIET' in summary(page), 'summary starts quiet')
        b = badge(page, 'main')
        check(b is not None and b['hidden'], 'main badge exists and is hidden when idle')

        # 1. Single window, reply ends with a question -> NEEDS YOU even while looking at it.
        page.evaluate("document.getElementById('chat-input').value = 'ASKQ test'; sendMessage();")
        b = badge(page, 'main')
        check(b and b['state'] == 'working', f'main shows WORKING right after send (got {b and b["state"]})')
        settle(page)
        b = badge(page, 'main')
        check(b and b['state'] == 'needs' and not b['hidden'], f'question reply -> NEEDS YOU (got {b})')
        check('question' in (b or {}).get('title', ''), 'tooltip explains why')
        check('1 NEEDS YOU' in summary(page), f'summary counts it (got {summary(page)!r})')
        check(page.title().startswith('(1) '), f'tab title prefixed (got {page.title()!r})')

        # Click the badge to dismiss.
        page.click('#pane-main-header .pane-status')
        check(badge(page, 'main')['state'] == 'idle', 'clicking badge dismisses it')

        # 2. Single window, plain reply while in view -> no badge.
        page.evaluate("document.getElementById('chat-input').value = 'plain'; sendMessage();")
        settle(page)
        check(badge(page, 'main')['state'] == 'idle', 'plain reply while looking -> stays idle')

        # 3. Second window finishes while the user is in the main window -> DONE.
        ws_id = page.evaluate("openProjectWorkspace('C:\\\\fixture\\\\proj', { forceNewPane: true })")
        settle(page)
        key = f'ws{ws_id}'
        page.click('#chat-window', position={'x': 50, 'y': 50})   # the user is looking at main
        # DATA's send first awaits the in-chat console check, so read the badge
        # one task later (the stubbed reply takes longer than that to arrive).
        st = page.evaluate(f"""async () => {{ document.getElementById('pane-input-{key}').value = 'plain';
            sendProjectMessage({ws_id});
            await new Promise(r => setTimeout(r, 0));
            return document.querySelector('#pane-{key}-header .pane-status')?.dataset.state; }}""")
        check(st == 'working', f'workspace shows WORKING right after send (got {st})')
        settle(page)
        b = badge(page, key)
        check(b and b['state'] == 'done', f'workspace finishing unseen -> DONE (got {b})')
        check('1 DONE' in summary(page), f'summary shows DONE (got {summary(page)!r})')

        # Looking at it clears DONE.
        page.click(f'#chat-win-{key}', position={'x': 50, 'y': 50})
        check(badge(page, key)['state'] == 'idle', 'touching the window clears DONE')

        # 4. Failure -> ERROR.
        page.click('#chat-window', position={'x': 50, 'y': 50})
        page.evaluate(f"document.getElementById('pane-input-{key}').value = 'FAIL'; sendProjectMessage({ws_id});")
        settle(page)
        b = badge(page, key)
        check(b and b['state'] == 'error', f'HTTP 500 -> ERROR (got {b})')

        # Summary click jumps to it and marks it seen.
        page.click('#pane-status-summary .pss-label')
        check(badge(page, key)['state'] == 'idle', 'summary click jumps to the window and clears ERROR')

        # 5. ask_options card -> NEEDS YOU, answering it -> back to WORKING.
        page.evaluate("_handleUiEvent({type:'ask_options', payload:{question:'Pick one', options:['A','B'], pane:''}})")
        b = badge(page, 'main')
        check(b and b['state'] == 'needs' and 'Pick one' in b['title'], f'ask_options -> NEEDS YOU (got {b})')
        page.click('.ask-options-card .ask-opt-btn[data-idx="0"]')
        b = badge(page, 'main')
        check(b and b['state'] == 'working', f'answering the options -> WORKING (got {b})')
        settle(page)

        # 6. ask_options arriving mid-turn wins over a plain finish.
        page.evaluate("""PaneStatus.start('main');
                         _handleUiEvent({type:'ask_options', payload:{question:'Mid', options:['X'], pane:''}});
                         PaneStatus.finish('main', {text: 'plain'});""")
        check(badge(page, 'main')['state'] == 'needs', 'ask_options during the turn -> NEEDS YOU at finish')

        # 7. A stopped turn ends idle, not ERROR.
        page.evaluate("PaneStatus.start('main'); PaneStatus.stopped('main'); PaneStatus.finish('main', {error: 'aborted'});")
        check(badge(page, 'main')['state'] == 'idle', 'stopped turn -> idle')

        # 8. A pushed message into main while looking at the workspace -> DONE.
        page.click(f'#chat-win-{key}', position={'x': 50, 'y': 50})
        page.evaluate("PaneStatus.pushed('main', 'A message arrived')")
        check(badge(page, 'main')['state'] == 'done', 'pushed message while looking elsewhere -> DONE')

        # 8b. Signed-out model in the workspace -> NEEDS YOU, even while looking at it.
        st = page.evaluate(f"""() => {{ document.getElementById('pane-input-{key}').value = 'LOGIN';
            sendProjectMessage({ws_id}); return true; }}""")
        settle(page)
        b = badge(page, key)
        check(b and b['state'] == 'needs' and 'signed out' in b['title'], f'signed-out reply -> NEEDS YOU (got {b})')
        page.click(f'#pane-{key}-header .pane-status')

        # 9. Header rewrite keeps the badge.
        page.evaluate("PaneStatus.finish('main', {text: 'Need anything?'})")
        page.evaluate("_setMainPaneProject(999, 'rewritten', 'C:\\\\fixture\\\\rewritten')")
        b = badge(page, 'main')
        check(b and b['state'] == 'needs', f'badge survives main header rewrite (got {b})')

        # 10. Closing a window drops it from the summary; a late finish does not resurrect it.
        page.evaluate(f"PaneStatus.finish('{key}', {{text: 'x?'}})")
        page.evaluate(f"closeProjectWorkspace({ws_id})")
        page.evaluate(f"PaneStatus.finish('{key}', {{error: 'late'}})")
        check(page.evaluate(f"!PaneStatus._recs.has('{key}')"), 'closed window record is gone after a late finish')
        check('ERROR' not in summary(page), f'summary drops the closed window (got {summary(page)!r})')

        # 11. Bell toggle persists.
        page.click('#pane-status-summary .pss-bell')
        check(page.evaluate("localStorage.getItem('paneStatus.alerts')") == 'off', 'bell turns alerts off')
        page.click('#pane-status-summary .pss-bell')
        check(page.evaluate("localStorage.getItem('paneStatus.alerts')") == 'on', 'bell turns alerts back on')

        ps_errors = [e for e in errors if 'PaneStatus' in e or 'pane_status' in e or 'pane-status' in e]
        check(not ps_errors, f'no page errors from pane status ({ps_errors})')
        if errors:
            print('note: other page errors (not from this feature):', errors[:5])

        page.screenshot(path=str(ROOT.parent / 'build' / 'pane_status_check.png'))
        browser.close()

    print(f'\n{"ALL PASSED" if not failures else str(len(failures)) + " FAILED"}')
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(run())
