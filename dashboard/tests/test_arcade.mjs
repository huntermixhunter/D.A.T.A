import test from 'node:test';
import assert from 'node:assert/strict';
import { Game, WORLD, delta, distance, wrap, angleDiff, cleanProfile, purchase, hangarCost, UPGRADES, DIFFICULTIES, DEFAULT_DIFFICULTY,
  BOSS_EVERY, LASER_BLUE_AT, SPAWN_RADIUS, CAMERA_LEAD, SHIP } from '../arcade/engine.mjs';
import { VIEW_SPAN, zoomFor } from '../arcade/renderer.mjs';

function seeded() {
  let seed = 123456;
  return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
}
function game() { const g = new Game({ random: seeded() }); g.start(); return g; }
// A run that starts in a chosen sector at a chosen difficulty.
function sector(difficulty, wave) { const g = new Game({ random: seeded(), difficulty }); g.wave = wave; g.start(); return g; }
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
// Hold the dreadnought's weapons and keep the player out of harm's way.
function calm(g) {
  g.enemies = []; g.rocks = []; g.mines = []; g.spawnTimer = 1e8; g.player.invulnerable = 1e9;
  const boss = g.boss; boss.ringTimer = boss.launchTimer = boss.coreTimer = 1e9;
  for (const gun of boss.guns) gun.timer = 1e9;
}
// Fire one player round along +x so it passes through the target point.
function strike(g, target, damage = 1) {
  const x = wrap(target.x - 330), y = target.y;
  g.shots.push({ x, y, oldX: x, oldY: y, vx: 1000, vy: 0, stepX: 0, stepY: 0, life: .8, owner: 'player', damage, kind: 'bolt', color: 'red', angle: 0, dead: false });
  g.updateShots(.4);
  g.shots = g.shots.filter(shot => !shot.dead);
}
function advance(g, seconds, input = {}) { for (let n = 0; n < Math.round(seconds * 60); n++) g.step(1 / 60, input); }
function clearBases(g) { for (const base of g.bases) { for (const pod of base.pods) g.hitBase(base, pod, 100); g.hitBase(base, null, 100); } g.step(.016); }
const volleys = g => g.drainEvents().filter(event => event.type === 'shot').length;
const LEVELS = ['cadet', 'pilot', 'veteran', 'ace'];
const g0 = difficulty => new Game({ difficulty });

test('difficulty defaults to Pilot, saves with the profile, and rejects unknown levels', () => {
  assert.deepEqual(DIFFICULTIES.map(level => level.id), LEVELS);
  assert.equal(DEFAULT_DIFFICULTY, 'pilot');
  assert.equal(cleanProfile(null).difficulty, 'pilot');
  assert.equal(cleanProfile({ difficulty: 'ace' }).difficulty, 'ace');
  assert.equal(cleanProfile(JSON.parse(JSON.stringify(cleanProfile({ difficulty: 'veteran' })))).difficulty, 'veteran');
  for (const bad of ['godmode', 'ACE', '', 7, { id: 'ace' }, null]) assert.equal(cleanProfile({ difficulty: bad }).difficulty, 'pilot');
  assert.equal(new Game().difficulty.id, 'pilot');
  assert.equal(new Game({ difficulty: 'nope' }).difficulty.id, 'pilot');
});

test('Pilot keeps the opening sector exactly as before', () => {
  const g = game();
  assert.equal(g.threat, 0); assert.equal(g.bases.length, 3); assert.equal(g.boss, null);
  assert.ok(g.bases.every(base => base.maxHp === 10 && base.pods.every(pod => pod.maxHp === 3)));
  assert.equal(g.makeEnemy('chaser', 0, 0, 0).hp, 1); assert.equal(g.makeEnemy('gunner', 0, 0, 0).hp, 2);
  assert.equal(g.enemyDamage(14), 14); assert.equal(g.enemyDamage(28), 28);
  assert.equal(g.cannonSpeed, 299); assert.equal(g.plasmaSpeed, 338); assert.equal(g.pace, 250);
  assert.equal(g.maxEnemies, 24); assert.equal(g.addScore(100), 100);
});

test('harder difficulties field tougher, faster, deadlier enemies and pay out more, with the same ship', () => {
  for (const wave of [1, 4, 9, 14]) {
    const games = LEVELS.map(id => sector(id, wave));
    for (let i = 1; i < games.length; i++) {
      const [easier, harder] = [games[i - 1], games[i]];
      assert.ok(harder.enemyDamage(14) > easier.enemyDamage(14), 'damage rises with difficulty in sector ' + wave);
      assert.ok(harder.pace > easier.pace, 'fighters fly faster');
      assert.ok(harder.cannonSpeed > easier.cannonSpeed, 'cannon rounds travel faster');
      assert.ok(harder.bases[0].pods[0].maxHp >= easier.bases[0].pods[0].maxHp, 'pods are at least as tough');
      assert.ok(harder.bases[0].maxHp > easier.bases[0].maxHp, 'cores are tougher');
      assert.ok(harder.bases.length >= easier.bases.length, 'at least as many stations');
      assert.ok(harder.maxEnemies >= easier.maxEnemies, 'more fighters can be active');
    }
  }
  const ships = LEVELS.map(id => new Game({ difficulty: id, hangar: { armor: 2, cannons: 1 } }));
  for (const ship of ships) {
    assert.equal(ship.maxHull, ships[1].maxHull); assert.equal(ship.maxShield, ships[1].maxShield);
    assert.equal(ship.speed, ships[1].speed); assert.equal(ship.fireDelay, ships[1].fireDelay);
  }
  const scores = LEVELS.map(id => new Game({ difficulty: id }).addScore(100));
  assert.deepEqual(scores, [75, 100, 150, 200]);
  const salvage = LEVELS.map(id => { const g = new Game({ difficulty: id }); g.reward(100); return g.salvage; });
  assert.deepEqual(salvage, [100, 100, 125, 150]);
});

test('every difficulty keeps escalating deep into the patrol', () => {
  for (const id of LEVELS) {
    const early = sector(id, 2), late = sector(id, 14), later = sector(id, 24);
    assert.ok(late.pace > early.pace && later.pace >= late.pace, id + ' fighters speed up to a cap the boost can still escape');
    assert.ok(later.pace < g0(id).speed * 1.9, id + ' boost outruns the fastest fighters');
    for (const [a, b] of [[early, late], [late, later]]) {
      assert.ok(b.enemyDamage(14) > a.enemyDamage(14), id + ' damage keeps rising');
      assert.ok(b.bases[0].maxHp > a.bases[0].maxHp, id + ' cores keep toughening');
      assert.ok(b.bases[0].pods[0].maxHp > a.bases[0].pods[0].maxHp, id + ' pods keep toughening');
      assert.ok(b.makeEnemy('chaser', 0, 0, 0).hp >= a.makeEnemy('chaser', 0, 0, 0).hp, id + ' fighters do not soften');
    }
  }
  // Pilot no longer flattens out: Sector 14 is far tougher than the opening.
  const pilot = sector('pilot', 14);
  assert.ok(pilot.bases[0].pods[0].maxHp >= 9 && pilot.bases[0].maxHp >= 50, 'late stations take real firepower');
  assert.ok(pilot.enemyDamage(14) >= 20 && pilot.makeEnemy('chaser', 0, 0, 0).hp >= 5);
  assert.ok(pilot.pace > 390, 'late fighters outrun the old speed cap');
});

test('every fifth sector is a dreadnought fight instead of stations', () => {
  assert.equal(BOSS_EVERY, 5);
  for (const wave of [1, 2, 4, 6, 9, 11, 14]) {
    const g = sector('pilot', wave);
    assert.equal(g.boss, null, 'no dreadnought in sector ' + wave); assert.ok(g.bases.length >= 3);
  }
  for (const wave of [5, 10, 15]) {
    const g = sector('pilot', wave), events = g.drainEvents();
    assert.ok(g.boss, 'dreadnought in sector ' + wave); assert.equal(g.bases.length, 0); assert.equal(g.remaining, 1);
    assert.equal(g.boss.level, wave / 5); assert.equal(g.boss.guns.length, wave === 5 ? 4 : 6); assert.equal(g.boss.phase, 1);
    assert.ok(distance(g.boss, g.player) >= 1490, 'it arrives from beyond the view');
    assert.ok(events.some(event => event.type === 'boss' && event.level === wave / 5));
    assert.equal(g.condition, 'GREEN'); g.step(1 / 60); assert.equal(g.condition, 'YELLOW', 'a dreadnought raises the alert');
  }
  const first = sector('pilot', 5).boss, second = sector('pilot', 10).boss, aceFirst = sector('ace', 5).boss;
  assert.ok(second.maxHp > first.maxHp && second.guns[0].maxHp > first.guns[0].maxHp, 'later dreadnoughts are tougher');
  assert.ok(aceFirst.maxHp > first.maxHp && aceFirst.guns[0].maxHp > first.guns[0].maxHp, 'harder difficulties build tougher ones');
});

test('batteries power the dreadnought shield; the hull only takes damage once they fall', () => {
  const g = sector('pilot', 5), boss = g.boss; calm(g);
  boss.angle = 0; g.placeBoss(boss); g.drainEvents();
  const hp = boss.hp;
  strike(g, boss);
  assert.equal(boss.hp, hp, 'the shield deflects hull hits');
  assert.ok(g.drainEvents().some(event => event.type === 'deflect' && event.kind === 'shield'));
  for (const gun of boss.guns.slice(0, -1)) g.hitGun(boss, gun, 1e3);
  strike(g, boss);
  assert.equal(boss.phase, 1); assert.equal(boss.hp, hp, 'one battery still holds the shield');
  const last = boss.guns.at(-1), gunHp = last.hp;
  strike(g, last, 2);
  assert.equal(last.hp, gunHp - 2, 'shots that reach a battery damage it');
  g.hitGun(boss, last, 1e3); g.drainEvents();
  g.step(1 / 60);
  assert.equal(boss.phase, 2); assert.equal(g.condition, 'RED');
  strike(g, boss, 5);
  assert.equal(boss.hp, hp - 5, 'the exposed hull takes damage');
  g.player.x = wrap(boss.x + 150); g.player.y = boss.y; g.player.pulses = 1;
  assert.ok(g.pulse()); assert.equal(boss.hp, hp - 17, 'a pulse hits the exposed hull');
  const score = g.score, salvage = g.salvage;
  g.damageBoss(boss, 1e6);
  assert.equal(boss.dead, true); assert.equal(g.bossKills, 1);
  assert.ok(g.drainEvents().some(event => event.type === 'bossDown'));
  g.step(1 / 60);
  assert.equal(g.mode, 'upgrade'); assert.equal(g.remaining, 0); assert.equal(g.clearedSector, 5);
  assert.ok(g.score - score >= 5000, 'a dreadnought is worth at least 5,000');
  assert.ok(g.salvage - salvage >= 400, 'and pays out a large salvage bonus');
  g.player.hull = 7;
  assert.ok(g.chooseUpgrade(g.choices[0]));
  assert.equal(g.player.hull, g.maxHull, 'destroying a dreadnought earns a full repair');
  assert.equal(g.wave, 6); assert.equal(g.boss, null); assert.ok(g.bases.length >= 3);
});

test('the dreadnought charges before each plasma ring, fires its batteries, and launches fighters', () => {
  const g = sector('pilot', 5), boss = g.boss; calm(g);
  // Far enough away that the ring cannot reach the ship during the check.
  g.player.x = wrap(boss.x + 2200); g.player.y = boss.y;
  boss.ringTimer = .001;
  g.step(1 / 60);
  assert.ok(g.drainEvents().some(event => event.type === 'bossCharge'), 'the charge-up is announced');
  assert.ok(boss.charge > 0); assert.equal(g.shots.length, 0, 'no plasma until the charge completes');
  advance(g, .5);
  assert.equal(g.shots.length, 0, 'still charging');
  advance(g, .5);
  const ring = g.drainEvents().find(event => event.type === 'bossRing');
  assert.ok(ring, 'the ring fires once charged');
  const plasma = g.shots.filter(shot => shot.owner === 'enemy' && shot.kind === 'plasma');
  assert.equal(plasma.length, ring.count); assert.equal(ring.count, 18);
  assert.equal(boss.charge, 0); assert.ok(boss.ringTimer > 2);

  g.shots = []; boss.ringTimer = 1e9; boss.launchTimer = .001;
  g.step(1 / 60);
  assert.equal(g.enemies.length, 3, 'a level-one dreadnought launches three fighters');
  assert.ok(g.drainEvents().some(event => event.type === 'launch'));
  assert.ok(g.enemies.every(enemy => distance(enemy, boss) < 260), 'from its rear bay');

  g.enemies = []; boss.launchTimer = 1e9;
  g.player.x = wrap(boss.x + 600); g.player.y = boss.y;
  for (const gun of boss.guns) gun.timer = .001;
  g.step(1 / 60);
  assert.equal(g.shots.filter(shot => shot.kind === 'cannon').length, 4, 'every battery fires when in range');
});

test('ramming the dreadnought bounces the ship and hurts', () => {
  const g = sector('pilot', 5), boss = g.boss; calm(g); g.player.invulnerable = 0;
  boss.angle = 0; g.placeBoss(boss);
  g.player.x = wrap(boss.x - 230); g.player.y = boss.y; g.player.angle = 0; g.player.vx = g.speed; g.player.vy = 0;
  const hull = g.player.hull + g.player.shield;
  advance(g, .4, { x: 1 });
  assert.ok(g.player.hull + g.player.shield < hull, 'the collision hurts');
  assert.ok(distance(g.player, boss) > 130, 'the ship is pushed back out');
});

test('cannons fire red lasers until two weapon upgrades are installed, then blue', () => {
  assert.equal(LASER_BLUE_AT, 2);
  const g = game(); quiet(g);
  g.step(1 / 60, { fire: true });
  assert.ok(g.shots.length >= 2 && g.shots.every(shot => shot.color === 'red'));
  assert.equal(g.drainEvents().find(event => event.type === 'shot').laser, 'red');
  assert.equal(new Game({ hangar: { cannons: 4 } }).laser, 'red', 'every run starts red, whatever the hangar');
  const install = id => { g.mode = 'upgrade'; g.choices = [id]; assert.ok(g.chooseUpgrade(id)); quiet(g); };
  install('rapid');
  assert.equal(g.laser, 'red', 'one weapon upgrade is not enough');
  install('hull');
  assert.equal(g.laser, 'red', 'defense upgrades do not count');
  assert.ok(!g.drainEvents().some(event => event.type === 'laser'));
  install('power');
  assert.equal(g.laser, 'blue');
  assert.deepEqual(g.drainEvents().filter(event => event.type === 'laser').map(event => event.color), ['blue']);
  advance(g, .5);
  g.step(1 / 60, { fire: true });
  const shots = g.shots.filter(shot => shot.owner === 'player');
  assert.ok(shots.length >= 2 && shots.every(shot => shot.color === 'blue'));
  assert.ok(g.shots.every(shot => shot.owner === 'player' || shot.color === ''), 'enemy fire keeps its own colors');
});

test('the zoomed-out view sees further, and fighters still spawn beyond its corners', () => {
  assert.equal(VIEW_SPAN, 2000);
  for (const [w, h] of [[1920, 1018], [1440, 838], [1280, 738], [1000, 638], [390, 790], [320, 790]]) {
    const zoom = zoomFor(w, h), oldZoom = Math.min(Math.max(Math.min(w, h * 1.78) / 1600, .42), 2.4);
    const reach = Math.hypot(w, h) / zoom / 2, g = new Game();
    g.viewRadius = reach;
    assert.ok(w / zoom > w / oldZoom * 1.15, 'more of the sector is visible at ' + w + 'x' + h);
    // The camera can run ahead of the ship by its full-boost look-ahead, and the widest fighter is 112 across.
    const clears = () => g.spawnRadius >= reach + g.speed * SHIP.boost * CAMERA_LEAD + 56;
    assert.ok(clears(), 'spawns stay beyond the corners while boosting at ' + w + 'x' + h);
    g.upgrades.engine = 3;
    assert.ok(clears(), 'a fully upgraded drive pushes the ring out with the camera at ' + w + 'x' + h);
    assert.ok(g.spawnRadius >= SPAWN_RADIUS, 'the ring never shrinks below the standard radius');
  }
  const g = game(); quiet(g);
  for (let i = 0; i < 30; i++) g.spawnEnemies();
  assert.ok(g.enemies.length > 0 && g.enemies.every(enemy => distance(enemy, g.player) >= SPAWN_RADIUS - 1));
  g.enemies = []; g.viewRadius = 1600;
  g.spawnEnemies();
  assert.ok(g.enemies.every(enemy => distance(enemy, g.player) >= 1739), 'an ultrawide view pushes the spawn ring out');
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

test('a lethal hit on the tick the dreadnought falls ends the run without clearing the sector', () => {
  for (const bossFirst of [false, true]) {
    const g = sector('pilot', 5), boss = g.boss; calm(g);
    for (const gun of boss.guns) g.hitGun(boss, gun, 1e3);
    g.step(1 / 60); g.drainEvents();
    assert.equal(boss.phase, 2);
    g.player.invulnerable = 0;
    if (bossFirst) g.damageBoss(boss, 1e6);
    g.damagePlayer(1e4);
    if (!bossFirst) g.damageBoss(boss, 1e6);
    g.step(1 / 60);
    const events = g.drainEvents().map(event => event.type), order = bossFirst ? 'dreadnought first' : 'ship first';
    assert.equal(g.mode, 'dead', order); assert.equal(boss.dead, true, order);
    assert.ok(events.includes('death') && events.includes('bossDown'), order);
    assert.ok(!events.includes('clear'), 'no sector clear once the ship is lost (' + order + ')');
    assert.equal(g.clearedSector, 0, order); assert.deepEqual(g.choices, [], order);
  }
});

test('long play stays finite, limits pursuers, and raises the condition', () => {
  const g = game(); g.player.invulnerable = 1e9;
  for (let i = 0; i < 8000; i++) { g.step(1 / 60, { x: Math.cos(i / 100), y: Math.sin(i / 100), fire: i % 20 < 10 }); g.drainEvents(); }
  assert.ok(Number.isFinite(g.player.x + g.player.y + g.score)); assert.ok(g.enemies.length <= 30);
  assert.ok(g.shots.length < 150); assert.equal(g.condition, 'RED'); assert.equal(g.mode, 'playing');
});
