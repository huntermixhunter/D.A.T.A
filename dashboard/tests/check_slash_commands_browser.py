"""Browser check for the dashboard slash commands (slash_commands.js).

Loads the real index.html / app.js / slash_commands.js from disk in headless
Chromium. Every request to the bridge origin is answered by a stub, so nothing
reaches a model and nothing runs on the machine. The stub records /chat_stream,
/provider, /effort and /console/run calls so the checks can prove a command was
handled by the dashboard and NOT sent to the AI.

Works against both dashboards:
  python tests/check_slash_commands_browser.py                 (this folder)
  python tests/check_slash_commands_browser.py <dashboard dir> (e.g. LCARS)
"""
import json
import mimetypes
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prompt_shortcuts as ps  # noqa: E402  (the real store, against a temp folder)
SC_DIR = Path(tempfile.mkdtemp(prefix='sc_check_'))
ORIGIN = 'http://localhost:7777'
STATIC_EXT = {'.html', '.js', '.css', '.png', '.jpg', '.webp', '.svg', '.mp3', '.ico', '.woff', '.woff2', '.mjs'}
GOOD_DIRS = {'c:\\fixture\\proj', 'c:\\fixture\\saisaipro'}

LEVELS = [{'id': '', 'short': 'AUTO'}, {'id': 'low', 'short': 'LOW'}, {'id': 'medium', 'short': 'MEDIUM'},
          {'id': 'high', 'short': 'HIGH'}, {'id': 'xhigh', 'short': 'XHIGH'}, {'id': 'max', 'short': 'MAX'}]
PROVIDERS = [
    {'id': 'claude-cli', 'label': 'Claude Opus (latest)', 'available': True, 'authenticated': True,
     'effort_levels': LEVELS, 'supports_effort': True},
    {'id': 'claude-cli-opus-48', 'label': 'Claude Opus 4.8', 'available': True, 'authenticated': True,
     'effort_levels': LEVELS, 'supports_effort': True},
    {'id': 'claude-cli-sonnet', 'label': 'Claude Sonnet', 'available': True, 'authenticated': True,
     'effort_levels': LEVELS, 'supports_effort': True},
    {'id': 'codex', 'label': 'Codex', 'available': True, 'authenticated': True,
     'effort_levels': LEVELS[:4], 'supports_effort': True},
    {'id': 'ollama', 'label': 'Ollama (local)', 'available': True, 'authenticated': True,
     'effort_levels': [], 'supports_effort': False},
]

calls = {'chat': [], 'provider': [], 'effort': [], 'console': [], 'shortcuts_unmarked': 0}
state = {'active': 'claude-cli', 'effort': ''}
failures = []


def check(cond, label):
    print(('PASS ' if cond else 'FAIL ') + label)
    if not cond:
        failures.append(label)


def handle(route):
    req = route.request
    url = urlsplit(req.url)
    if url.netloc != 'localhost:7777':
        if 'd3js.org' in url.netloc or 'fonts.g' in url.netloc:
            return route.continue_()
        return route.abort()
    path = url.path
    body = {}
    if req.method == 'POST':
        try:
            body = json.loads(req.post_data or '{}')
        except ValueError:
            body = {}
    if path in ('/chat_stream', '/chat'):
        calls['chat'].append(body.get('message', ''))
        return route.fulfill(status=200, headers={'Content-Type': 'text/event-stream'},
                             body='event: token\ndata: {"text": "ok"}\n\nevent: done\ndata: {}\n\n')
    if path == '/providers':
        return route.fulfill(status=200, json={'providers': PROVIDERS, 'active': state['active'],
                                               'effort_levels': LEVELS, 'effort': state['effort']})
    if path == '/provider' and req.method == 'POST':
        calls['provider'].append(body.get('provider'))
        state['active'] = body.get('provider')
        return route.fulfill(status=200, json={'active': state['active']})
    if path == '/effort' and req.method == 'POST':
        calls['effort'].append(body.get('effort'))
        state['effort'] = body.get('effort') or ''
        return route.fulfill(status=200, json={'active': state['effort']})
    if path == '/prompt-shortcuts':
        if req.headers.get('x-data-request') != '1':
            calls['shortcuts_unmarked'] += 1
            return route.fulfill(status=403, json={'error': 'Open shortcuts from the dashboard.'})
        if req.method == 'GET':
            return route.fulfill(status=200, json={'shortcuts': ps.load(SC_DIR)})
        try:
            act = body.get('action')
            if act == 'save':
                items = ps.save(SC_DIR, body, body.get('rename_from', ''))
            elif act == 'delete':
                items = ps.delete(SC_DIR, body.get('name', ''))
            elif act == 'reset':
                items = ps.reset(SC_DIR)
            else:
                raise ps.ShortcutError('Unknown action.')
        except ps.ShortcutError as e:
            return route.fulfill(status=e.status, json={'error': str(e)})
        return route.fulfill(status=200, json={'ok': True, 'shortcuts': items})
    if path.startswith('/console/'):
        calls['console'].append(body.get('command'))
        return route.fulfill(status=200, json={'help': 'console help'})
    if path == '/project' and req.method == 'POST':
        p = (body.get('path') or '').lower()
        if p and p not in GOOD_DIRS:
            return route.fulfill(status=400, json={'error': 'Directory not found'})
        return route.fulfill(status=200, json={'nodes': []})
    target = (ROOT / ('index.html' if path in ('/', '') else path.lstrip('/'))).resolve()
    if req.method == 'GET' and target.suffix in STATIC_EXT and target.is_file() and target.is_relative_to(ROOT):
        return route.fulfill(status=200, body=target.read_bytes(),
                             headers={'Content-Type': mimetypes.guess_type(target.name)[0] or 'application/octet-stream'})
    if path == '/ui_events':
        return route.fulfill(status=200, json={'events': []})
    if path == '/jobs/pending':
        return route.fulfill(status=200, json={'reports': []})
    return route.fulfill(status=200, json={})


def send_main(page, text):
    page.evaluate("(t) => { document.getElementById('chat-input').value = t; sendMessage(); }", text)
    page.wait_for_timeout(350)


def send_pane(page, ws_id, text):
    page.evaluate("([id, t]) => { document.getElementById('pane-input-ws' + id).value = t; sendProjectMessage(id); }",
                  [ws_id, text])
    page.wait_for_timeout(350)


def last_card(page, win_sel):
    return page.evaluate("""(sel) => {
        const cards = document.querySelectorAll(sel + ' .slash-msg');
        const c = cards[cards.length - 1];
        if (!c) return null;
        return { head: c.querySelector('.slash-head').textContent,
                 body: c.querySelector('.slash-body').textContent,
                 buttons: [...c.querySelectorAll('.slash-btn')].map(b => b.textContent),
                 current: [...c.querySelectorAll('.slash-btn.current')].map(b => b.textContent),
                 count: cards.length };
    }""", win_sel)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1600, 'height': 900})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('**/*', handle)
        page.goto(ORIGIN + '/')
        page.wait_for_function('window.DashSlash && typeof sendMessage === "function"')
        page.wait_for_timeout(1200)
        page.evaluate("document.querySelectorAll('.boot-overlay, #boot-screen, .modal-overlay').forEach(e => e.remove())")
        page.evaluate("loadProviders()")
        page.wait_for_timeout(300)

        # 1. /help in the main channel: rendered locally, never sent to the AI.
        send_main(page, '/help')
        c = last_card(page, '#chat-window')
        check(c and '/model' in c['body'] and '/effort' in c['body'] and '/new' in c['body'],
              f'/help lists the dashboard commands (got {c and c["body"][:80]!r})')
        check(not calls['chat'], '/help never reaches the AI')
        check(page.evaluate("document.getElementById('chat-input').value") == '', 'input cleared after a command')

        # 2. /model with a partial name switches the unrooted main channel globally.
        send_main(page, '/model opus 4.8')
        check(calls['provider'][-1:] == ['claude-cli-opus-48'], f'/model opus 4.8 -> POST /provider (got {calls["provider"]})')

        # 3. Ambiguous name -> picker buttons, no switch.
        n = len(calls['provider'])
        send_main(page, '/model claude')
        c = last_card(page, '#chat-window')
        check(c and len(c['buttons']) == 3 and len(calls['provider']) == n,
              f'/model claude -> 3 choices, no switch (got {c and c["buttons"]})')

        # 4. Bare /model -> list with the current one highlighted.
        send_main(page, '/model')
        c = last_card(page, '#chat-window')
        check(c and c['current'] == ['Claude Opus 4.8'], f'/model marks the current model (got {c and c["current"]})')

        # 5. /effort sets the global default when main is unrooted; aliases work.
        send_main(page, '/effort hi')
        check(calls['effort'][-1:] == ['high'], f'/effort hi -> POST /effort high (got {calls["effort"]})')
        send_main(page, '/effort auto')
        check(calls['effort'][-1:] == [''], f'/effort auto -> clears to auto (got {calls["effort"]})')
        send_main(page, '/effort banana')
        c = last_card(page, '#chat-window')
        check(c and 'MAX' in c['buttons'], f'/effort with a bad level shows the picker (got {c and c["buttons"]})')

        # 6. /new opens a separate window with the named model.
        send_main(page, '/effort xhigh')
        send_main(page, '/new C:\\fixture\\proj sonnet')
        page.wait_for_timeout(300)
        ws = page.evaluate("""() => { const e = [..._workspaces.entries()].find(([, w]) => !w.isMain);
                               return e ? { id: e[0], provider: e[1].provider, effort: e[1].effort, path: e[1].path } : null; }""")
        check(ws and ws['provider'] == 'claude-cli-sonnet', f'/new ... sonnet opens a Sonnet window (got {ws})')
        check(ws and ws['effort'] == 'xhigh', f'/new inherits this window\'s effort (got {ws})')
        ws_id = ws['id'] if ws else None
        sel = page.evaluate("(id) => document.getElementById('pane-provider-ws' + id)?.value", ws_id)
        check(sel == 'claude-cli-sonnet', f'new window\'s model dropdown matches (got {sel})')

        # 7. Commands typed in a pane act on that pane only.
        before = list(calls['provider'])
        send_pane(page, ws_id, '/model codex')
        prov = page.evaluate("(id) => _workspaces.get(id).provider", ws_id)
        check(prov == 'codex' and calls['provider'] == before, f'pane /model codex is per-window (got {prov})')
        send_pane(page, ws_id, '/effort max')
        c = last_card(page, f'#chat-win-ws{ws_id}')
        eff = page.evaluate("(id) => _workspaces.get(id).effort", ws_id)
        check(eff != 'max' and c and 'MAX' not in c['buttons'],
              f'pane /effort max refused on a model without MAX (effort={eff}, buttons={c and c["buttons"]})')
        send_pane(page, ws_id, '/effort medium')
        eff = page.evaluate("(id) => _workspaces.get(id).effort", ws_id)
        check(eff == 'medium', f'pane /effort medium sets the window (got {eff})')
        send_pane(page, ws_id, '/model ollama')
        send_pane(page, ws_id, '/effort high')
        c = last_card(page, f'#chat-win-ws{ws_id}')
        check(c and 'no reasoning effort' in c['body'], 'effort refused on a model without the control')

        # 8. /panes lists every window.
        send_pane(page, ws_id, '/panes')
        c = last_card(page, f'#chat-win-ws{ws_id}')
        check(c and 'proj' in c['body'] and 'Ollama' in c['body'], f'/panes lists windows (got {c and c["body"][:120]!r})')

        # 9. /stop works while the window is mid-reply.
        page.evaluate("(id) => { const w = _workspaces.get(id); w.isThinking = true; w.abortController = new AbortController(); }", ws_id)
        send_pane(page, ws_id, '/stop')
        busy = page.evaluate("(id) => _workspaces.get(id).isThinking", ws_id)
        check(busy is False, '/stop clears the busy window')

        # 10. /clear empties the screen.
        send_pane(page, ws_id, '/clear')
        n_msgs = page.evaluate("(id) => document.querySelectorAll('#chat-win-ws' + id + ' .chat-message').length", ws_id)
        check(n_msgs == 1, f'/clear leaves only its own note (got {n_msgs} messages)')

        # 11. A bare word that is not a model opens a sibling folder; a missing one is reported.
        send_pane(page, ws_id, '/new SaiSaiPro')
        page.wait_for_timeout(300)
        paths = page.evaluate("[..._workspaces.values()].map(w => w.path)")
        check(any(p.lower() == 'c:\\fixture\\saisaipro' for p in paths), f'/new SaiSaiPro opens the sibling folder (got {paths})')
        send_pane(page, ws_id, '/new NoSuchFolder')
        page.wait_for_timeout(300)
        c = last_card(page, f'#chat-win-ws{ws_id}')
        check(c and 'Could not open' in c['body'], 'missing folder is reported, not silently ignored')

        # 12. Things that are not commands go to the AI; unknown commands do not.
        chat_before = len(calls['chat'])
        send_main(page, '/r/stocks is up today')
        check(len(calls['chat']) == chat_before + 1, 'a path-like "/r/stocks" message goes to the AI')
        page.wait_for_timeout(400)
        send_main(page, '/bogus')
        c = last_card(page, '#chat-window')
        check(c and 'Unknown command' in c['body'] and len(calls['chat']) == chat_before + 1,
              'unknown /bogus is answered locally')

        # 13. /login is left to the in-chat console when this dashboard has one.
        has_console = page.evaluate("typeof _consoleIntercept === 'function'")
        if has_console:
            send_main(page, '/login')
            check(calls['console'][-1:] == ['/login'], f'/login goes to the console (got {calls["console"]})')
        else:
            print('SKIP /login console routing (no console in this dashboard)')

        # 14. Autocomplete: "/mo" shows /model, Tab completes it.
        page.focus('#chat-input')
        page.keyboard.type('/mo')
        vis = page.evaluate("!!document.querySelector('.slash-popup:not(.hidden) .slash-pop-item')")
        first = page.evaluate("document.querySelector('.slash-popup .slash-pop-item.active .slash-pop-name')?.textContent")
        check(vis and first == '/model', f'typing /mo pops up /model (got {first})')
        page.keyboard.press('Tab')
        val = page.evaluate("document.getElementById('chat-input').value")
        check(val == '/model ', f'Tab completes to "/model " (got {val!r})')
        hidden = page.evaluate("document.querySelector('.slash-popup').classList.contains('hidden')")
        check(hidden, 'popup closes after completing')
        page.evaluate("document.getElementById('chat-input').value = ''")

        # 15. Prompt shortcuts. The starter set is served by the real store.
        page.focus('#chat-input')
        page.keyboard.type('/bri')
        page.wait_for_timeout(300)
        pop = page.evaluate("""() => [...document.querySelectorAll('.slash-popup:not(.hidden) .slash-pop-item')]
            .map(r => ({ name: r.querySelector('.slash-pop-name').textContent, prompt: r.classList.contains('prompt') }))""")
        check(pop == [{'name': '/brief', 'prompt': True}], f'typing /bri offers the /brief prompt (got {pop})')
        page.evaluate("document.getElementById('chat-input').value = ''; document.getElementById('chat-input').blur()")
        page.wait_for_timeout(200)

        n = len(calls['chat'])
        send_main(page, '/brief')
        check(len(calls['chat']) == n + 1 and 'status brief' in calls['chat'][-1],
              f'/brief sends the saved prompt to the AI (got {calls["chat"][-1:]!r})')
        check(page.evaluate("document.getElementById('chat-input').value") == '', 'input cleared after /brief')
        page.wait_for_timeout(300)

        send_main(page, '/brief focus on the installer')
        check(calls['chat'][-1].endswith('\n\nfocus on the installer'), 'extra text is appended to a prompt without {args}')
        page.wait_for_timeout(300)

        send_main(page, '/explain app.js')
        check(calls['chat'][-1].startswith('Explain app.js in plain language'), f'{{args}} is filled in (got {calls["chat"][-1][:40]!r})')
        page.wait_for_timeout(300)

        n = len(calls['chat'])
        send_main(page, '/explain')
        c = last_card(page, '#chat-window')
        val = page.evaluate("document.getElementById('chat-input').value")
        check(len(calls['chat']) == n and c and 'needs something after it' in c['body'] and val == '/explain ',
              f'/explain with no text asks for it and waits (input={val!r})')
        page.evaluate("document.getElementById('chat-input').value = ''")

        send_main(page, '/shortcuts add standup Summarise what changed today in {folder}.')
        c = last_card(page, '#chat-window')
        check(c and 'Saved' in c['body'] and '/standup' in c['body'], f'/shortcuts add saves (got {c and c["body"]!r})')
        send_main(page, '/standup')
        check(calls['chat'][-1].startswith('Summarise what changed today in '), f'new /standup sends (got {calls["chat"][-1]!r})')
        page.wait_for_timeout(300)

        send_main(page, '/shortcuts add model hijack the model command')
        c = last_card(page, '#chat-window')
        check(c and 'built-in command' in c['body'], 'a shortcut cannot take a built-in name')

        # Editor: change /standup through the form.
        send_main(page, '/shortcuts edit standup')
        page.evaluate("""() => { const cards = document.querySelectorAll('#chat-window .slash-msg');
            const f = cards[cards.length - 1];
            f.querySelector('.sf-prompt').value = 'Edited standup prompt';
            f.querySelector('.sf-desc').value = 'End of day';
            [...f.querySelectorAll('.slash-btn')].find(b => b.textContent === 'Save changes').click(); }""")
        page.wait_for_timeout(400)
        c = last_card(page, '#chat-window')
        saved = {x['name']: x for x in ps.load(SC_DIR)}
        check(c and 'Updated' in c['body'] and saved['standup']['prompt'] == 'Edited standup prompt'
              and saved['standup']['desc'] == 'End of day', f'the editor saves changes (got {c and c["body"]!r})')

        # Typing in the editor stays in the editor (no global hotkeys, no send).
        send_main(page, '/shortcuts add')
        page.wait_for_timeout(200)
        n = len(calls['chat'])
        page.click('#chat-window .slash-msg:last-child .sf-name')
        page.keyboard.press('Control+A')
        page.keyboard.type('brief')
        page.click('#chat-window .slash-msg:last-child .sf-prompt')
        page.keyboard.press('Control+A')
        page.keyboard.type('typed in the form')
        page.keyboard.press('Enter')
        page.keyboard.press('Control+Enter')
        page.wait_for_timeout(300)
        err = page.evaluate("document.querySelector('#chat-window .slash-msg:last-child .sf-err').textContent")
        check('already exists' in err and len(calls['chat']) == n,
              f'editor: Ctrl+Enter saves, an existing name is refused, nothing is sent (got {err!r})')

        # The no-name form starts from the last message sent in this window.
        send_main(page, '/shortcuts new')
        prefill = page.evaluate("document.querySelector('#chat-window .slash-msg:last-child .sf-prompt').value")
        check(prefill.startswith('Edited standup prompt') or prefill.startswith('Summarise'),
              f'/shortcuts new offers the last sent message (got {prefill[:40]!r})')

        # List card: Delete then Undo.
        send_main(page, '/shortcuts')
        c = last_card(page, '#chat-window')
        check(c and '/standup' in c['body'] and '/brief' in c['body'], 'list shows saved shortcuts')
        page.click('#chat-window .slash-msg:last-child tr[data-name="standup"] button[data-act="delete"]')
        page.wait_for_timeout(400)
        gone = 'standup' not in [x['name'] for x in ps.load(SC_DIR)]
        page.evaluate("""() => { const cards = document.querySelectorAll('#chat-window .slash-msg');
            [...cards[cards.length - 1].querySelectorAll('.slash-btn')].find(b => b.textContent === 'Undo').click(); }""")
        page.wait_for_timeout(400)
        back = 'standup' in [x['name'] for x in ps.load(SC_DIR)]
        check(gone and back, f'Delete removes and Undo restores (gone={gone}, back={back})')

        # A busy pane keeps the prompt in its box instead of dropping it.
        n = len(calls['chat'])
        page.evaluate("(id) => { _workspaces.get(id).isThinking = true; }", ws_id)
        send_pane(page, ws_id, '/brief')
        val = page.evaluate("(id) => document.getElementById('pane-input-ws' + id).value", ws_id)
        check(len(calls['chat']) == n and 'status brief' in val, f'busy pane: prompt waits in the box (got {val[:30]!r})')
        page.evaluate("(id) => { _workspaces.get(id).isThinking = false; document.getElementById('pane-input-ws' + id).value = ''; }", ws_id)
        send_pane(page, ws_id, '/review')
        check(len(calls['chat']) == n + 1 and 'Review the uncommitted changes' in calls['chat'][-1], 'idle pane: /review sends')
        page.wait_for_timeout(300)
        check(calls['shortcuts_unmarked'] == 0, 'every shortcut request carried X-Data-Request')

        # 16. /close removes a pane; the main channel refuses.
        n_before = page.evaluate("[..._workspaces.values()].filter(w => !w.isMain).length")
        send_pane(page, ws_id, '/close')
        n_after = page.evaluate("[..._workspaces.values()].filter(w => !w.isMain).length")
        check(n_after == n_before - 1, f'/close closes the window ({n_before} -> {n_after})')
        send_main(page, '/close')
        c = last_card(page, '#chat-window')
        check(c and 'main channel stays open' in c['body'], 'main channel cannot be closed')

        check(not errors, f'no page errors (got {errors[:3]})')
        browser.close()


if __name__ == '__main__':
    run()
    print(f'\n{len(failures)} failure(s)')
    sys.exit(1 if failures else 0)
