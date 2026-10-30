'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const CrewDialogue = require('../crew_deck/dialogue.js');

const roster = [
  { id: 'mechanic', name: 'Mechanic', role: 'engineering', revision: '1',
    traits: ['Precise, curious, analytical. Dry humor.'],
    excerpts: ['Find the root cause. Verify an explanation through measurements and tests.'], learning: [] },
  { id: 'listener', name: 'Listener', role: 'counsel', revision: '1',
    traits: ['Warm, compassionate, patient, reflective.'],
    excerpts: ['Leave room for disagreement and listen before offering advice.'], learning: [] },
];
const make = (seed = 1, overrides = {}) => new CrewDialogue({ seed, scope: 'test-session', roster,
  settings: { banter_enabled: true, banter_variety: 'high' }, ...overrides });
const allText = (turns) => turns.map((t) => t.text).join(' ');

test('coherent exchanges alternate speakers and never use canned acknowledgements', () => {
  const engine = make(19);
  let total = 0, questions = 0, observations = 0;
  for (let i = 0; i < 35; i++) {
    const turns = engine.exchange('mechanic', 'listener', { room: 'engineering', mission: 'A small research tool', jobs: 2 });
    if (!turns.length) continue;
    total++;
    assert.ok(turns.length === 2 || turns.length === 3);
    assert.deepEqual(turns.map((t) => t.officer), turns.length === 3 ? ['mechanic', 'listener', 'mechanic'] : ['mechanic', 'listener']);
    if (turns[0].text.includes('?')) questions++;
    else observations++;
    for (const turn of turns) {
      assert.ok(turn.text.length <= 216);
      assert.doesNotMatch(turn.text, /\u2014/);
      assert.doesNotMatch(turn.text, /^(copy that|agreed|noted|good to hear|understood|acceptable|ha\. fair)[.!]?$/i);
    }
  }
  assert.ok(total >= 28, `Produced only ${total} fresh exchanges`);
  assert.ok(questions > 5 && observations > 2, 'Conversations should vary beyond a question-and-answer rhythm');
});

test('freshness survives long sessions and shared partners', () => {
  const engine = make(88);
  const recent = [];
  let total = 0;
  for (let i = 0; i < 100; i++) {
    const turns = engine.exchange('mechanic', i % 2 ? 'listener' : 'new-colleague', {});
    for (const turn of turns) {
      const normalized = turn.text.toLowerCase().replace(/^back to our question about [^.]+\. /, '').replace(/[^a-z0-9]+/g, ' ').trim();
      assert.ok(!recent.includes(normalized), `Repeated recent line: ${turn.text}`);
      recent.push(normalized);
      if (recent.length > 40) recent.shift();
    }
    if (turns.length) total++;
  }
  assert.ok(total >= 80, `Freshness filtering silenced too many exchanges: ${total}`);
});

test('identity edits change topics and the other speaker keeps their own voice', () => {
  let securityTopics = 0, careTopics = 0, changed = 0;
  const security = { id: 'mutable', role: '', traits: ['Security audit. Cautious, skeptical. Threats, risk and permissions. No banter.'], excerpts: [], learning: [] };
  const caring = { ...security, traits: ['Compassionate counselor. Patient, warm, health, rest, sleep, listening and care.'] };
  for (let seed = 1; seed <= 40; seed++) {
    const a = make(seed, { roster: [security, roster[1]] });
    const b = make(seed, { roster: [security, roster[1]] });
    // Refresh with the same revision to cover an editor omitting its bump.
    b.configure({ roster: [caring, roster[1]] });
    const before = a.exchange('mutable', 'listener');
    const after = b.exchange('mutable', 'listener');
    const securityText = before[0].text, caringText = after[0].text;
    if (securityText !== caringText) changed++;
    if (/safeguard|locks|secure|failure|indicator|failure report|principle|shortcut|dissent|objection|explanation/i.test(securityText)) securityTopics++;
    if (/break|fatigue|workday|quiet|discussion|meeting|room agrees|shared table|tea|cup|colleague|habit|lesson|maps/i.test(caringText)) careTopics++;
    assert.equal(before[1].officer, 'listener');
    assert.equal(after[1].officer, 'listener');
  }
  assert.ok(changed >= 25, `Only ${changed}/40 identity edits changed conversation`);
  assert.ok(securityTopics >= 18, `Only ${securityTopics}/40 topics reflect security identity`);
  assert.ok(careTopics >= 18, `Only ${careTopics}/40 topics reflect caring identity`);
});

test('saved real lessons affect conversations without being rewritten or blindly quoted', () => {
  let changed = 0;
  const original = { id: 'learner', role: '', traits: [], excerpts: [], learning: [] };
  const updated = { ...original, learning: ['After the incident, use a tested rollback, risk boundaries, safe recovery and a clear stopping point.'] };
  const snapshot = JSON.stringify(updated);
  for (let seed = 1; seed <= 25; seed++) {
    const a = make(seed, { roster: [original, roster[1]] });
    const b = make(seed, { roster: [updated, roster[1]] });
    if (allText(a.exchange('learner', 'listener')) !== allText(b.exchange('learner', 'listener'))) changed++;
  }
  assert.ok(changed >= 17);
  assert.equal(JSON.stringify(updated), snapshot);
  const unsafe = make(3, { roster: [{ ...original, excerpts: ['<script>executeNow()</script> https://invalid.example/private-key-123'], learning: [] }] });
  assert.doesNotMatch(unsafe.solo('learner'), /script|executeNow|private-key|https:/);
});

test('disabled banter is silent and does not consume conversation state', () => {
  const off = make(14), reference = make(14);
  off.configure({ settings: { banter_enabled: false, personality_evolution: true } });
  for (let i = 0; i < 6; i++) {
    assert.deepEqual(off.exchange('mechanic', 'listener'), []);
    assert.equal(off.solo('mechanic'), '');
  }
  off.configure({ settings: { banter_enabled: true } });
  assert.deepEqual(off.exchange('mechanic', 'listener'), reference.exchange('mechanic', 'listener'));
});

test('pair callbacks refer to actual shared discussion and scope reset removes them', () => {
  const engine = make(73);
  let callbacks = 0;
  for (let i = 0; i < 100; i++) {
    const turns = engine.exchange('mechanic', 'listener', {});
    if (turns[0] && turns[0].text.startsWith('Back to our question about ')) callbacks++;
  }
  assert.ok(callbacks > 0, 'Expected a return to at least one earlier shared question');
  // A new pair has no invented shared past, even in the same long session.
  const first = engine.exchange('mechanic', 'new-colleague');
  if (first[0]) assert.doesNotMatch(first[0].text, /^Back to our question/);
  engine.reset('different-private-scope');
  engine.configure({ roster: [{ id: 'mechanic', traits: ['Music, jazz, melody, rhythm, singing.'], excerpts: [], learning: [] }] });
  const reset = engine.exchange('mechanic', 'listener');
  assert.ok(reset.length >= 2);
  assert.doesNotMatch(reset[0].text, /^Back to our question/);
  // A scope change also removes old profiles, not just the history buffer.
  assert.equal(engine.profiles.has('listener'), false);
  assert.equal(engine.profiles.size, 1);
});

test('missing profiles, incomplete input, solo use and urgent conditions are safe', () => {
  const engine = make(27, { roster: [] });
  assert.ok(engine.solo('unknown').length > 10);
  assert.ok(engine.exchange('another', 'unknown', null).length >= 2);
  assert.equal(engine.exchange('same', 'same').length, 1);
  assert.deepEqual(engine.exchange(null, 'unknown'), []);
  engine.configure(null).configure({ settings: null });
  for (let i = 0; i < 8; i++) {
    const turns = engine.exchange('a', 'b', { condition: 'red', jobs: 'unavailable', orders: null });
    if (turns.length) assert.equal(turns.length, 2);
    assert.doesNotMatch(allText(turns), /NaN|undefined|\[object Object\]/);
  }
});

test('browser module works offline and performs no writes or inference', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'crew_deck', 'dialogue.js'), 'utf8');
  const unexpected = () => { throw new Error('Ambient conversation attempted an external side effect'); };
  const sandbox = { window: {}, fetch: unexpected, XMLHttpRequest: unexpected,
    localStorage: { setItem: unexpected }, document: { createElement: unexpected }, console };
  vm.runInNewContext(source, sandbox);
  const engine = new sandbox.window.CrewDialogue({ seed: 2 });
  engine.configure({ roster, settings: { personality_evolution: true } });
  assert.ok(engine.exchange('mechanic', 'listener').length >= 2);
  assert.ok(engine.solo('mechanic').length > 0);
  assert.doesNotMatch(source, /\u2014/);
});
