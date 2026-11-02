// Pure simulation shared by the browser and the deterministic gameplay checks.
// World units are measured against the artwork: the player ship is 150 long,
// a fortress is 480 across, and the sector wraps at WORLD on both axes.
export const WORLD = 4800;
export const TAU = Math.PI * 2;
export const wrap = n => ((n % WORLD) + WORLD) % WORLD;
export const delta = (a, b) => ((a - b + WORLD * 1.5) % WORLD) - WORLD / 2;
export const distance = (a, b) => Math.hypot(delta(a.x, b.x), delta(a.y, b.y));
export const clamp = (n, low, high) => Math.min(high, Math.max(low, n));
export const angleDiff = (from, to) => Math.atan2(Math.sin(to - from), Math.cos(to - from));

// Fixed simulation tick. Any frame rate produces the same flight path.
const TICK = 1 / 120;
const MAX_ENEMIES = 24;
const CONDITIONS = ['GREEN', 'YELLOW', 'RED'];
// Fighters arrive from beyond the edge of the zoomed-out view, never on screen.
export const SPAWN_RADIUS = 1350;
// The camera looks ahead of the ship by this many seconds of its velocity.
export const CAMERA_LEAD = .24;
// Every fifth sector is a dreadnought fight.
export const BOSS_EVERY = 5;
// The cannons fire red lasers until this many weapon upgrades are installed in a run.
export const LASER_BLUE_AT = 2;
export const WEAPON_UPGRADES = ['rapid', 'spread', 'power'];

export const SHIP = { radius: 24, nose: 74, tail: 69, turn: 7.5, turnEase: 12, cruise: 300, boost: 1.9 };
export const ENEMIES = {
  chaser: { radius: 30, score: 100, hp: 1 },
  gunner: { radius: 32, score: 180, hp: 2 },
};
// Pod centers and barrel tips, measured from the fortress artwork core.
export const FORTRESS = {
  body: 108, pod: 50, core: 44,
  pods: [
    { x: 152, y: -2, tipX: 235, tipY: -1 },
    { x: 105, y: 145, tipX: 186, tipY: 208 },
    { x: -104, y: 145, tipX: -186, tipY: 208 },
    { x: -153, y: -2, tipX: -235, tipY: -1 },
    { x: -104, y: -146, tipX: -183, tipY: -210 },
    { x: 105, y: -146, tipX: 183, tipY: -210 },
  ],
};
// The dreadnought is the gunship artwork at four times scale. Local units, x points forward.
export const BOSS = {
  width: 460, gun: 30, shield: { rx: 285, ry: 205 },
  core: { x: 70, y: 0 }, bay: { x: -190, y: 0 },
  // Collision circles for the body and the thin crescent blades.
  hull: [
    { x: 150, y: 0, r: 58 }, { x: 45, y: 0, r: 94 }, { x: -80, y: 0, r: 94 },
    { x: -180, y: -122, r: 30 }, { x: -180, y: 122, r: 30 }, { x: -60, y: -134, r: 24 }, { x: -60, y: 134, r: 24 },
  ],
  // Batteries sit on the outer plate edges, so each can be hit from its exposed side.
  // Every dreadnought mounts the first four. From the second one on, all six.
  guns: [{ x: 20, y: -116 }, { x: 20, y: 116 }, { x: -108, y: -108 }, { x: -108, y: 108 }, { x: 142, y: -60 }, { x: 142, y: 60 }],
};

// Difficulty scales enemy toughness, damage, aggression, and numbers, and how quickly later
// sectors escalate. The player's ship is never changed, so hangar upgrades keep their value.
export const DIFFICULTIES = [
  { id: 'cadet', name: 'Cadet', detail: 'Gentler fire, fewer fighters, and slower escalation. Score x0.75.',
    start: 0, growth: .7, hp: .8, damage: .7, fire: .75, speed: .92, bullet: .9, spawn: .8, score: .75, salvage: 1 },
  { id: 'pilot', name: 'Pilot', detail: 'The standard patrol. Every sector hits harder than the last.',
    start: 0, growth: 1, hp: 1, damage: 1, fire: 1, speed: 1, bullet: 1, spawn: 1, score: 1, salvage: 1 },
  { id: 'veteran', name: 'Veteran', detail: 'Tougher, faster enemies that hit harder and escalate quickly. Score x1.5, salvage x1.25.',
    start: 1, growth: 1.4, hp: 1.15, damage: 1.25, fire: 1.25, speed: 1.07, bullet: 1.1, spawn: 1.2, score: 1.5, salvage: 1.25 },
  { id: 'ace', name: 'Ace', detail: 'Relentless from the first sector. Bring an upgraded ship. Score x2, salvage x1.5.',
    start: 2, growth: 1.75, hp: 1.3, damage: 1.45, fire: 1.5, speed: 1.14, bullet: 1.18, spawn: 1.4, score: 2, salvage: 1.5 },
];
export const DEFAULT_DIFFICULTY = 'pilot';
export const difficultyFor = id => DIFFICULTIES.find(item => item.id === id) || DIFFICULTIES.find(item => item.id === DEFAULT_DIFFICULTY);

export const UPGRADES = [
  { id: 'rapid', name: 'Overclocked cannons', tag: 'WEAPONS', detail: 'Fire 20% faster with each level.', max: 4, icon: 'burst' },
  { id: 'spread', name: 'Split shot', tag: 'WEAPONS', detail: 'Add a pair of forward shots. A wider arc, more firepower.', max: 2, icon: 'split' },
  { id: 'power', name: 'Heavy rounds', tag: 'WEAPONS', detail: 'Every shot deals one extra damage.', max: 4, icon: 'bolt' },
  { id: 'hull', name: 'Reinforced hull', tag: 'DEFENSE', detail: 'Add 25 maximum hull and repair 45 hull now.', max: 4, icon: 'hex' },
  { id: 'shield', name: 'Shield amplifier', tag: 'DEFENSE', detail: 'Add 20 shield capacity and recharge faster.', max: 4, icon: 'shield' },
  { id: 'engine', name: 'Ion drive', tag: 'FLIGHT', detail: 'Fly 12% faster. Boost recharges faster.', max: 3, icon: 'arrow' },
  { id: 'magnet', name: 'Salvage tractor', tag: 'UTILITY', detail: 'Double pickup range and earn 25% more salvage.', max: 2, icon: 'orbit' },
  { id: 'pulse', name: 'Pulse capacitor', tag: 'SPECIAL', detail: 'A larger shockwave and one extra pulse each sector.', max: 3, icon: 'ring' },
];

export const HANGAR = [
  { id: 'armor', name: 'Hull plating', system: 'HULL', detail: '+15 starting hull per level', cost: 180, max: 5, icon: 'hex' },
  { id: 'reactor', name: 'Shield reactor', system: 'SHIELDS', detail: '+10 starting shields per level', cost: 180, max: 5, icon: 'shield' },
  { id: 'drive', name: 'Drive tuning', system: 'ENGINES', detail: '+5% flight speed per level', cost: 220, max: 5, icon: 'arrow' },
  { id: 'cannons', name: 'Weapons control', system: 'WEAPONS', detail: '+8% cannon fire rate per level', cost: 200, max: 4, icon: 'burst' },
];
export const hangarCost = (id, level) => Math.round(HANGAR.find(item => item.id === id).cost * (1 + level * .65));

export function cleanProfile(value) {
  const n = (v, max = 1e9) => Number.isFinite(v) ? clamp(Math.floor(v), 0, max) : 0;
  const raw = value && typeof value === 'object' ? value : {};
  return {
    version: 1, salvage: n(raw.salvage), best: n(raw.best), sector: n(raw.sector, 999),
    // Older records tracked sectors reached. Reaching 2 proves that 1 was cleared.
    clearedSector: Math.max(n(raw.clearedSector, 999), n(raw.sector, 999) - 1),
    runs: n(raw.runs), sound: raw.sound !== false, difficulty: difficultyFor(raw.difficulty).id,
    hangar: Object.fromEntries(HANGAR.map(item => [item.id, n(raw.hangar?.[item.id], item.max)])),
  };
}

export function purchase(profile, id) {
  const item = HANGAR.find(upgrade => upgrade.id === id);
  if (!item || profile.hangar[id] >= item.max) return false;
  const cost = hangarCost(id, profile.hangar[id]);
  if (profile.salvage < cost) return false;
  profile.salvage -= cost;
  profile.hangar[id]++;
  return true;
}

function hitsSegment(bullet, target, radius) {
  const x = delta(target.x, bullet.oldX), y = delta(target.y, bullet.oldY);
  const dx = bullet.stepX, dy = bullet.stepY;
  const t = clamp((x * dx + y * dy) / (dx * dx + dy * dy || 1), 0, 1);
  return (x - dx * t) ** 2 + (y - dy * t) ** 2 <= radius ** 2;
}

export class Game {
  constructor({ random = Math.random, hangar = {}, difficulty = DEFAULT_DIFFICULTY } = {}) {
    this.random = random;
    this.hangar = cleanProfile({ hangar }).hangar;
    this.difficulty = difficultyFor(difficulty);
    this.mode = 'ready';
    this.upgrades = Object.fromEntries(UPGRADES.map(item => [item.id, 0]));
    this.wave = 1;
    this.clearedSector = 0;
    this.bossKills = 0;
    this.viewRadius = 0;
    this.score = 0;
    this.salvage = 0;
    this.kills = 0;
    this.time = 0;
    this.sectorTime = 0;
    this.spawnTimer = 2.5;
    this.condition = 'GREEN';
    this.alertRank = 0;
    this.nextId = 1;
    this.events = [];
    this.shots = [];
    this.enemies = [];
    this.pickups = [];
    this.bases = [];
    this.boss = null;
    this.rocks = [];
    this.mines = [];
    this.blasts = [];
    this.choices = [];
    this.combo = 0;
    this.comboTime = 0;
    this.player = { x: WORLD / 2, y: WORLD / 2, vx: 0, vy: 0, angle: -Math.PI / 2,
      hull: this.maxHull, shield: this.maxShield, energy: 100, invulnerable: 2,
      sinceHit: 10, shotTimer: 0, trigger: false, pulses: 2, boosting: false };
  }

  get maxHull() { return 100 + this.hangar.armor * 15 + this.upgrades.hull * 25; }
  get maxShield() { return 50 + this.hangar.reactor * 10 + this.upgrades.shield * 20; }
  get speed() { return SHIP.cruise * (1 + this.hangar.drive * .05 + this.upgrades.engine * .12); }
  get fireDelay() { return .2 * Math.pow(.8, this.upgrades.rapid) / (1 + this.hangar.cannons * .08); }
  get multiplier() { return 1 + Math.min(4, Math.floor(this.combo / 5)); }
  get remaining() { return this.bases.filter(base => !base.dead).length + (this.boss && !this.boss.dead ? 1 : 0); }
  get bossSector() { return this.wave % BOSS_EVERY === 0; }
  get weaponLevel() { return WEAPON_UPGRADES.reduce((sum, id) => sum + this.upgrades[id], 0); }
  get laser() { return this.weaponLevel >= LASER_BLUE_AT ? 'blue' : 'red'; }
  // Escalation: zero in Sector 1 on Pilot, rising every sector at the difficulty's pace.
  get threat() { return this.difficulty.start + (this.wave - 1) * this.difficulty.growth; }
  get maxEnemies() { return Math.round(MAX_ENEMIES * this.difficulty.spawn); }
  get cannonSpeed() { return Math.min(450, 299 + this.threat * 10) * this.difficulty.bullet; }
  get plasmaSpeed() { return Math.min(470, 338 + this.threat * 9) * this.difficulty.bullet; }
  get pace() {
    const t = this.threat, speed = this.difficulty.speed;
    return Math.min(440 * speed, (250 + t * 11) * speed + this.sectorTime * .35);
  }
  // The browser reports how far the view reaches, so wide screens never see fighters appear.
  // The ring also clears the camera's look-ahead at full boost, plus a margin wider than any fighter.
  get spawnRadius() { return Math.max(SPAWN_RADIUS, this.viewRadius + 80 + this.speed * SHIP.boost * CAMERA_LEAD); }
  // Enemy hits grow 3.5% per escalation step on top of the difficulty's damage.
  enemyDamage(base) { return Math.round(base * this.difficulty.damage * (1 + this.threat * .035)); }
  // Hit points grow faster than linearly, so late sectors keep pace with an upgraded ship.
  toughness(base, linear, curve) {
    const t = this.threat;
    return Math.max(1, Math.floor((base + t * linear + t * t / curve) * this.difficulty.hp));
  }

  emit(type, data = {}) { this.events.push({ type, ...data }); }
  drainEvents() { return this.events.splice(0); }
  start() { if (this.mode !== 'ready') return; this.mode = 'playing'; this.spawnSector(); }

  spawnSector() {
    const p = this.player;
    this.sectorTime = 0;
    this.spawnTimer = 2.5;
    this.condition = 'GREEN';
    this.alertRank = 0;
    this.shots = []; this.enemies = []; this.pickups = []; this.bases = []; this.rocks = []; this.mines = []; this.blasts = [];
    this.boss = null;
    const boss = this.bossSector;
    const count = boss ? 0 : Math.min(3 + Math.floor(this.threat / 2), 7);
    const offset = this.random() * TAU;
    for (let i = 0; i < count; i++) {
      let spot = null;
      for (let attempt = 0; attempt < 40 && !spot; attempt++) {
        const angle = offset + (i + this.random() * .6 - .3) * TAU / count;
        const radius = 900 + this.random() * 1150;
        const candidate = { x: wrap(p.x + Math.cos(angle) * radius), y: wrap(p.y + Math.sin(angle) * radius) };
        if (this.bases.every(base => distance(base, candidate) > 820)) spot = candidate;
      }
      const fallback = offset + i * TAU / count;
      spot ||= { x: wrap(p.x + Math.cos(fallback) * 1300), y: wrap(p.y + Math.sin(fallback) * 1300) };
      this.bases.push(this.makeBase(spot.x, spot.y));
    }
    if (boss) this.boss = this.makeBoss();
    for (let i = 0; i < 52; i++) {
      const radius = 22 + Math.pow(this.random(), 1.6) * 40;
      const rock = { id: this.nextId++, x: this.random() * WORLD, y: this.random() * WORLD, radius,
        seed: this.random(), variant: Math.floor(this.random() * 8), hp: radius > 44 ? 4 : radius > 32 ? 3 : 2, dead: false };
      if (distance(rock, p) > 300 && this.bases.every(base => distance(rock, base) > 330)) this.rocks.push(rock);
    }
    const mines = Math.min(Math.round((10 + this.threat * 2) * (boss ? .5 : 1)), 36);
    for (let i = 0; i < mines; i++) {
      const mine = { id: this.nextId++, x: this.random() * WORLD, y: this.random() * WORLD, phase: this.random() * TAU, dead: false };
      if (distance(mine, p) > 340 && this.bases.every(base => distance(mine, base) > 300)) this.mines.push(mine);
    }
    p.invulnerable = 2;
    this.emit('sector', { wave: this.wave, boss });
    if (this.boss) this.emit('boss', { x: this.boss.x, y: this.boss.y, level: this.boss.level });
  }

  makeBase(x, y) {
    // Stations come in two orientations, as in the arcade original.
    const rot = this.random() < .5 ? 0 : Math.PI / 2;
    const cos = Math.cos(rot), sin = Math.sin(rot);
    const turn = (dx, dy) => [dx * cos - dy * sin, dx * sin + dy * cos];
    const podHp = this.toughness(3, .45, 120);
    const pods = FORTRESS.pods.map((spec, index) => {
      const [ox, oy] = turn(spec.x, spec.y), [tx, ty] = turn(spec.tipX, spec.tipY);
      return { index, x: wrap(x + ox), y: wrap(y + oy), tipX: wrap(x + tx), tipY: wrap(y + ty),
        aim: Math.atan2(ty - oy, tx - ox), hp: podHp, maxHp: podHp, shotTimer: (1.5 + this.random() * 3) / this.difficulty.fire };
    });
    const hp = this.toughness(10, 2.5, 20);
    return { id: this.nextId++, x, y, rot, pods, hp, maxHp: hp, dead: false,
      hatch: 0, hatchState: 'closed', hatchTimer: 2.5 + this.random() * 3.5 };
  }

  makeEnemy(kind, x, y, angle, extra = {}) {
    return { id: this.nextId++, kind, x, y, angle, hp: this.toughness(ENEMIES[kind].hp, 1 / 3, 150),
      shotTimer: (1.2 + this.random() * 1.6) / this.difficulty.fire, phase: this.random() * TAU, side: this.random() < .5 ? -1 : 1,
      dead: false, formation: 0, leader: false, ox: 0, oy: 0, ...extra };
  }

  makeBoss() {
    const p = this.player, level = Math.max(1, Math.floor(this.wave / BOSS_EVERY)), t = this.threat, d = this.difficulty;
    const angle = this.random() * TAU, reach = Math.max(1500, this.spawnRadius + 150);
    const gunHp = Math.max(1, Math.floor((16 + level * 18 + t) * d.hp));
    const maxHp = Math.max(1, Math.floor((60 + level * 110 + t * 5) * d.hp));
    const guns = BOSS.guns.slice(0, level >= 2 ? 6 : 4).map((spec, index) => ({ index, x: 0, y: 0, aim: 0,
      hp: gunHp, maxHp: gunHp, timer: (1.6 + this.random() * 2.2) / d.fire }));
    const boss = { id: this.nextId++, level, x: wrap(p.x + Math.cos(angle) * reach), y: wrap(p.y + Math.sin(angle) * reach),
      angle: angle + Math.PI, hp: maxHp, maxHp, phase: 1, guns, core: { x: 0, y: 0 }, hull: BOSS.hull.map(part => ({ x: 0, y: 0, r: part.r })),
      charge: 0, ringTimer: 4.5, launchTimer: 7, coreTimer: 2, side: this.random() < .5 ? -1 : 1, sideTimer: 7, dead: false };
    this.placeBoss(boss);
    return boss;
  }

  // World position of a point given in the dreadnought's own frame.
  bossPoint(boss, local, out = {}) {
    const c = Math.cos(boss.angle), s = Math.sin(boss.angle);
    out.x = wrap(boss.x + local.x * c - local.y * s);
    out.y = wrap(boss.y + local.x * s + local.y * c);
    return out;
  }

  placeBoss(boss) {
    for (const gun of boss.guns) this.bossPoint(boss, BOSS.guns[gun.index], gun);
    this.bossPoint(boss, BOSS.core, boss.core);
    boss.hull.forEach((part, i) => this.bossPoint(boss, BOSS.hull[i], part));
  }

  pause() { if (this.mode === 'playing') { this.mode = 'paused'; this.player.boosting = false; this.player.trigger = false; } }
  resume() { if (this.mode === 'paused') this.mode = 'playing'; }

  addScore(amount) {
    this.combo++;
    this.comboTime = 5;
    const gained = Math.round(amount * this.multiplier * this.difficulty.score);
    this.score += gained;
    return gained;
  }

  reward(amount) { this.salvage += Math.round(amount * (1 + this.upgrades.magnet * .25) * this.difficulty.salvage); }

  damagePlayer(amount) {
    const p = this.player;
    if (this.mode !== 'playing' || p.invulnerable > 0) return false;
    const absorbed = Math.min(p.shield, amount);
    p.shield -= absorbed;
    p.hull = Math.max(0, p.hull - (amount - absorbed));
    p.sinceHit = 0; p.invulnerable = .7;
    this.combo = 0;
    this.emit(absorbed >= amount ? 'shieldHit' : 'hurt', { x: p.x, y: p.y });
    if (!p.hull) {
      this.mode = 'dead'; p.boosting = false; p.trigger = false;
      this.emit('death', { x: p.x, y: p.y, angle: p.angle });
    }
    return true;
  }

  // Bounce off solid structure, then take damage unless still invulnerable.
  collide(obstacle, damage) {
    const p = this.player;
    const dx = delta(p.x, obstacle.x), dy = delta(p.y, obstacle.y), length = Math.hypot(dx, dy) || 1;
    const nx = dx / length, ny = dy / length, inward = p.vx * nx + p.vy * ny;
    if (inward < 0) {
      p.vx -= 1.6 * inward * nx; p.vy -= 1.6 * inward * ny;
      p.angle = Math.atan2(p.vy, p.vx);
    }
    this.damagePlayer(damage);
  }

  shoot(x, y, angle, speed, owner, damage = 1, kind = owner === 'player' ? 'bolt' : 'cannon', carryX = 0, carryY = 0) {
    const vx = Math.cos(angle) * speed + carryX, vy = Math.sin(angle) * speed + carryY;
    this.shots.push({ x: wrap(x), y: wrap(y), oldX: wrap(x), oldY: wrap(y), vx, vy, stepX: 0, stepY: 0,
      life: owner === 'player' ? .8 : 2.6, owner, damage, kind, color: owner === 'player' ? this.laser : '',
      angle: Math.atan2(vy, vx), dead: false });
  }

  fire() {
    const p = this.player, damage = 1 + this.upgrades.power;
    for (let lane = -this.upgrades.spread; lane <= this.upgrades.spread; lane++) {
      const a = p.angle + lane * .13;
      this.shoot(p.x + Math.cos(a) * SHIP.nose, p.y + Math.sin(a) * SHIP.nose, a, 1000, 'player', damage, 'bolt', p.vx, p.vy);
    }
    const back = p.angle + Math.PI;
    this.shoot(p.x + Math.cos(back) * SHIP.tail, p.y + Math.sin(back) * SHIP.tail, back, 900, 'player', damage, 'bolt', p.vx, p.vy);
    p.shotTimer = this.fireDelay;
    this.emit('shot', { x: p.x, y: p.y, angle: p.angle, laser: this.laser });
  }

  killEnemy(enemy, credited = true) {
    if (enemy.dead) return;
    enemy.dead = true;
    let points = 0;
    if (credited) {
      this.kills++;
      points = this.addScore(ENEMIES[enemy.kind].score + (enemy.leader ? 300 : 0));
      this.pickups.push({ id: this.nextId++, x: enemy.x, y: enemy.y, kind: this.random() < .1 ? 'repair' : 'salvage', amount: 12, life: 28 });
      if (enemy.leader) this.emit('leader', { x: enemy.x, y: enemy.y });
    }
    if (enemy.leader) {
      const formation = enemy.formation;
      for (const wing of this.enemies) if (wing.formation === formation) wing.formation = 0;
    }
    this.emit('explosion', { x: enemy.x, y: enemy.y, size: 36, kind: enemy.kind, points });
  }

  hitBase(base, pod, amount) {
    if (base.dead) return false;
    if (pod) {
      if (pod.hp <= 0) return false;
      pod.hp -= amount;
      this.emit('podHit', { x: pod.x, y: pod.y, base: base.id, index: pod.index });
      if (pod.hp <= 0) {
        const points = this.addScore(75);
        this.reward(4);
        this.emit('explosion', { x: pod.x, y: pod.y, size: 46, kind: 'pod', points });
        if (base.pods.every(item => item.hp <= 0)) this.emit('exposed', { x: base.x, y: base.y });
      }
      return true;
    }
    if (base.pods.some(item => item.hp > 0)) {
      // An armored core only yields to a shot through its open hatch.
      if (base.hatch < .55) return false;
      this.destroyBase(base, true);
      return true;
    }
    base.hp -= amount;
    this.emit('coreHit', { x: base.x, y: base.y });
    if (base.hp <= 0) this.destroyBase(base, false);
    return true;
  }

  destroyBase(base, direct) {
    base.dead = true;
    const points = this.addScore(direct ? 1500 : 1000);
    this.reward(direct ? 70 : 55);
    this.pickups.push({ id: this.nextId++, x: base.x, y: base.y, kind: 'repair', amount: 18, life: 40 });
    this.emit('base', { x: base.x, y: base.y, rot: base.rot, id: base.id, direct, points, pods: base.pods.map(pod => pod.hp > 0) });
  }

  // Batteries take hits first. The hull deflects fire until every battery is destroyed.
  hitBoss(shot) {
    const boss = this.boss;
    const gun = boss.guns.find(item => item.hp > 0 && hitsSegment(shot, item, BOSS.gun));
    if (gun) {
      shot.dead = true;
      this.emit('spark', { x: shot.x, y: shot.y, kind: 'pod', laser: shot.color });
      this.hitGun(boss, gun, shot.damage);
      return true;
    }
    if (!boss.hull.some(part => hitsSegment(shot, part, part.r))) return false;
    shot.dead = true;
    if (boss.phase === 1) this.emit('deflect', { x: shot.x, y: shot.y, kind: 'shield' });
    else {
      this.emit('spark', { x: shot.x, y: shot.y, kind: 'boss', laser: shot.color });
      this.damageBoss(boss, shot.damage);
    }
    return true;
  }

  hitGun(boss, gun, amount) {
    if (boss.dead || gun.hp <= 0) return false;
    gun.hp -= amount;
    this.emit('gunHit', { index: gun.index });
    if (gun.hp <= 0) {
      const points = this.addScore(250);
      this.reward(10);
      this.emit('explosion', { x: gun.x, y: gun.y, size: 48, kind: 'pod', points });
      if (boss.guns.every(item => item.hp <= 0)) {
        boss.phase = 2; boss.coreTimer = 1.4; boss.ringTimer = Math.min(boss.ringTimer, 2.5);
        this.emit('bossShield', { x: boss.x, y: boss.y });
      }
    }
    return true;
  }

  damageBoss(boss, amount) {
    if (boss.dead || boss.phase !== 2) return false;
    boss.hp -= amount;
    this.emit('bossHit', {});
    if (boss.hp <= 0) this.destroyBoss(boss);
    return true;
  }

  destroyBoss(boss) {
    boss.dead = true;
    boss.hp = 0;
    boss.charge = 0;
    this.bossKills++;
    const points = this.addScore(5000 * boss.level);
    this.reward(150 + boss.level * 75);
    this.emit('bossDown', { x: boss.x, y: boss.y, angle: boss.angle, level: boss.level, points });
  }

  breakRock(rock, credited = true) {
    if (rock.dead) return;
    rock.dead = true;
    if (credited) this.addScore(25);
    this.emit('explosion', { x: rock.x, y: rock.y, size: rock.radius, kind: 'rock' });
    if (rock.radius <= 40) return;
    for (const side of [-1, 1]) {
      const a = rock.seed * TAU + side * Math.PI / 2, r = rock.radius * .5;
      this.rocks.push({ id: this.nextId++, x: wrap(rock.x + Math.cos(a) * r), y: wrap(rock.y + Math.sin(a) * r),
        radius: r, seed: (rock.seed + .37 * side + 1) % 1, variant: (rock.variant + 3 + side) & 7, hp: 1, dead: false });
    }
  }

  detonateMine(mine, delay = .1) {
    if (mine.dead) return;
    mine.dead = true;
    this.addScore(40);
    this.blasts.push({ x: mine.x, y: mine.y, radius: 170, delay });
  }

  applyBlast(blast) {
    this.emit('explosion', { x: blast.x, y: blast.y, size: 72, kind: 'mine' });
    for (const enemy of this.enemies) if (!enemy.dead && distance(enemy, blast) < blast.radius) this.killEnemy(enemy);
    for (const mine of this.mines) if (!mine.dead && distance(mine, blast) < blast.radius) this.detonateMine(mine, .16);
    for (const rock of [...this.rocks]) if (!rock.dead && distance(rock, blast) < blast.radius + rock.radius * .5) this.breakRock(rock, false);
    for (const base of this.bases) {
      if (!base.dead) for (const pod of base.pods) if (pod.hp > 0 && distance(pod, blast) < blast.radius + 20) this.hitBase(base, pod, 2);
    }
    const boss = this.boss;
    if (boss && !boss.dead) {
      for (const gun of boss.guns) if (gun.hp > 0 && distance(gun, blast) < blast.radius + 20) this.hitGun(boss, gun, 2);
      if (boss.hull.some(part => distance(part, blast) < blast.radius + part.r * .5)) this.damageBoss(boss, 6);
    }
    if (distance(blast, this.player) < blast.radius * .8) this.damagePlayer(this.enemyDamage(28));
  }

  pulse() {
    if (this.mode !== 'playing' || this.player.pulses <= 0) return false;
    const p = this.player;
    p.pulses--;
    const radius = 380 + this.upgrades.pulse * 90;
    p.invulnerable = Math.max(p.invulnerable, 1);
    this.emit('pulse', { x: p.x, y: p.y, radius });
    this.shots = this.shots.filter(shot => shot.owner === 'player' || distance(shot, p) > radius);
    for (const enemy of this.enemies) if (!enemy.dead && distance(enemy, p) < radius) this.killEnemy(enemy);
    for (const mine of this.mines) if (!mine.dead && distance(mine, p) < radius) this.detonateMine(mine);
    for (const base of this.bases) {
      if (base.dead) continue;
      const wasOpen = base.pods.every(pod => pod.hp <= 0);
      for (const pod of base.pods) if (pod.hp > 0 && distance(pod, p) < radius) this.hitBase(base, pod, 4);
      if (wasOpen && distance(base, p) < radius) this.hitBase(base, null, 7);
    }
    const boss = this.boss;
    if (boss && !boss.dead) {
      for (const gun of boss.guns) if (gun.hp > 0 && distance(gun, p) < radius) this.hitGun(boss, gun, 4);
      if (boss.hull.some(part => distance(part, p) < radius + part.r * .5)) this.damageBoss(boss, 12);
    }
    return true;
  }

  // Returns extra seconds to wait before the next spawn.
  spawnEnemies() {
    const p = this.player, t = this.threat;
    if (this.enemies.length >= this.maxEnemies) return 0;
    const angle = this.random() * TAU;
    const reach = this.spawnRadius, x = wrap(p.x + Math.cos(angle) * reach), y = wrap(p.y + Math.sin(angle) * reach);
    const pressure = this.sectorTime > 100 ? 2 : this.sectorTime > 50 ? 1 : 0;
    const odds = t < 1 ? [0, .25, .45][pressure] : [.3, .5, .7][pressure];
    if (this.random() < odds) { this.spawnFormation(x, y, angle + Math.PI); return 3; }
    const count = 1 + (t >= 2 || this.sectorTime > 40 ? 1 : 0) + (t >= 7 ? 1 : 0) + (t >= 14 ? 1 : 0);
    const gunners = t >= 1 ? Math.min(.55, .35 + t * .012) : 0;
    for (let i = 0; i < count && this.enemies.length < this.maxEnemies; i++) {
      const kind = gunners && this.random() < gunners ? 'gunner' : 'chaser';
      const a = angle + (i - (count - 1) / 2) * .25;
      this.enemies.push(this.makeEnemy(kind, wrap(p.x + Math.cos(a) * reach), wrap(p.y + Math.sin(a) * reach), a + Math.PI));
    }
    return 0;
  }

  // A leader with wingmen in a V. Destroying the leader breaks the formation.
  spawnFormation(x, y, heading) {
    const id = this.nextId++, c = Math.cos(heading), s = Math.sin(heading), t = this.threat;
    const slots = [[0, 0], [-82, -80], [-82, 80], [-164, -160], [-164, 160], [-246, -240], [-246, 240]].slice(0, t >= 6 ? 7 : 5);
    for (const [i, [ox, oy]] of slots.entries()) {
      if (this.enemies.length >= this.maxEnemies) break;
      const kind = t >= 3 && i >= 3 ? 'gunner' : 'chaser';
      this.enemies.push(this.makeEnemy(kind, wrap(x + ox * c - oy * s), wrap(y + ox * s + oy * c), heading,
        { formation: id, leader: i === 0, ox, oy }));
    }
    this.emit('formation', { x, y });
  }

  chooseUpgrade(id) {
    if (this.mode !== 'upgrade' || !this.choices.includes(id)) return false;
    const laser = this.laser, afterBoss = this.bossSector;
    if (id === 'repair') { this.player.hull = this.maxHull; this.reward(60); }
    else {
      const item = UPGRADES.find(upgrade => upgrade.id === id);
      if (!item || this.upgrades[id] >= item.max) return false;
      this.upgrades[id]++;
      if (id === 'hull') this.player.hull = Math.min(this.maxHull, this.player.hull + 45);
    }
    // Destroying a dreadnought earns a full repair.
    this.player.hull = afterBoss ? this.maxHull : Math.min(this.maxHull, this.player.hull + 25);
    this.player.shield = this.maxShield;
    this.player.energy = 100;
    this.player.pulses = Math.min(6, this.player.pulses + 1 + this.upgrades.pulse);
    this.player.vx = this.player.vy = 0;
    this.wave++;
    this.mode = 'playing';
    this.choices = [];
    if (this.laser !== laser) this.emit('laser', { color: this.laser });
    this.spawnSector();
    return true;
  }

  finishSector() {
    if (this.mode !== 'playing' || this.remaining !== 0) return;
    const boss = this.bossSector;
    this.clearedSector = this.wave;
    this.reward(75 + this.wave * 15 + (boss ? 100 : 0));
    this.score += Math.round((1500 + Math.max(0, Math.floor(180 - this.sectorTime)) * 10) * this.difficulty.score);
    this.mode = 'upgrade';
    this.player.boosting = false;
    this.player.trigger = false;
    const available = UPGRADES.filter(item => this.upgrades[item.id] < item.max).map(item => item.id);
    this.choices = [];
    while (available.length && this.choices.length < 3) {
      this.choices.push(available.splice(Math.floor(this.random() * available.length), 1)[0]);
    }
    if (!this.choices.length) this.choices.push('repair');
    this.emit('clear', { wave: this.wave, boss });
  }

  updateCondition() {
    const formation = this.enemies.some(enemy => enemy.formation && !enemy.dead);
    const boss = this.boss && !this.boss.dead ? this.boss : null;
    let level = this.sectorTime > 100 ? 'RED' : this.sectorTime > 50 ? 'YELLOW' : 'GREEN';
    if ((formation || boss) && level === 'GREEN') level = 'YELLOW';
    if (boss && boss.phase === 2) level = 'RED';
    this.condition = level;
    const rank = CONDITIONS.indexOf(level);
    if (rank > this.alertRank) { this.alertRank = rank; this.emit('condition', { level }); }
  }

  updateHatch(base, dt) {
    base.hatchTimer -= dt;
    if (base.hatchState === 'closed') {
      if (base.hatchTimer <= 0) { base.hatchState = 'opening'; base.hatchTimer = .4; }
    } else if (base.hatchState === 'opening') {
      base.hatch = clamp(1 - base.hatchTimer / .4, 0, 1);
      if (base.hatchTimer <= 0) { base.hatchState = 'open'; base.hatch = 1; base.hatchTimer = 1.4; }
    } else if (base.hatchState === 'open') {
      if (base.hatchTimer <= 0) { base.hatchState = 'closing'; base.hatchTimer = .4; }
    } else {
      base.hatch = clamp(base.hatchTimer / .4, 0, 1);
      if (base.hatchTimer <= 0) { base.hatchState = 'closed'; base.hatch = 0; base.hatchTimer = 3.8 + this.random() * 3.2; }
    }
  }

  step(dt, input = {}) {
    if (this.mode !== 'playing') return;
    dt = clamp(dt, 0, .05);
    const ticks = Math.max(1, Math.ceil(dt / TICK - 1e-6));
    const h = dt / ticks;
    // The trigger edge is read once per frame so a tap always fires promptly.
    const pressed = !!input.fire && !this.player.trigger;
    for (let i = 0; i < ticks && this.mode === 'playing'; i++) this.tick(h, input, pressed && i === 0);
    this.player.trigger = !!input.fire;
  }

  tick(dt, input, pressed) {
    this.time += dt; this.sectorTime += dt;
    this.comboTime -= dt;
    if (this.comboTime <= 0) this.combo = 0;
    const p = this.player;
    p.invulnerable = Math.max(0, p.invulnerable - dt);
    p.sinceHit += dt;
    if (p.sinceHit > 3.2) p.shield = Math.min(this.maxShield, p.shield + dt * (7 + this.upgrades.shield * 3));

    // Constant forward flight: input steers, the ship never stops.
    const ix = clamp(input.x || 0, -1, 1), iy = clamp(input.y || 0, -1, 1);
    if (Math.hypot(ix, iy) > .18) {
      // Ease into the exact heading, including shallow analog angles. Take the
      // shortest turn across the angle seam and keep this heading on release.
      const turn = angleDiff(p.angle, Math.atan2(iy, ix)) * (1 - Math.exp(-SHIP.turnEase * dt));
      p.angle += clamp(turn, -SHIP.turn * dt, SHIP.turn * dt);
    }
    p.boosting = !!input.boost && p.energy > 0;
    p.energy = clamp(p.energy + dt * (p.boosting ? -40 : 22 + this.upgrades.engine * 5), 0, 100);
    const speed = this.speed * (p.boosting ? SHIP.boost : 1);
    const ease = 1 - Math.exp(-dt * 5);
    p.vx += (Math.cos(p.angle) * speed - p.vx) * ease;
    p.vy += (Math.sin(p.angle) * speed - p.vy) * ease;
    p.x = wrap(p.x + p.vx * dt); p.y = wrap(p.y + p.vy * dt);

    // Trigger: holding repeats at the cannon rate; a fresh press may cut the wait in half.
    p.shotTimer -= dt;
    if (input.fire && (p.shotTimer <= 0 || (pressed && p.shotTimer <= this.fireDelay * .5))) this.fire();

    for (const blast of this.blasts) blast.delay -= dt;
    const ready = this.blasts.filter(blast => blast.delay <= 0);
    if (ready.length) { this.blasts = this.blasts.filter(blast => blast.delay > 0); for (const blast of ready) this.applyBlast(blast); }

    this.spawnTimer -= dt;
    if (this.spawnTimer <= 0) {
      const extra = this.spawnEnemies(), t = this.threat;
      const gap = Math.max(Math.max(1.05, 1.6 - t * .02), 4.95 - t * .25 - this.sectorTime / 70) / this.difficulty.spawn;
      // A dreadnought launches its own fighters, so fewer arrive from deep space.
      this.spawnTimer = gap * (this.boss && !this.boss.dead ? 1.5 : 1) + extra;
    }
    this.updateEnemies(dt);
    this.updateBases(dt);
    this.updateBoss(dt);
    for (const rock of this.rocks) {
      if (!rock.dead && distance(rock, p) < rock.radius + SHIP.radius * .7) { this.damagePlayer(this.enemyDamage(16)); this.breakRock(rock, false); }
    }
    for (const mine of this.mines) if (!mine.dead && distance(mine, p) < 44) this.detonateMine(mine, 0);
    this.updateShots(dt);
    this.updatePickups(dt);
    this.enemies = this.enemies.filter(item => !item.dead);
    this.shots = this.shots.filter(item => !item.dead && item.life > 0);
    this.pickups = this.pickups.filter(item => item.life > 0);
    if (this.mode === 'playing') this.updateCondition();
    if (this.mode === 'playing' && this.remaining === 0) this.finishSector();
  }

  updateEnemies(dt) {
    const p = this.player, rules = this.difficulty, t = this.threat, pace = this.pace;
    const leaders = new Map();
    for (const enemy of this.enemies) if (enemy.leader && !enemy.dead) leaders.set(enemy.formation, enemy);
    for (const enemy of this.enemies) {
      if (enemy.dead) continue;
      const gunner = enemy.kind === 'gunner';
      let speed = gunner ? pace * .78 : pace, turn = gunner ? 2.1 : 2.7;
      const dx = delta(p.x, enemy.x), dy = delta(p.y, enemy.y), d = Math.hypot(dx, dy) || 1;
      const toPlayer = Math.atan2(dy, dx);
      let goal = toPlayer;
      const leader = enemy.formation && !enemy.leader ? leaders.get(enemy.formation) : null;
      if (enemy.formation && !enemy.leader && !leader) enemy.formation = 0;
      if (leader) {
        const c = Math.cos(leader.angle), s = Math.sin(leader.angle);
        const gx = delta(leader.x + enemy.ox * c - enemy.oy * s, enemy.x), gy = delta(leader.y + enemy.ox * s + enemy.oy * c, enemy.y);
        const gap = Math.hypot(gx, gy);
        goal = gap < 12 ? leader.angle : Math.atan2(gy, gx);
        turn = 5;
        speed = pace * clamp(.8 + gap / 120, .8, 1.35);
      } else if (gunner) {
        if (d < 300) goal = toPlayer + Math.PI;
        else if (d < 470) goal = toPlayer + enemy.side * .9;
      } else {
        const lead = clamp(d / (speed * 1.6), 0, .7);
        goal = Math.atan2(dy + p.vy * lead, dx + p.vx * lead) + Math.sin(this.time * 1.7 + enemy.phase) * .18;
      }
      enemy.angle += clamp(angleDiff(enemy.angle, goal), -turn * dt, turn * dt);
      enemy.x = wrap(enemy.x + Math.cos(enemy.angle) * speed * dt);
      enemy.y = wrap(enemy.y + Math.sin(enemy.angle) * speed * dt);
      enemy.shotTimer -= dt;
      if (gunner && enemy.shotTimer <= 0) {
        if (d < 720 && Math.abs(angleDiff(enemy.angle, toPlayer)) < 1) {
          const gx = enemy.x + Math.cos(enemy.angle) * 52, gy = enemy.y + Math.sin(enemy.angle) * 52;
          // Veteran gunships fire a three-bolt spread.
          const aim = Math.atan2(delta(p.y, gy), delta(p.x, gx)), lanes = t >= 8 ? 1 : 0;
          for (let lane = -lanes; lane <= lanes; lane++) this.shoot(gx, gy, aim + lane * .14, this.plasmaSpeed, 'enemy', this.enemyDamage(14), 'plasma');
          this.emit('enemyShot', { x: gx, y: gy, kind: 'plasma' });
          enemy.shotTimer = (Math.max(1.4, 2.72 - t * .08) + this.random() * .6) / rules.fire;
        } else enemy.shotTimer = .3;
      }
      if (d < SHIP.radius + ENEMIES[enemy.kind].radius * .85) { this.damagePlayer(this.enemyDamage(16)); this.killEnemy(enemy, false); }
    }
  }

  updateBases(dt) {
    const p = this.player, t = this.threat;
    for (const base of this.bases) {
      if (base.dead) continue;
      this.updateHatch(base, dt);
      for (const pod of base.pods) {
        if (pod.hp > 0) {
          pod.shotTimer -= dt;
          if (pod.shotTimer <= 0) {
            const dx = delta(p.x, pod.tipX), dy = delta(p.y, pod.tipY), aim = Math.atan2(dy, dx);
            if (Math.hypot(dx, dy) < 780 && Math.cos(aim - pod.aim) > .2) {
              this.shoot(pod.tipX, pod.tipY, aim, this.cannonSpeed, 'enemy', this.enemyDamage(14), 'cannon');
              this.emit('enemyShot', { x: pod.tipX, y: pod.tipY, kind: 'cannon' });
              pod.shotTimer = (Math.max(1.8, 4.08 - t * .12) + this.random() * 1.5) / this.difficulty.fire;
            } else pod.shotTimer = .35 + this.random() * .5;
          }
        }
        if (distance(pod, p) < FORTRESS.pod + SHIP.radius * .6) this.collide(pod, this.enemyDamage(20));
      }
      if (distance(base, p) < FORTRESS.body + SHIP.radius * .6) this.collide(base, this.enemyDamage(20));
    }
  }

  updateBoss(dt) {
    const boss = this.boss, p = this.player, rules = this.difficulty;
    if (!boss || boss.dead) return;
    const enraged = boss.phase === 2;
    const dx = delta(p.x, boss.x), dy = delta(p.y, boss.y), range = Math.hypot(dx, dy), toPlayer = Math.atan2(dy, dx);
    if ((boss.sideTimer -= dt) <= 0) { boss.side = -boss.side; boss.sideTimer = 6 + this.random() * 4; }
    // Close in, then circle at battle range with the broadside batteries on target.
    let goal = toPlayer;
    if (range < 430) goal = toPlayer + boss.side * 2.1;
    else if (range < 760) goal = toPlayer + boss.side * .95;
    const turn = (enraged ? .72 : .52) * rules.speed;
    boss.angle += clamp(angleDiff(boss.angle, goal), -turn * dt, turn * dt);
    const speed = (105 + boss.level * 12) * rules.speed * (enraged ? 1.3 : 1) * (range > 1000 ? 1.9 : 1);
    boss.x = wrap(boss.x + Math.cos(boss.angle) * speed * dt);
    boss.y = wrap(boss.y + Math.sin(boss.angle) * speed * dt);
    this.placeBoss(boss);

    // Batteries turn to track the ship and fire aimed cannon rounds.
    for (const gun of boss.guns) {
      if (gun.hp <= 0) continue;
      const gx = delta(p.x, gun.x), gy = delta(p.y, gun.y);
      gun.aim = Math.atan2(gy, gx);
      if ((gun.timer -= dt) > 0) continue;
      if (Math.hypot(gx, gy) < 1000) {
        const mx = gun.x + Math.cos(gun.aim) * 34, my = gun.y + Math.sin(gun.aim) * 34;
        this.shoot(mx, my, gun.aim, this.cannonSpeed, 'enemy', this.enemyDamage(14), 'cannon');
        this.emit('enemyShot', { x: mx, y: my, kind: 'cannon' });
        gun.timer = (Math.max(1.3, 2.6 - boss.level * .2) + this.random() * 1.2) / rules.fire;
      } else gun.timer = .4;
    }

    // The core glows as it charges, then releases a ring of plasma. The glow is the warning.
    boss.ringTimer -= dt;
    if (boss.charge > 0 || boss.ringTimer <= 0) {
      if (boss.charge === 0) this.emit('bossCharge', { x: boss.core.x, y: boss.core.y });
      boss.charge = Math.min(1, boss.charge + dt / .9);
      if (boss.charge >= 1) {
        boss.charge = 0;
        boss.ringTimer = Math.max(2.4, 6.5 - boss.level * .5) * (enraged ? .6 : 1) / rules.fire;
        this.bossRing(boss);
      }
    }

    // Fighters launch from the rear bay.
    if ((boss.launchTimer -= dt) <= 0) {
      const count = Math.min(2 + boss.level, Math.max(0, this.maxEnemies - this.enemies.length));
      const bay = this.bossPoint(boss, BOSS.bay), back = boss.angle + Math.PI;
      for (let i = 0; i < count; i++) {
        const a = back + (i - (count - 1) / 2) * .4;
        this.enemies.push(this.makeEnemy('chaser', wrap(bay.x + Math.cos(a) * 30), wrap(bay.y + Math.sin(a) * 30), a));
      }
      if (count) this.emit('launch', { x: bay.x, y: bay.y, count });
      boss.launchTimer = Math.max(5, 11 - boss.level) * (enraged ? .75 : 1) / rules.spawn;
    }

    // With the shield down, the exposed core fires aimed plasma volleys.
    if (enraged && (boss.coreTimer -= dt) <= 0) {
      const aim = Math.atan2(delta(p.y, boss.core.y), delta(p.x, boss.core.x));
      for (let lane = -1; lane <= 1; lane++) this.shoot(boss.core.x, boss.core.y, aim + lane * .16, this.plasmaSpeed, 'enemy', this.enemyDamage(14), 'plasma');
      this.emit('enemyShot', { x: boss.core.x, y: boss.core.y, kind: 'plasma' });
      boss.coreTimer = Math.max(1.2, 2.4 - boss.level * .2) / rules.fire;
    }

    // Ramming the hull bounces the ship away and hurts.
    const hull = boss.hull.find(part => distance(part, p) < part.r + SHIP.radius * .6);
    if (hull) this.collide(hull, this.enemyDamage(24));
  }

  bossRing(boss) {
    const count = 14 + boss.level * 4 + (boss.phase === 2 ? 6 : 0);
    const speed = Math.min(360, 235 + boss.level * 15) * this.difficulty.bullet, offset = this.random() * TAU, damage = this.enemyDamage(12);
    for (let i = 0; i < count; i++) {
      const a = offset + i * TAU / count;
      this.shoot(boss.core.x + Math.cos(a) * 40, boss.core.y + Math.sin(a) * 40, a, speed, 'enemy', damage, 'plasma');
    }
    this.emit('bossRing', { x: boss.core.x, y: boss.core.y, count });
  }

  updateShots(dt) {
    const p = this.player;
    for (const shot of this.shots) {
      if (shot.dead) continue;
      shot.oldX = shot.x; shot.oldY = shot.y;
      shot.stepX = shot.vx * dt; shot.stepY = shot.vy * dt;
      shot.x = wrap(shot.x + shot.stepX); shot.y = wrap(shot.y + shot.stepY); shot.life -= dt;
      if (shot.owner === 'enemy') {
        if (hitsSegment(shot, p, SHIP.radius)) { shot.dead = true; this.damagePlayer(shot.damage); }
        else if (this.rocks.some(rock => !rock.dead && hitsSegment(shot, rock, rock.radius * .9))) {
          shot.dead = true; this.emit('spark', { x: shot.x, y: shot.y, kind: 'rock' });
        }
        continue;
      }
      for (const enemy of this.enemies) {
        if (!enemy.dead && hitsSegment(shot, enemy, ENEMIES[enemy.kind].radius)) {
          enemy.hp -= shot.damage; shot.dead = true;
          this.emit('spark', { x: shot.x, y: shot.y, kind: 'hit', id: enemy.id, laser: shot.color });
          if (enemy.hp <= 0) this.killEnemy(enemy);
          break;
        }
      }
      if (shot.dead) continue;
      if (this.boss && !this.boss.dead && this.hitBoss(shot)) continue;
      for (const base of this.bases) {
        if (base.dead) continue;
        const pod = base.pods.find(item => item.hp > 0 && hitsSegment(shot, item, FORTRESS.pod));
        if (pod) {
          shot.dead = true; this.hitBase(base, pod, shot.damage);
          this.emit('spark', { x: shot.x, y: shot.y, kind: 'pod', laser: shot.color }); break;
        }
        if (hitsSegment(shot, base, FORTRESS.core)) {
          shot.dead = true;
          this.emit(this.hitBase(base, null, shot.damage) ? 'spark' : 'deflect', { x: shot.x, y: shot.y, kind: 'core', laser: shot.color });
          break;
        }
      }
      if (shot.dead) continue;
      const rock = this.rocks.find(item => !item.dead && hitsSegment(shot, item, item.radius));
      if (rock) {
        rock.hp -= shot.damage; shot.dead = true;
        this.emit('spark', { x: shot.x, y: shot.y, kind: 'rock' });
        if (rock.hp <= 0) this.breakRock(rock);
        continue;
      }
      const mine = this.mines.find(item => !item.dead && hitsSegment(shot, item, 16));
      if (mine) { shot.dead = true; this.detonateMine(mine); }
    }
  }

  updatePickups(dt) {
    const p = this.player;
    for (const item of this.pickups) {
      item.life -= dt;
      const d = distance(item, p);
      if (d < 150 * (1 + this.upgrades.magnet)) {
        const speed = 260 + (1 - Math.min(1, d / 320)) * 320;
        item.x = wrap(item.x + delta(p.x, item.x) / (d || 1) * speed * dt);
        item.y = wrap(item.y + delta(p.y, item.y) / (d || 1) * speed * dt);
      }
      if (d < 38 && this.mode === 'playing') {
        if (item.kind === 'repair') p.hull = Math.min(this.maxHull, p.hull + item.amount);
        else this.reward(item.amount);
        item.life = 0;
        this.emit('pickup', { kind: item.kind, x: item.x, y: item.y, amount: item.amount });
      }
    }
  }
}
