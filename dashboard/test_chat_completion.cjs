// Exercise the real chat functions with streams that never close or finish cancel().
// Usage: node test_chat_completion.cjs [path/to/app.js]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, 'app.js'), 'utf8');

function productionFunction(name) {
  const match = source.match(new RegExp(`^async function ${name}\\([^]*?^}`, 'm'));
  assert.ok(match, `Missing production function ${name}`);
  return match[0];
}

async function run(pane, terminal, cancelRejects) {
  const messages = [];
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: 'hello', textContent: '', title: '', style: {},
      classList: { add() {}, remove() {} }, remove() {}, querySelector() { return null; },
    });
    return elements.get(id);
  };
  const ws = { path: '/test', provider: 'codex', crew: 'test', name: 'test', isThinking: false };
  let reads = 0, cancels = 0;
  const events = terminal === 'done'
    ? [['token', 'Completed answer'], ['done', '']]
    : [['error', 'Simulated failure']];
  const reader = {
    async read() {
      reads++;
      assert.equal(reads, 1, 'must not await EOF after a terminal event');
      const text = events.map(([kind, text]) => `event: ${kind}\ndata: ${JSON.stringify({ text })}\n\n`).join('');
      return { done: false, value: new TextEncoder().encode(text) };
    },
    cancel() {
      cancels++;
      return cancelRejects ? Promise.reject(new Error('cancel failed')) : new Promise(() => {});
    },
  };
  const context = {
    AbortController, TextDecoder, Date, JSON, Map,
    document: { getElementById: element },
    fetch: async () => ({ ok: true, status: 200, body: { getReader: () => reader } }),
    API_BASE: 'http://test.invalid', _workspaces: new Map(pane === 'project' ? [[1, ws]] : []),
    _autoSpeakNextReply: false, isThinking: false, _abortController: null,
    _lastKnownActiveEffort: '', _lastKnownActiveProvider: 'codex', MAIN_CHAT_CREW: 'test',
    _streamMeta: null, _streamStartTime: Date.now(), _streamTimerInterval: null,
    _messageQueue: [], _inFlightUserBubble: null, _lastActiveWsId: null,
    setTimeout: () => 1, clearTimeout() {}, clearInterval() {},
    _paneId: x => x, _paneStyleWire: () => ({}), _buildPaneRoster: () => ({}),
    _getPendingForPane: () => [], _renderAttachmentTray() {},
    _createThoughtStream: () => element('thought'), _createPaneThoughtStream: () => element('thought'),
    _startStreamBubble: () => ({}), _appendStreamToken: (_, text) => messages.push(text),
    _finalizeStreamBubble() {}, _finalizePaneThoughtStream() {}, removeThinkingFromPane() {},
    appendMessage: (_, text) => messages.push(text), appendMessageToPane: (_, role, text) => {
      if (role === 'data') messages.push(text);
    },
    offlineResponse: () => 'UNEXPECTED OFFLINE FALLBACK', playDataSound() {},
    addLog() {}, setStatus() {}, fetchVitals() {}, crewLabel: x => x,
  };
  vm.createContext(context);
  const name = pane === 'main' ? '_dispatchChatMessage' : 'sendProjectMessage';
  vm.runInContext(productionFunction(name), context);
  await context[name](...(pane === 'main' ? ['hello', []] : [1]));
  assert.equal(reads, 1);
  assert.equal(cancels, 1);
  assert.equal(pane === 'main' ? context.isThinking : ws.isThinking, false);
  assert.equal(pane === 'main' ? context._abortController : ws.abortController, null);
  assert.deepEqual(messages, [terminal === 'done' ? 'Completed answer' : 'Simulated failure'].map(
    text => pane === 'main' && terminal === 'error' ? `⚠ ${text}` : text));
}

for (const pane of ['main', 'project']) {
  for (const terminal of ['done', 'error']) {
    for (const reject of [false, true]) {
      test(`${pane}: ${terminal}, cancellation ${reject ? 'rejects' : 'never resolves'}`,
        { timeout: 2000 }, () => run(pane, terminal, reject));
    }
  }
}
