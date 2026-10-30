'use strict';
// Exercise the actual routing and event handlers without a live bridge or model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
const begin = source.indexOf('function _resolvePaneTarget(');
const end = source.indexOf('function appendMessageToPane(', begin);
assert.ok(begin >= 0 && end > begin, 'routing functions must exist');
const folder = 'c:\\projects\\shared';
const mainFolder = 'c:\\projects\\home';
const newFolder = 'c:\\projects\\next';

function element(id = '') {
  const classes = new Set();
  return {
    id, hidden: false, children: [], buttons: [], value: '',
    classList: { contains: c => classes.has(c), add: c => classes.add(c) },
    appendChild(child) { this.children.push(child); },
    querySelectorAll() { return this.buttons; },
    querySelector() { return null; },
    set innerHTML(value) {
      this.html = value;
      this.buttons = [...value.matchAll(/data-idx="(\d+)"/g)].map(([, idx]) => ({
        dataset: { idx }, classList: { contains: () => false, add: () => {} },
        addEventListener(name, callback) { this[name] = callback; },
      }));
    },
  };
}

function fixture(tag = 'scurrent') {
  const messages = [], sends = [], spawns = [], opens = [], logs = [], timers = [];
  const windows = new Map(['chat-window', 'chat-input', 'pane-main',
    'chat-win-ws2', 'chat-win-ws3', 'pane-ws2', 'pane-ws3',
    'pane-input-ws2', 'pane-input-ws3'].map(id => [id, element(id)]));
  const workspaces = new Map([
    [1, { path: mainFolder, name: 'Home', isMain: true }],
    [2, { path: folder, name: 'Shared' }],
    [3, { path: folder, name: 'Shared' }],
  ]);
  const paneOrder = ['pane-main', 'pane-ws2', 'pane-ws3'];
  windows.set('chats-wrapper', {
    querySelectorAll: () => paneOrder.map(id => windows.get(id)).filter(pane => pane && !pane.hidden),
  });
  const ctx = vm.createContext({
    _workspaces: workspaces, _paneId: base => `${tag}-${base}`,
    _lastKnownActiveProvider: 'test',
    document: { getElementById: id => windows.get(id) || null, createElement: () => element() },
    appendMessageToPane: (win, role, text) => messages.push({ pane: win.id, role, text }),
    appendMessage: (role, text) => messages.push({ pane: 'chat-window', role, text }),
    addLog: text => logs.push(text), playDataSound: () => {},
    loadProjectGraph: () => {}, renderProjectMiniMatrix: () => {},
    openProjectWorkspace: value => opens.push(value),
    MAIN_CHAT_CREW: 'data', _crewAvatar: () => '', escapeHtml: text => text,
    _isPinnedToBottom: () => false,
    sendMessage: () => sends.push({ id: 'main', text: windows.get('chat-input').value }),
    sendProjectMessage: id => sends.push({ id, text: windows.get(`pane-input-ws${id}`).value }),
    setTimeout: callback => timers.push(callback),
  });
  vm.runInContext(source.slice(begin, end), ctx, { filename: 'app.js:pane-routing' });
  ctx._spawnWorkspacesFromEvent = specs => spawns.push(specs);
  const key = id => `${id === 1 ? mainFolder : folder}::${tag}-${id === 1 ? 'main' : `ws${id}`}`;
  return { ctx, messages, sends, spawns, opens, logs, timers, windows, workspaces, key, paneOrder };
}

function ask(f, pane) {
  f.ctx._handleUiEvent({ type: 'ask_options', payload: { pane, question: 'Choose a format', options: ['Text', 'Audio'] } });
}

function root(f, pane) {
  f.ctx._handleUiEvent({ type: 'project_rooted', payload: { pane, path: newFolder, name: 'Next', nodes: [] } });
}

test('exact composite keys isolate chats sharing a folder and normalize case', () => {
  const f = fixture();
  assert.equal(f.ctx._resolvePaneTarget(f.key(2)).wsId, 2);
  assert.equal(f.ctx._resolvePaneTarget(`  ${f.key(3).toUpperCase()}  `).wsId, 3);
  assert.equal(f.ctx._resolvePaneTarget(f.key(1)).winEl.id, 'chat-window');
});

test('folder, stale, foreign and malformed keys cannot render or change any chat', () => {
  for (const key of [folder, mainFolder, `${folder}::sold-ws2`, `${folder}::sforeign-ws3`,
    '::sold-main', `${folder}::scurrent-ws99`, 'c:\\missing', '::unassigned-job-1', 12, {}]) {
    const f = fixture();
    root(f, key);
    ask(f, key);
    assert.equal(f.messages.length, 0, String(key));
    assert.equal(f.windows.get('chat-window').children.length, 0);
    assert.equal(f.windows.get('chat-win-ws2').children.length, 0);
    assert.equal(f.windows.get('chat-win-ws3').children.length, 0);
    assert.equal(f.workspaces.get(1).path, mainFolder);
    assert.equal(f.workspaces.get(2).path, folder);
    assert.equal(f.workspaces.get(3).path, folder);
    assert.equal(f.opens.length, 0);
  }
});

test('a folder-only event cannot borrow the sole remaining matching chat', () => {
  const f = fixture();
  f.workspaces.delete(3);
  root(f, folder);
  ask(f, folder);
  assert.equal(f.workspaces.get(2).path, folder);
  assert.equal(f.messages.length, 0);
  assert.equal(f.windows.get('chat-win-ws2').children.length, 0);
});

test('a valid project change updates and confirms only its originating chat', () => {
  const f = fixture();
  root(f, f.key(3));
  assert.equal(f.workspaces.get(3).path, newFolder);
  assert.equal(f.workspaces.get(2).path, folder);
  assert.equal(f.workspaces.get(1).path, mainFolder);
  assert.deepEqual(f.messages.map(m => m.pane), ['chat-win-ws3']);
  ask(f, f.key(3));
  assert.equal(f.windows.get('chat-window').children.length, 0);
  assert.equal(f.windows.get('chat-win-ws2').children.length, 0);
});

test('a valid main project change uses the real main chat element', () => {
  const f = fixture();
  root(f, f.key(1));
  assert.equal(f.workspaces.get(1).path, newFolder);
  assert.deepEqual(f.messages.map(m => m.pane), ['chat-window']);
});

test('a choice card and its answer stay in the exact sibling chat', () => {
  const f = fixture();
  ask(f, f.key(3));
  const cards = f.windows.get('chat-win-ws3').children;
  assert.equal(cards.length, 1);
  assert.equal(f.windows.get('chat-win-ws2').children.length, 0);
  assert.equal(f.windows.get('chat-window').children.length, 0);
  cards[0].buttons[1].click();
  assert.deepEqual(f.sends, [{ id: 3, text: 'Audio' }]);
});

test('rooted and unrooted main chats can receive and answer their own choices', () => {
  for (const unrooted of [false, true]) {
    const f = fixture();
    if (unrooted) f.workspaces.delete(1);
    ask(f, unrooted ? '::scurrent-main' : f.key(1));
    const cards = f.windows.get('chat-window').children;
    assert.equal(cards.length, 1);
    cards[0].buttons[0].click();
    assert.deepEqual(f.sends, [{ id: 'main', text: 'Text' }]);
  }
});

test('only the unrooted main identity can initialize its first workspace', () => {
  const f = fixture();
  f.workspaces.clear();
  root(f, '::sforeign-main');
  root(f, folder);
  assert.equal(f.opens.length, 0);
  root(f, '::scurrent-main');
  assert.deepEqual(f.opens, [newFolder]);
  assert.deepEqual(f.messages.map(m => m.pane), ['chat-window']);
});

test('explicitly unscoped events still reach main', () => {
  const f = fixture();
  ask(f, '');
  assert.equal(f.windows.get('chat-window').children.length, 1);
  root(f, '');
  assert.equal(f.workspaces.get(1).path, newFolder);
});

test('missing or hidden destinations cannot redirect into main', () => {
  for (const missing of [true, false]) {
    const f = fixture();
    if (missing) f.windows.delete('chat-win-ws3');
    else f.windows.get('pane-ws3').hidden = true;
    root(f, f.key(3));
    ask(f, f.key(3));
    assert.equal(f.messages.length, 0);
    assert.equal(f.workspaces.get(3).path, folder);
    assert.equal(f.windows.get('chat-window').children.length, 0);
  }
  const f = fixture();
  f.workspaces.delete(1);
  f.windows.get('pane-main').hidden = true;
  root(f, '::scurrent-main');
  ask(f, '');
  assert.equal(f.messages.length, 0);
  assert.equal(f.opens.length, 0);
});

test('broadcast events are acted on only by the owning tab', () => {
  const owner = fixture('sowner');
  const other = fixture('sother');
  const pane = owner.key(3);
  for (const f of [other, owner]) {
    ask(f, pane);
    f.ctx._handleUiEvent({ type: 'spawn_workspaces', payload: { pane, workspaces: [{ path: newFolder }] } });
    root(f, pane);
  }
  assert.equal(other.messages.length, 0);
  assert.equal(other.spawns.length, 0);
  assert.equal(other.windows.get('chat-window').children.length, 0);
  assert.equal(other.workspaces.get(3).path, folder);
  assert.equal(owner.spawns.length, 1);
  assert.equal(owner.windows.get('chat-win-ws3').children.length, 1);
  assert.equal(owner.workspaces.get(3).path, newFolder);
});

test('folder-only spawn events do not open unrelated chat windows', () => {
  const f = fixture();
  f.ctx._handleUiEvent({ type: 'spawn_workspaces', payload: { pane: folder, workspaces: [{ path: newFolder }] } });
  assert.equal(f.spawns.length, 0);
});

test('intentional messages require an unambiguous name, path, number or exact ID', () => {
  const f = fixture();
  for (const address of ['shared', folder, 'projects']) {
    assert.equal(f.ctx._resolveTargetPane(address, f.key(1)), null);
  }
  assert.equal(f.ctx._resolveTargetPane('window 3', f.key(1)).wsId, 3);
  assert.equal(f.ctx._resolveTargetPane('scurrent-ws3', f.key(1)).wsId, 3);
  assert.equal(f.ctx._resolveTargetPane(f.key(3), f.key(1)).wsId, 3);
  assert.equal(f.ctx._resolveTargetPane('main', f.key(3)).wsId, 1);
  f.workspaces.get(3).name = 'Tour';
  assert.equal(f.ctx._resolveTargetPane('Tour', f.key(1)).wsId, 3);
});

test('stale identities and digits inside project names cannot select window numbers', () => {
  const f = fixture();
  for (const address of [`${folder}::sold-ws2`, 'sold-ws2', '::sother-main', 'project 2 notes']) {
    assert.equal(f.ctx._resolveTargetPane(address, f.key(1)), null, address);
  }
});

test('an ambiguous handoff reports only to its sender and does not run another chat', () => {
  const f = fixture();
  f.ctx._deliverPaneMessage({ from_key: f.key(1), to: 'shared', message: 'Review this' });
  assert.equal(f.sends.length, 0);
  assert.deepEqual(f.messages.map(m => m.pane), ['chat-window']);
  assert.match(f.messages[0].text, /exact pane ID or window number/);
});

test('a valid handoff replies to the exact sender even when names match', () => {
  const f = fixture();
  f.ctx._deliverPaneMessage({ from_key: f.key(2), to: f.key(3), message: 'Review this' });
  assert.deepEqual(f.messages.map(m => m.pane), ['chat-win-ws3']);
  assert.equal(f.sends.length, 1);
  assert.equal(f.sends[0].id, 3);
  assert.ok(f.sends[0].text.includes(`addressed to "${f.key(2)}"`));
});

test('a foreign sender or missing destination cannot render or submit a handoff', () => {
  const f = fixture();
  f.ctx._deliverPaneMessage({ from_key: `${folder}::sother-ws2`, to: 'window 3', message: 'Foreign' });
  f.windows.delete('chat-win-ws3');
  f.ctx._deliverPaneMessage({ from_key: f.key(2), to: f.key(3), message: 'Closed' });
  assert.equal(f.messages.length, 0);
  assert.equal(f.sends.length, 0);
});

test('queued handoffs stop if their destination closes or changes projects', () => {
  for (const closes of [true, false]) {
    const f = fixture();
    f.workspaces.get(3).isThinking = true;
    f.ctx._deliverPaneMessage({ from_key: f.key(2), to: f.key(3), message: 'Later' });
    assert.equal(f.timers.length, 1);
    if (closes) f.workspaces.delete(3);
    else {
      f.workspaces.get(3).path = newFolder;
      f.workspaces.get(3).isThinking = false;
    }
    f.timers.shift()();
    assert.equal(f.sends.length, 0);
    assert.equal(f.timers.length, 0);
  }
});

test('window numbers and rosters follow rearranged panes while exact IDs stay stable', () => {
  const f = fixture();
  f.paneOrder.splice(0, 3, 'pane-ws3', 'pane-main', 'pane-ws2');
  assert.equal(f.ctx._resolveTargetPane('window 1', f.key(2)).wsId, 3);
  assert.equal(f.ctx._resolveTargetPane('window 2', f.key(2)).wsId, 1);
  assert.equal(f.ctx._resolveTargetPane(f.key(2), f.key(1)).wsId, 2);
  assert.deepEqual(Array.from(f.ctx._buildPaneRoster('main').open_panes, p => p.pane_id),
    ['scurrent-ws3', 'scurrent-main', 'scurrent-ws2']);
  f.ctx._deliverPaneMessage({ from_key: f.key(2), to: 'window 1', message: 'Review this' });
  assert.equal(f.sends[0].id, 3);
});

test('closing a moved pane renumbers only the visible windows', () => {
  const f = fixture();
  f.paneOrder.splice(0, 3, 'pane-ws3', 'pane-main', 'pane-ws2');
  f.windows.get('pane-main').hidden = true;
  assert.equal(f.ctx._resolveTargetPane('window 2', f.key(3)).wsId, 2);
  assert.equal(f.ctx._resolveTargetPane('window 3', f.key(3)), null);
  assert.equal(f.ctx._resolveTargetPane(f.key(1), f.key(3)), null);
});

test('an unrooted main pane keeps its visible position and can receive a handoff', () => {
  const f = fixture();
  f.workspaces.delete(1);
  f.paneOrder.splice(0, 3, 'pane-ws2', 'pane-main', 'pane-ws3');
  const target = f.ctx._resolveTargetPane('window 2', f.key(2));
  assert.equal(target.key, '::scurrent-main');
  assert.equal(target.isMain, true);
  f.ctx._deliverPaneMessage({ from_key: f.key(2), to: 'window 2', message: 'Review this' });
  assert.equal(f.sends[0].id, 'main');
});
