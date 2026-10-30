"""Verify window rearrangement with local fixtures and a disposable browser profile."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PANES = '.chat-pane:not([hidden])'


def order(page):
    return page.locator('#chats-wrapper').locator(PANES).evaluate_all('(panes) => panes.map(p => p.id)')


def point(page, selector):
    box = page.locator(selector).bounding_box()
    assert box and box['width'] and box['height'], selector
    return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2


def drag_to(page, source, target, finish=True):
    page.mouse.move(*point(page, source))
    page.mouse.down()
    page.mouse.move(*point(page, target), steps=8)
    if finish:
        page.mouse.up()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://localhost:7832')
    parser.add_argument('--artifacts', type=Path, default=ROOT.parent / 'build/pane-reorder-checks')
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    errors, checks, writes = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1200}, reduced_motion='reduce')

        def route_request(route):
            request = route.request
            if request.url.startswith(args.base + '/'):
                if request.method != 'GET':
                    writes.append(urlsplit(request.url).path)
                    route.fulfill(json={'nodes': [], 'ok': True})
                else:
                    route.continue_()
            elif urlsplit(request.url).hostname in {'fonts.googleapis.com', 'fonts.gstatic.com', 'd3js.org'}:
                route.continue_()
            else:
                route.abort()

        context.route('**/*', route_request)
        page = context.new_page()
        page.set_default_timeout(8000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.base + '/', wait_until='networkidle')
        page.evaluate("showPanel('chat')")
        expect(page.locator('#pane-main .pane-move-handle')).to_be_hidden()
        page.evaluate("""async () => {
          for (const name of ['Notes', 'Research', 'Planning', 'Drafts'])
            await openProjectWorkspace('C:/Example/' + name, {forceNewPane: true});
          const pane = document.getElementById('pane-ws1');
          window.originalPane = pane;
          window.originalWorkspace = _workspaces.get(1);
          window.originalPaneKey = _paneCompositeKey(1, originalWorkspace);
          originalWorkspace.isThinking = true;
          document.getElementById('pane-input-ws1').value = 'Keep this unsent draft';
          const transcript = document.getElementById('chat-win-ws1');
          transcript.textContent = 'Retained conversation. '.repeat(400);
          transcript.scrollTo({top: 60, behavior: 'instant'});
          window.originalScroll = transcript.scrollTop;
        }""")
        assert order(page) == ['pane-main', 'pane-ws1', 'pane-ws2', 'pane-ws3', 'pane-ws4']
        expect(page.locator('.pane-move-handle:enabled')).to_have_count(5)

        # Preserve custom column sizes while moving a live conversation.
        resizer = page.locator('.chat-splitter.vertical').first
        box = resizer.bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()
        page.mouse.move(box['x'] + 55, box['y'] + box['height'] / 2, steps=5)
        page.mouse.up()
        columns = page.locator('#chats-wrapper').evaluate('el => el.style.gridTemplateColumns')
        drag_to(page, '#pane-ws1 .pane-move-handle', '#pane-ws4', finish=False)
        expect(page.locator('#pane-ws4')).to_have_class('chat-pane pane-drop-target')
        page.screenshot(path=str(args.artifacts / 'drop-target.png'))
        page.mouse.up()
        assert order(page) == ['pane-main', 'pane-ws2', 'pane-ws3', 'pane-ws4', 'pane-ws1']
        assert page.evaluate("document.getElementById('pane-ws1') === originalPane")
        assert page.evaluate('_workspaces.get(1) === originalWorkspace && originalWorkspace.isThinking')
        assert page.evaluate('_paneCompositeKey(1, originalWorkspace) === originalPaneKey')
        expect(page.locator('#pane-input-ws1')).to_have_value('Keep this unsent draft')
        assert page.evaluate("document.getElementById('chat-win-ws1').scrollTop === originalScroll")
        assert page.locator('#chats-wrapper').evaluate('el => el.style.gridTemplateColumns') == columns
        expect(page.locator('.chat-splitter.vertical')).to_have_count(2)
        expect(page.locator('.chat-splitter.horizontal')).to_have_count(1)
        assert page.evaluate("_resolveTargetPane('window 5', '').paneId === _paneId('ws1')")
        checks.append('five-pane drag preserves the live conversation, draft, scroll, IDs and resized tracks')

        # Drag the title itself and then use the keyboard on the visible grip.
        drag_to(page, '#pane-main .pane-name', '#pane-ws3')
        assert order(page) == ['pane-ws2', 'pane-ws3', 'pane-main', 'pane-ws4', 'pane-ws1']
        handle = page.locator('#pane-ws1 .pane-move-handle')
        handle.focus()
        handle.press('Home')
        assert order(page)[0] == 'pane-ws1'
        handle.press('ArrowDown')
        assert order(page)[3] == 'pane-ws1'
        expect(handle).to_be_focused()
        expect(page.locator('#pane-move-status')).to_contain_text('position 4 of 5')
        checks.append('title dragging, keyboard moves, focus and spoken position feedback')

        before = order(page)
        drag_to(page, '#pane-ws1 .pane-move-handle', '#pane-ws2', finish=False)
        page.keyboard.press('Escape')
        page.mouse.up()
        assert order(page) == before
        assert page.evaluate('originalWorkspace.isThinking')
        assert '/stop' not in writes
        page.mouse.move(*point(page, '#pane-ws1 .pane-move-handle'))
        page.mouse.down()
        page.mouse.move(5, 5, steps=5)
        page.mouse.up()
        assert order(page) == before
        expect(page.locator('.pane-drop-target, .pane-moving, body.chat-pane-moving')).to_have_count(0)
        checks.append('Escape cancels just the drag; dropping outside leaves the order unchanged')

        # Existing controls and the close button still respond after a move.
        page.locator('#pane-ws1 .pane-detail-btn').click()
        expect(page.locator('#pane-detail-pop-ws1')).to_be_visible()
        page.locator('#pane-ws1 .pane-detail-btn').click()
        select = page.locator('#pane-provider-ws1')
        select.select_option(select.locator('option').first.get_attribute('value'))
        assert order(page) == before
        page.locator('#pane-ws3 .chat-pane-close').click()
        assert 'pane-ws3' not in order(page)
        expect(page.locator('.chat-splitter.vertical')).to_have_count(1)
        assert page.evaluate("_orderedPanes().map(p => p.paneId).join('|')") == page.evaluate("[...document.querySelectorAll('.chat-pane:not([hidden])')].map(p => _paneId(p.id === 'pane-main' ? 'main' : p.id.slice(5))).join('|')")
        checks.append('controls, closing and pane numbering remain correct after rearrangement')

        # Both main-header replacement paths must regain a grip.
        page.evaluate("openProjectWorkspace('C:/Example/Home')")
        expect(page.locator('#pane-main .pane-move-handle')).to_be_enabled()
        page.locator('#pane-main .chat-pane-close').click()
        expect(page.locator('#pane-main')).to_be_hidden()
        page.evaluate("openProjectWorkspace('C:/Example/Restored')")
        expect(page.locator('#pane-main .pane-move-handle')).to_be_enabled()
        checks.append('closing and reopening a rooted main chat restores rearrangement controls')

        page.evaluate("localStorage.setItem('data-void-patrol-v1:arcade-preview', JSON.stringify({clearedSector: 1})); RewardThemes.paint();")
        for theme in ['minimal', 'cyber', 'event-horizon']:
            assert page.evaluate('theme => applyTheme(theme)', theme)
            drag_to(page, '#pane-main .pane-move-handle', '#pane-ws2', finish=False)
            assert page.locator('#pane-ws2').evaluate("""el => {
              const probe = document.createElement('span');
              probe.style.color = 'var(--data-orange)';
              el.appendChild(probe);
              const matches = getComputedStyle(el, '::after').borderTopColor === getComputedStyle(probe).color;
              probe.remove();
              return matches;
            }""")
            page.keyboard.press('Escape')
            page.mouse.up()
        checks.append('drag feedback inherits minimal, cyber and Event Horizon themes')

        # Native touch dispatch checks capture and touch-action in a mobile stack.
        mobile = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        mobile.route('**/*', route_request)
        touch = mobile.new_page()
        touch.on('pageerror', lambda error: errors.append(str(error)))
        touch.goto(args.base + '/', wait_until='networkidle')
        touch.evaluate("showPanel('chat'); openProjectWorkspace('C:/Example/Mobile', {forceNewPane: true})")
        expect(touch.locator('#pane-ws1 .pane-move-handle')).to_be_enabled()
        x1, y1 = point(touch, '#pane-ws1 .pane-move-handle')
        x2, y2 = point(touch, '#pane-main .pane-name')
        session = mobile.new_cdp_session(touch)
        session.send('Input.dispatchTouchEvent', {'type': 'touchStart', 'touchPoints': [{'x': x1, 'y': y1}]})
        session.send('Input.dispatchTouchEvent', {'type': 'touchMove', 'touchPoints': [{'x': x2, 'y': y2}]})
        session.send('Input.dispatchTouchEvent', {'type': 'touchEnd', 'touchPoints': []})
        assert order(touch) == ['pane-ws1', 'pane-main']
        assert touch.locator('#pane-ws1 .pane-move-handle').bounding_box()['width'] >= 44
        assert touch.evaluate('document.documentElement.scrollWidth <= innerWidth')
        checks.append('touch drag works in a 390px single-column layout')
        mobile.close()
        assert not errors, errors
        assert not {'/chat', '/chat_stream', '/stop'} & set(writes), writes
        browser.close()
    result = {'result': 'PASS', 'checks': checks, 'errors': errors}
    (args.artifacts / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
