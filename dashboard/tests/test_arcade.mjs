import test from 'node:test';
import assert from 'node:assert/strict';
import { Game, WORLD, delta, distance, wrap, angleDiff, cleanProfile, purchase, hangarCost, UPGRADES, REWARD_SECTOR, hasEventHorizon } from '../arcade/engine.mjs';

function seeded() {
  let seed = 123456;
  return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
}
function game() { const g = new Game({ random: seeded() }); g.start(); return g; }
// Move a station together with its pods and barrels.
function place(base, x, y) {
  const dx = delta(x, base.x), dy = delta(y, base.y);
  base.x = wrap(x); base.y = wrap(y);
  for (const pod of base.pods) {
    pod.x = wrap(pod.x + dx); pod.y = wrap(pod.y + dy); pod.tipX = wrap(pod.tipX + dx); pod.tipY = wrap(pod.tipY + dy);
  }
}
// Remove hazards, silence the stations, and park them far from the player's flight path.
function quiet(g) {
  g.enemies = []; g.rocks = []; g.mines = []; g.spawnTimer = 1e8;
  g.bases.forEach((base, i) => {
    place(base, g.player.x + WORLD / 2, g.player.y + (i - 1) * 900);
    base.hatchTimer = 1e8;
    for (const pod of base.pods) pod.shotTimer = 1e8;
  });
}
function advance(g, seconds, input = {}) { for (let n = 0; n < Math.round(seconds * 60); n++) g.step(1 / 60, input); }
function clearBases(g) { for (const base of g.bases) { for (const pod of base.pods) g.hitBase(base, pod, 100); g.hitBase(base, null, 100); } g.step(.016); }
const volleys = g => g.drainEvents().filter(event => event.type === 'shot').length;

test('Event Horizon requires clearing ten sectors in one run and allows endless continuation', () => {
  const g = game();
  for (let sector = 1; sector <= REWARD_SECTOR; sector++) {
    assert.equal(g.wave, sector);
    assert.equal(hasEventHorizon({ clearedSector: g.clearedSector, sector: g.wave }), false);
    quiet(g); clearBases(g);
    assert.equal(g.clearedSector, sector);
    assert.equal(hasEventHorizon({ clearedSector: g.clearedSector }), sector === REWARD_SECTOR);
    const salvage = g.salvage;
    g.finishSector();
    assert.equal(g.salvage, salvage, 'a completed sector cannot grant duplicate salvage');
    assert.ok(g.chooseUpgrade(g.choices[0]));
  }
  assert.equal(g.wave, 11);
  assert.equal(g.mode, 'playing');
  assert.equal(g.clearedSector, 10);
});

test('death in Sector 10 does not unlock Event Horizon and new runs reset completion', () => {
  const g = game();
  for (let i = 1; i < REWARD_SECTOR; i++) {
    quiet(g); clearBases(g); g.chooseUpgrade(g.choices[0]);
  }
  g.player.invulnerable = 0;
  g.damagePlayer(1e6);
  assert.equal(g.mode, 'dead');
  assert.equal(hasEventHorizon({ sector: g.wave, clearedSector: g.clearedSector }), false);
  assert.equal(game().clearedSector, 0);
});

test('reward survives save and purchase; old records migrate only when clearance is provable', () => {
  assert.equal(hasEventHorizon(null), false);
  assert.equal(hasEventHorizon({ sector: 10 }), false);
  assert.equal(hasEventHorizon({ sector: 11 }), true);
  assert.equal(hasEventHorizon({ clearedSector: '10' }), false);
  assert.equal(hasEventHorizon({ clearedSector: Infinity }), false);
  const profile = cleanProfile({ clearedSector: 10, sector: 10, salvage: 1000 });
  assert.ok(purchase(profile, 'armor'));
  assert.equal(hasEventHorizon(cleanProfile(JSON.parse(JSON.stringify(profile)))), true);
});

test('flight never stops, steering is frame-rate independent, and the sector wraps', () => {
  const a = game(), b = game(), c = game(), d = game(); quiet(a); quiet(b); quiet(c); quiet(d);
  const origin = { ...a.player };
  advance(a, 2);
  assert.ok(distance(a.player, origin) > 450, 'the ship keeps flying with no input');
  assert.ok(Math.abs(Math.hypot(a.player.vx, a.player.vy) - a.speed) < 1);
  advance(b, 2, { x: 1 }); for (let i = 0; i < 60; i++) c.step(1 / 30, { x: 1 });
  assert.ok(Math.abs(delta(b.player.x, c.player.x)) < .01 && Math.abs(delta(b.player.y, c.player.y)) < .01);
  assert.ok(Math.abs(b.player.angle) < .01, 'the ship turns to face the stick');
  advance(d, 2, { x: 1, y: 1 });
  assert.ok(Math.abs(Math.hypot(d.player.vx, d.player.vy) - Math.hypot(b.player.vx, b.player.vy)) < 1, 'diagonals are not faster');
  b.player.x = WORLD - 1; advance(b, .2, { x: 1 });
  assert.ok(b.player.x < 100);
  assert.equal(delta(2, WORLD - 2), 4);
});

test('steering reaches arbitrary headings and holds its course after release', () => {
  for (const heading of [.12, .41, 1.22, 2.61, -2.75]) {
    const g = game(); quiet(g);
    advance(g, 1, { x: Math.cos(heading), y: Math.sin(heading) });
    assert.ok(Math.abs(angleDiff(g.player.angle, heading)) < .001, 'reach the exact analog heading');
    const released = g.player.angle, origin = { ...g.player };
    advance(g, .5);
    assert.equal(g.player.angle, released, 'release keeps the heading instead of snapping to a preset');
    assert.ok(distance(origin, g.player) > 130, 'the ship cruises on the released heading');
    assert.equal(g.shots.length, 0, 'steering never pulls the trigger');
  }
});

test('small course corrections ease into place and take the shortest turn across the angle seam', () => {
  const g = game(); quiet(g);
  const start = Math.PI - .04, goal = -Math.PI + .12;
  g.player.angle = start;
  const input = { x: Math.cos(goal), y: Math.sin(goal) };
  g.step(1 / 60, input);
  const firstTurn = angleDiff(start, g.player.angle);
  assert.ok(firstTurn > 0 && firstTurn < .08, 'a small correction begins gently in the shortest direction');
  let last = g.player.angle, lastTurn = firstTurn;
  for (let i = 0; i < 40; i++) {
    g.step(1 / 60, input);
    const turn = angleDiff(last, g.player.angle);
    assert.ok(turn > 0 && turn < lastTurn, 'the turn eases down without snapping, overshooting, or reversing');
    last = g.player.angle; lastTurn = turn;
  }
  assert.ok(Math.abs(angleDiff(g.player.angle, goal)) < .001);
});

test('pause freezes combat, movement, and threat until resume', () => {
  const g = game(); advance(g, .5, { x: 1, fire: true }); g.pause();
  const before = JSON.stringify(g); advance(g, 2, { x: -1, fire: true, pulse: true });
  assert.equal(JSON.stringify(g), before); g.resume(); advance(g, .5, { y: 1 });
  assert.ok(g.time > .9); assert.equal(g.mode, 'playing');
});

test('cannons fire only on the trigger, holding repeats, and a fresh tap fires sooner', () => {
  const g = game(); quiet(g);
  advance(g, 1); g.drainEvents();
  assert.equal(g.shots.length, 0, 'no trigger, no fire');
  g.step(1 / 60, { fire: true });
  assert.equal(g.shots.length, 2, 'fore and aft cannons fire on the first frame');
  assert.ok(g.shots.some(shot => Math.cos(shot.angle - g.player.angle) > .9));
  assert.ok(g.shots.some(shot => Math.cos(shot.angle - g.player.angle) < -.9));
  advance(g, 1, { fire: true });
  const held = volleys(g);
  assert.ok(held >= 5 && held <= 7, 'held trigger fired ' + held + ' volleys in a second');
  advance(g, 1); assert.equal(volleys(g), 0, 'releasing the trigger stops fire');

  const hold = game(); quiet(hold);
  for (let i = 0; i < 8; i++) hold.step(1 / 60, { fire: true });
  const tap = game(); quiet(tap);
  tap.step(1 / 60, { fire: true });
  for (let i = 0; i < 6; i++) tap.step(1 / 60, {});
  tap.step(1 / 60, { fire: true });
  assert.equal(volleys(hold), 1);
  assert.equal(volleys(tap), 2, 'a fresh press cuts the cooldown');
});

test('front and rear cannon shots sweep across the world seam', () => {
  const g = game(); g.player.x = 10; g.player.y = 800; g.player.angle = 0; quiet(g);
  g.enemies = [{ x: WORLD - 110, y: 800, hp: 1, angle: 0, phase: 0, kind: 'chaser', shotTimer: 99, dead: false, formation: 0 }];
  g.fire(); assert.equal(g.shots.length, 2);
  assert.ok(g.shots.some(s => s.vx > 0)); assert.ok(g.shots.some(s => s.vx < 0));
  g.step(.04); assert.equal(g.kills, 1); assert.ok(g.score >= 100);
});

test('six pods protect the core until they fall or a shot finds the open hatch', () => {
  const g = game(); quiet(g); const base = g.bases[0], hp = base.hp;
  g.hitBase(base, null, 1000); assert.equal(base.hp, hp); assert.equal(base.dead, false);
  for (const pod of base.pods.slice(0, 5)) g.hitBase(base, pod, 100);
  g.hitBase(base, null, 1000); assert.equal(base.hp, hp);
  g.hitBase(base, base.pods[5], 100); g.hitBase(base, null, 1000); assert.equal(base.dead, true);
  const armed = g.bases[1]; armed.hatch = 1; const score = g.score; g.drainEvents();
  assert.equal(g.hitBase(armed, null, 1), true); assert.equal(armed.dead, true);
  assert.ok(armed.pods.every(pod => pod.hp > 0)); assert.ok(g.score - score >= 1500);
  assert.ok(g.drainEvents().some(event => event.type === 'base' && event.direct));
  clearBases(g); assert.equal(g.mode, 'upgrade'); assert.equal(g.remaining, 0);
  assert.equal(g.choices.length, 3); assert.equal(new Set(g.choices).size, 3);
  assert.ok(g.salvage >= 300); assert.ok(g.score > 4500);
  assert.equal(g.chooseUpgrade('unknown'), false);
  const chosen = g.choices[0]; assert.equal(g.chooseUpgrade(chosen), true);
  assert.equal(g.upgrades[chosen], 1); assert.equal(g.wave, 2); assert.equal(g.mode, 'playing');
  assert.equal(g.player.shield, g.maxShield); assert.equal(g.shots.length, 0);
});

test('station hatches cycle open and closed', () => {
  const g = game(); quiet(g); const base = g.bases[0]; base.hatchTimer = .5;
  let opened = false, closedAgain = false;
  for (let i = 0; i < 240; i++) {
    g.step(1 / 60);
    if (base.hatch === 1) opened = true;
    if (opened && base.hatchState === 'closed') closedAgain = true;
  }
  assert.ok(opened && closedAgain);
});

test('pod cannons fire from their barrels at a ship in front of them and stop when destroyed', () => {
  const g = game(); quiet(g); const base = g.bases[0], pod = base.pods[0];
  g.player.x = wrap(pod.tipX + Math.cos(pod.aim) * 300); g.player.y = wrap(pod.tipY + Math.sin(pod.aim) * 300);
  g.player.angle = pod.aim; g.player.vx = g.player.vy = 0; g.player.invulnerable = 10;
  pod.shotTimer = 0; g.step(1 / 60);
  const shot = g.shots.find(item => item.owner === 'enemy');
  assert.ok(shot, 'the facing pod fired');
  assert.ok(Math.hypot(delta(shot.x, pod.tipX), delta(shot.y, pod.tipY)) < 20, 'the shot leaves the barrel tip');
  g.shots = []; pod.hp = 0; pod.shotTimer = 0;
  advance(g, .5); assert.equal(g.shots.filter(item => item.owner === 'enemy').length, 0);
});

test('cosmo-mines chain-detonate and wreck nearby ships', () => {
  const g = game(); quiet(g); const p = g.player;
  const first = { id: 901, x: wrap(p.x + 500), y: p.y, phase: 0, dead: false };
  const second = { id: 902, x: wrap(p.x + 620), y: p.y, phase: 0, dead: false };
  g.mines = [first, second];
  g.enemies = [{ ...g.makeEnemy('chaser', wrap(p.x + 700), p.y, 0), shotTimer: 99 }];
  g.detonateMine(first); advance(g, .5);
  assert.ok(first.dead && second.dead, 'the blast set off the second mine');
  assert.equal(g.enemies.length, 0); assert.equal(g.kills, 1);
  assert.equal(g.player.hull, g.maxHull, 'distant blasts do not hurt the player');
});

test('a formation flies in a V behind its leader and scatters when the leader falls', () => {
  const g = game(); quiet(g);
  g.spawnFormation(wrap(g.player.x + 600), g.player.y, Math.PI);
  assert.equal(g.enemies.length, 5); assert.equal(g.enemies.filter(enemy => enemy.leader).length, 1);
  const leader = g.enemies.find(enemy => enemy.leader);
  advance(g, .3);
  for (const wing of g.enemies.filter(enemy => !enemy.leader)) assert.ok(distance(wing, leader) < 300);
  assert.equal(g.condition, 'YELLOW');
  const score = g.score; g.killEnemy(leader);
  assert.ok(g.score - score >= 400, 'leader bonus');
  assert.ok(g.enemies.every(enemy => enemy.dead || enemy.formation === 0));
});

test('condition escalates from green to yellow to red', () => {
  const g = game(); quiet(g);
  advance(g, 1); assert.equal(g.condition, 'GREEN'); g.drainEvents();
  g.sectorTime = 60; g.step(1 / 60); assert.equal(g.condition, 'YELLOW');
  g.sectorTime = 110; g.step(1 / 60); assert.equal(g.condition, 'RED');
  assert.deepEqual(g.drainEvents().filter(event => event.type === 'condition').map(event => event.level), ['YELLOW', 'RED']);
});

test('shield absorbs damage, invulnerability prevents duplicate hits, and death is terminal', () => {
  const g = game(); quiet(g); g.player.invulnerable = 0;
  g.damagePlayer(30); assert.equal(g.player.shield, 20); assert.equal(g.player.hull, 100);
  g.damagePlayer(30); assert.equal(g.player.shield, 20);
  g.player.invulnerable = 0; g.damagePlayer(50); assert.equal(g.player.hull, 70); assert.equal(g.player.shield, 0);
  advance(g, 4); assert.ok(g.player.shield > 0);
  g.player.invulnerable = 0; g.damagePlayer(1000); assert.equal(g.mode, 'dead'); assert.equal(g.player.hull, 0);
  const time = g.time; advance(g, 5); assert.equal(g.time, time); assert.equal(g.pulse(), false);
});

test('pulse clears nearby bullets and enemies, respects range, and consumes one charge', () => {
  const g = game(); quiet(g);
  g.enemies = [{ x: g.player.x + 100, y: g.player.y, kind: 'chaser', hp: 2, dead: false }, { x: g.player.x + 800, y: g.player.y, kind: 'chaser', hp: 2, dead: false }];
  g.shoot(g.player.x + 30, g.player.y, 0, 200, 'enemy');
  g.shoot(g.player.x + 900, g.player.y, 0, 200, 'enemy');
  assert.equal(g.pulse(), true); assert.equal(g.player.pulses, 1); assert.equal(g.kills, 1);
  assert.equal(g.enemies[1].dead, false); assert.equal(g.shots.length, 1);
  g.player.pulses = 0; assert.equal(g.pulse(), false);
});

test('boost is faster, drains energy, and recharges after release', () => {
  const a = game(), b = game(); quiet(a); quiet(b); const start = { ...a.player };
  advance(a, 1, { x: 1 }); advance(b, 1, { x: 1, boost: true });
  assert.ok(distance(b.player, start) > distance(a.player, start) * 1.7);
  assert.ok(b.player.energy < 65); const energy = b.player.energy; advance(b, 1);
  assert.ok(b.player.energy > energy); assert.equal(b.player.boosting, false);
});

test('salvage purchase requires funds, respects caps, and improves a new ship', () => {
  const p = cleanProfile(null); assert.equal(purchase(p, 'armor'), false);
  p.salvage = 200; assert.equal(purchase(p, 'armor'), true); assert.equal(p.salvage, 20);
  assert.equal(p.hangar.armor, 1); assert.equal(hangarCost('armor', 1), 297);
  assert.equal(new Game({ hangar: p.hangar }).maxHull, 115);
  p.salvage = 200; assert.equal(purchase(p, 'cannons'), true);
  assert.ok(new Game({ hangar: p.hangar }).fireDelay < new Game().fireDelay);
  p.hangar.armor = 5; p.salvage = 99999;
  assert.equal(purchase(p, 'armor'), false); assert.equal(purchase(p, 'anything'), false);
});

test('saved state is bounded and malformed records cannot poison the simulation', () => {
  const p = cleanProfile({ salvage: -10, best: Infinity, sector: 10000, sound: 'yes', autofire: true, hangar: { armor: 1e50, reactor: -99, drive: '4', cannons: 9 } });
  assert.equal(p.salvage, 0); assert.equal(p.best, 0); assert.equal(p.sector, 999);
  assert.deepEqual(p.hangar, { armor: 5, reactor: 0, drive: 0, cannons: 4 }); assert.equal(p.sound, true);
  assert.equal(cleanProfile(null).sound, true);
  assert.equal(cleanProfile({ sound: false }).sound, false);
  assert.equal('autofire' in p, false, 'constant fire is gone; old records cannot turn it back on');
});

test('maxed equipment always has a usable field-service choice', () => {
  const g = game(); quiet(g); for (const item of UPGRADES) g.upgrades[item.id] = item.max;
  g.player.hull = 1; clearBases(g); assert.deepEqual(g.choices, ['repair']);
  const salvage = g.salvage; assert.equal(g.chooseUpgrade('repair'), true);
  assert.equal(g.player.hull, g.maxHull); assert.ok(g.salvage > salvage);
});

test('a simultaneous lethal hit and last-base destruction cannot revive the run', () => {
  const g = game(); quiet(g); g.player.invulnerable = 0; g.damagePlayer(1000);
  clearBases(g); assert.equal(g.mode, 'dead'); assert.deepEqual(g.choices, []);
});

test('long play stays finite, limits pursuers, and raises the condition', () => {
  const g = game(); g.player.invulnerable = 1e9;
  for (let i = 0; i < 8000; i++) { g.step(1 / 60, { x: Math.cos(i / 100), y: Math.sin(i / 100), fire: i % 20 < 10 }); g.drainEvents(); }
  assert.ok(Number.isFinite(g.player.x + g.player.y + g.score)); assert.ok(g.enemies.length <= 30);
  assert.ok(g.shots.length < 150); assert.equal(g.condition, 'RED'); assert.equal(g.mode, 'playing');
});
