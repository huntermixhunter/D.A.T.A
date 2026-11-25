'use strict';
// Crew Deck "needs attention" view: which work is flagged, for whom, and where
// GO TO WORK sends the Captain. Runs data.js in a sandbox with no network.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'crew_deck', 'data.js'), 'utf8');
const sandbox = { window: {}, console };
vm.runInNewContext(source, sandbox);
const CD = sandbox.window.CD;

const NOW = 1_800_000_000_000;           // ms
const S = (ms) => (NOW - ms) / 1000;      // seconds-since-epoch for "ms ago"
const MIN = 60e3, HOUR = 3600e3;
const plain = (v) => JSON.parse(JSON.stringify(v));

function snap(extra = {}) {
  return { jobs: [], orders: [], crew: null, ...extra };
}
function crewFeed(extra = {}) {
  return { available: true, scope: 'hunter', since: 1, agents: [], turns: [], recent_agents: [], recent_turns: [], ...extra };
}

test('failed and timed-out jobs are flagged with their log tail and a job target', () => {
  const s = snap({ jobs: [
    { id: 'j1', label: 'pytest run', status: 'failed', exit_code: 2, finished_at: S(5 * MIN), stderr: 'a\nb\nc\nd\ne\nf\ng\nAssertionError: boom\n' },
    { id: 'j2', label: 'Render reel', status: 'timeout', finished_at: S(30 * MIN), stdout: 'out' },
    { id: 'j3', label: 'old failure', status: 'failed', finished_at: S(30 * HOUR) },
    { id: 'j4', label: 'fine', status: 'ok', finished_at: S(MIN) },
    { id: 'j5', label: 'preview server', status: 'running', started_at: S(10 * HOUR) },
  ] });
  const out = plain(CD.deriveAttention(s, NOW, {}));
  assert.deepEqual(out.map((a) => a.key), ['j1', 'j2']);
  const [a, b] = out;
  assert.equal(a.role, 'engineering');
  assert.equal(a.level, 'error');
  assert.match(a.label, /^Job failed: pytest run/);
  assert.equal(a.log.split('\n').length, 6);
  assert.match(a.log, /AssertionError: boom$/);
  assert.deepEqual(a.target, { kind: 'job', id: 'j1' });
  assert.equal(b.role, 'content');
  assert.match(b.label, /^Job timed out/);
});

test('acknowledged items stay hidden; long-running jobs are never treated as stuck', () => {
  const s = snap({ jobs: [{ id: 'j1', label: 'x', status: 'failed', finished_at: S(MIN) },
                          { id: 'j2', label: 'server', status: 'running', started_at: S(48 * HOUR) }] });
  assert.equal(CD.deriveAttention(s, NOW, { j1: NOW }).length, 0);
});

test('standing orders whose last run failed are flagged once per run', () => {
  const orders = [
    { id: 'so-1', name: 'Weekly security audit', last_run: S(2 * HOUR), last_result: '(Standing order failed: CLI exited 1)' },
    { id: 'so-2', name: 'Post to Instagram', last_run: S(HOUR), last_result: 'Instagram poster exited 3.\nSTDOUT:' },
    { id: 'so-3', name: 'Blog post', last_run: S(HOUR), last_result: "I'll write the post. (No errors so far.)" },
    { id: 'so-4', name: 'News refresh', last_run: S(HOUR), last_result: 'Instagram poster ran clean.' },
  ];
  const out = plain(CD.deriveAttention(snap({ orders }), NOW, {}));
  assert.deepEqual(out.map((a) => a.target.id).sort(), ['so-1', 'so-2']);
  const audit = out.find((a) => a.target.id === 'so-1');
  assert.equal(audit.role, 'security');
  assert.deepEqual(audit.target, { kind: 'order', id: 'so-1' });
  // Acknowledging this run does not hide the next failed run.
  const acks = { [audit.key]: NOW };
  assert.equal(CD.deriveAttention(snap({ orders }), NOW, acks).filter((a) => a.target.id === 'so-1').length, 0);
  orders[0].last_run = S(MIN);
  assert.equal(CD.deriveAttention(snap({ orders }), NOW, acks).filter((a) => a.target.id === 'so-1').length, 1);
});

test('failed subagents and errored chats point at the pane they ran in', () => {
  const crew = crewFeed({
    recent_agents: [
      { id: 'a1', role: 'review', type: 'riker', description: 'review the diff', status: 'failed', ended_at: S(3 * MIN), summary: 'Tool error', pane: 'sabc-ws2', project: 'DATA' },
      { id: 'a2', role: 'security', type: 'worf', status: 'stopped', ended_at: S(MIN), pane: 'sabc-main' },
      { id: 'a3', role: null, type: 'Explore', description: 'find files', status: 'failed', ended_at: S(MIN), pane: 'sabc-main', project: '' },
    ],
    recent_turns: [
      { id: 't1', role: null, source: 'chat', ok: false, ended_at: S(2 * MIN), pane: 'sabc-ws1', project: 'AnandaDAO', provider: 'claude-cli' },
      { id: 't2', role: null, source: 'chat', ok: true, ended_at: S(MIN), pane: 'sabc-main' },
    ],
  });
  const out = plain(CD.deriveAttention(snap({ crew }), NOW, {}));
  const byKey = Object.fromEntries(out.map((a) => [a.key, a]));
  assert.deepEqual(Object.keys(byKey).sort(), ['agent:a1', 'agent:a3', 'turn:t1']);
  assert.equal(byKey['agent:a1'].role, 'review');
  assert.deepEqual(byKey['agent:a1'].target, { kind: 'pane', pane: 'sabc-ws2', project: 'DATA' });
  assert.equal(byKey['agent:a3'].role, 'core', 'generic helpers belong to the main computer');
  assert.equal(byKey['turn:t1'].role, 'core');
  assert.match(byKey['turn:t1'].label, /in AnandaDAO/);
});

test('a failed standing-order turn is not listed twice', () => {
  const s = snap({
    orders: [{ id: 'so-9', name: 'Digest', last_run: S(MIN), last_result: '(Standing order failed: x)' }],
    crew: crewFeed({ recent_turns: [{ id: 't9', role: 'counsel', source: 'order', order_id: 'so-9', label: 'Digest', ok: false, ended_at: S(MIN) }] }),
  });
  const out = plain(CD.deriveAttention(s, NOW, {}));
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].target, { kind: 'order', id: 'so-9' });
});

test('stalls: silent work is flagged, work waiting on a helper is not', () => {
  const crew = crewFeed({
    agents: [
      { id: 'p1', role: 'engineering', type: 'geordi', description: 'debug the bridge', last_event_at: S(40 * MIN), doing: 'Waiting on 1 helper', pane: 'sabc-ws1', project: 'LCARS' },
      { id: 'c1', role: null, type: 'Explore', description: 'grep logs', parent: 'p1', turn: 't1', last_event_at: S(20 * MIN), doing: 'Running a command', pane: 'sabc-ws1', project: 'LCARS' },
      { id: 'q1', role: 'security', type: 'worf', description: 'audit', turn: 't2', last_event_at: S(2 * MIN), doing: 'Reading app.js' },
    ],
    turns: [
      { id: 't1', role: null, source: 'chat', last_event_at: S(45 * MIN), pane: 'sabc-ws1', project: 'LCARS' },
      { id: 't3', role: 'command', source: 'chat', last_event_at: S(15 * MIN), doing: 'Thinking', pane: 'sabc-main', project: '' },
      { id: 't4', role: null, source: 'order', order_id: 'so-5', label: 'Briefing', last_event_at: S(13 * MIN) },
      { id: 't5', role: null, source: 'chat', last_event_at: S(MIN) },
    ],
  });
  const out = plain(CD.deriveAttention(snap({ crew }), NOW, {}));
  const ids = out.map((a) => a.key.split(':')[1]).sort();
  assert.deepEqual(ids, ['c1', 't3', 't4'], 'p1 waits on c1 and t1 waits on c1; q1 and t5 are moving');
  for (const a of out) { assert.equal(a.level, 'warn'); assert.equal(a.kind, 'stuck'); }
  const c1 = out.find((a) => a.key.startsWith('stuck:c1:'));
  assert.equal(c1.role, 'core');
  assert.match(c1.detail, /^Quiet for 20m 00s on "Running a command"$/);
  assert.deepEqual(out.find((a) => a.key.startsWith('stuck:t4:')).target, { kind: 'order', id: 'so-5' });
  assert.equal(out.find((a) => a.key.startsWith('stuck:t3:')).role, 'command');
  // A stall acknowledged at one quiet point comes back if it stalls again later.
  const ack = { [c1.key]: NOW };
  assert.equal(CD.deriveAttention(snap({ crew }), NOW, ack).some((a) => a.key === c1.key), false);
  crew.agents[1].last_event_at = S(13 * MIN);
  assert.equal(CD.deriveAttention(snap({ crew }), NOW, ack).some((a) => a.key.startsWith('stuck:c1:')), true);
});

test('errors sort before stalls, newest first', () => {
  const s = snap({
    jobs: [{ id: 'old', label: 'a', status: 'failed', finished_at: S(3 * HOUR) },
           { id: 'new', label: 'b', status: 'failed', finished_at: S(MIN) }],
    crew: crewFeed({ turns: [{ id: 'tx', role: null, source: 'chat', last_event_at: S(30 * MIN) }] }),
  });
  assert.deepEqual(plain(CD.deriveAttention(s, NOW, {})).map((a) => a.key.split(':')[0]), ['new', 'old', 'stuck']);
});

test('GO TO WORK targets for every duty source', () => {
  assert.equal(CD.targetFor(null), null);
  assert.deepEqual(plain(CD.targetFor({ type: 'job', id: 'j1' })), { kind: 'job', id: 'j1' });
  assert.deepEqual(plain(CD.targetFor({ type: 'order', id: 7 })), { kind: 'order', id: '7' });
  assert.deepEqual(plain(CD.targetFor({ type: 'mission' })), { kind: 'mission' });
  assert.deepEqual(plain(CD.targetFor({ type: 'agent', pane: 'sabc-ws1', project: 'DATA' })), { kind: 'pane', pane: 'sabc-ws1', project: 'DATA' });
  assert.deepEqual(plain(CD.targetFor({ type: 'turn', pane: '', project: '' })), { kind: 'pane', pane: '', project: '' });
  assert.deepEqual(plain(CD.targetFor({ type: 'turn', orderId: 'so-2', pane: '' })), { kind: 'order', id: 'so-2' });
});

test('live duties carry the order id for order turns', () => {
  const s = snap({ crew: crewFeed({ turns: [{ id: 't1', role: 'counsel', source: 'order', order_id: 'so-3', label: 'Digest', started_at: S(MIN), last_event_at: S(1000) }] }) });
  const d = CD.deriveLive(s, NOW, {}).duties.find((x) => x.key === 'turn:t1');
  assert.deepEqual(plain(CD.targetFor(d.source)), { kind: 'order', id: 'so-3' });
});

test('no em dashes in the deck sources', () => {
  for (const f of ['data.js', 'deck.js', 'deck.css', 'host.js']) {
    assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '..', 'crew_deck', f), 'utf8'), /—/, f);
  }
});
