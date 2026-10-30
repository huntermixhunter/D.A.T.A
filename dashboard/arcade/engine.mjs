// Pure simulation shared by the browser and the deterministic gameplay checks.
// World units are measured against the artwork: the player ship is 150 long,
// a fortress is 480 across, and the sector wraps at WORLD on both axes.
export const WORLD = 4800;
export const REWARD_SECTOR = 1;
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
    runs: n(raw.runs), sound: raw.sound !== false,
    hangar: Object.fromEntries(HANGAR.map(item => [item.id, n(raw.hangar?.[item.id], item.max)])),
  };
}

export const hasEventHorizon = profile => cleanProfile(profile).clearedSector >= REWARD_SECTOR;

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
  constructor({ random = Math.random, hangar = {} } = {}) {
    this.random = random;
    this.hangar = cleanProfile({ hangar }).hangar;
    this.mode = 'ready';
    this.upgrades = Object.fromEntries(UPGRADES.map(item => [item.id, 0]));
    this.wave = 1;
    this.clearedSector = 0;
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
  get remaining() { return this.bases.filter(base => !base.dead).length; }

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
    const count = Math.min(3 + Math.floor((this.wave - 1) / 2), 7);
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
    for (let i = 0; i < 52; i++) {
      const radius = 22 + Math.pow(this.random(), 1.6) * 40;
      const rock = { id: this.nextId++, x: this.random() * WORLD, y: this.random() * WORLD, radius,
        seed: this.random(), variant: Math.floor(this.random() * 8), hp: radius > 44 ? 4 : radius > 32 ? 3 : 2, dead: false };
      if (distance(rock, p) > 300 && this.bases.every(base => distance(rock, base) > 330)) this.rocks.push(rock);
    }
    for (let i = 0; i < Math.min(8 + this.wave * 2, 34); i++) {
      const mine = { id: this.nextId++, x: this.random() * WORLD, y: this.random() * WORLD, phase: this.random() * TAU, dead: false };
      if (distance(mine, p) > 340 && this.bases.every(base => distance(mine, base) > 300)) this.mines.push(mine);
    }
    p.invulnerable = 2;
    this.emit('sector', { wave: this.wave });
  }

  makeBase(x, y) {
    // Stations come in two orientations, as in the arcade original.
    const rot = this.random() < .5 ? 0 : Math.PI / 2;
    const cos = Math.cos(rot), sin = Math.sin(rot);
    const turn = (dx, dy) => [dx * cos - dy * sin, dx * sin + dy * cos];
    const podHp = 3 + Math.floor(this.wave / 3);
    const pods = FORTRESS.pods.map((spec, index) => {
      const [ox, oy] = turn(spec.x, spec.y), [tx, ty] = turn(spec.tipX, spec.tipY);
      return { index, x: wrap(x + ox), y: wrap(y + oy), tipX: wrap(x + tx), tipY: wrap(y + ty),
        aim: Math.atan2(ty - oy, tx - ox), hp: podHp, maxHp: podHp, shotTimer: 1.5 + this.random() * 3 };
    });
    const hp = 8 + this.wave * 2;
    return { id: this.nextId++, x, y, rot, pods, hp, maxHp: hp, dead: false,
      hatch: 0, hatchState: 'closed', hatchTimer: 2.5 + this.random() * 3.5 };
  }

  makeEnemy(kind, x, y, angle, extra = {}) {
    return { id: this.nextId++, kind, x, y, angle, hp: ENEMIES[kind].hp + Math.floor((this.wave - 1) / 3),
      shotTimer: 1.2 + this.random() * 1.6, phase: this.random() * TAU, side: this.random() < .5 ? -1 : 1,
      dead: false, formation: 0, leader: false, ox: 0, oy: 0, ...extra };
  }

  pause() { if (this.mode === 'playing') { this.mode = 'paused'; this.player.boosting = false; this.player.trigger = false; } }
  resume() { if (this.mode === 'paused') this.mode = 'playing'; }

  addScore(amount) {
    this.combo++;
    this.comboTime = 5;
    const gained = amount * this.multiplier;
    this.score += gained;
    return gained;
  }

  reward(amount) { this.salvage += Math.round(amount * (1 + this.upgrades.magnet * .25)); }

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
      life: owner === 'player' ? .8 : 2.6, owner, damage, kind, angle: Math.atan2(vy, vx), dead: false });
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
    this.emit('shot', { x: p.x, y: p.y, angle: p.angle });
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
    if (distance(blast, this.player) < blast.radius * .8) this.damagePlayer(28);
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
    return true;
  }

  // Returns extra seconds to wait before the next spawn.
  spawnEnemies() {
    const p = this.player;
    if (this.enemies.length >= MAX_ENEMIES) return 0;
    const angle = this.random() * TAU;
    const x = wrap(p.x + Math.cos(angle) * 980), y = wrap(p.y + Math.sin(angle) * 980);
    const pressure = this.sectorTime > 100 ? 2 : this.sectorTime > 50 ? 1 : 0;
    const odds = this.wave === 1 ? [0, .25, .45][pressure] : [.3, .5, .7][pressure];
    if (this.random() < odds) { this.spawnFormation(x, y, angle + Math.PI); return 3; }
    const count = this.wave >= 3 || this.sectorTime > 40 ? 2 : 1;
    for (let i = 0; i < count && this.enemies.length < MAX_ENEMIES; i++) {
      const kind = this.wave >= 2 && this.random() < .35 ? 'gunner' : 'chaser';
      const a = angle + (i - (count - 1) / 2) * .25;
      this.enemies.push(this.makeEnemy(kind, wrap(p.x + Math.cos(a) * 980), wrap(p.y + Math.sin(a) * 980), a + Math.PI));
    }
    return 0;
  }

  // A leader with four wingmen in a V. Destroying the leader breaks the formation.
  spawnFormation(x, y, heading) {
    const id = this.nextId++, c = Math.cos(heading), s = Math.sin(heading);
    const slots = [[0, 0], [-82, -80], [-82, 80], [-164, -160], [-164, 160]];
    for (const [i, [ox, oy]] of slots.entries()) {
      if (this.enemies.length >= MAX_ENEMIES) break;
      const kind = this.wave >= 4 && i >= 3 ? 'gunner' : 'chaser';
      this.enemies.push(this.makeEnemy(kind, wrap(x + ox * c - oy * s), wrap(y + ox * s + oy * c), heading,
        { formation: id, leader: i === 0, ox, oy }));
    }
    this.emit('formation', { x, y });
  }

  chooseUpgrade(id) {
    if (this.mode !== 'upgrade' || !this.choices.includes(id)) return false;
    if (id === 'repair') { this.player.hull = this.maxHull; this.reward(60); }
    else {
      const item = UPGRADES.find(upgrade => upgrade.id === id);
      if (!item || this.upgrades[id] >= item.max) return false;
      this.upgrades[id]++;
      if (id === 'hull') this.player.hull = Math.min(this.maxHull, this.player.hull + 45);
    }
    this.player.hull = Math.min(this.maxHull, this.player.hull + 25);
    this.player.shield = this.maxShield;
    this.player.energy = 100;
    this.player.pulses = Math.min(6, this.player.pulses + 1 + this.upgrades.pulse);
    this.player.vx = this.player.vy = 0;
    this.wave++;
    this.mode = 'playing';
    this.choices = [];
    this.spawnSector();
    return true;
  }

  finishSector() {
    if (this.mode !== 'playing' || this.remaining !== 0) return;
    this.clearedSector = this.wave;
    this.reward(75 + this.wave * 15);
    this.score += 1500 + Math.max(0, Math.floor(180 - this.sectorTime)) * 10;
    this.mode = 'upgrade';
    this.player.boosting = false;
    this.player.trigger = false;
    const available = UPGRADES.filter(item => this.upgrades[item.id] < item.max).map(item => item.id);
    this.choices = [];
    while (available.length && this.choices.length < 3) {
      this.choices.push(available.splice(Math.floor(this.random() * available.length), 1)[0]);
    }
    if (!this.choices.length) this.choices.push('repair');
    this.emit('clear', { wave: this.wave });
  }

  updateCondition() {
    const formation = this.enemies.some(enemy => enemy.formation && !enemy.dead);
    let level = this.sectorTime > 100 ? 'RED' : this.sectorTime > 50 ? 'YELLOW' : 'GREEN';
    if (formation && level === 'GREEN') level = 'YELLOW';
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
      const extra = this.spawnEnemies();
      this.spawnTimer = Math.max(1.6, 5.2 - this.wave * .25 - this.sectorTime / 70) + extra;
    }
    this.updateEnemies(dt);
    this.updateBases(dt);
    for (const rock of this.rocks) {
      if (!rock.dead && distance(rock, p) < rock.radius + SHIP.radius * .7) { this.damagePlayer(16); this.breakRock(rock, false); }
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
    const p = this.player;
    const leaders = new Map();
    for (const enemy of this.enemies) if (enemy.leader && !enemy.dead) leaders.set(enemy.formation, enemy);
    const pace = Math.min(390, 240 + this.wave * 10 + this.sectorTime * .35);
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
          this.shoot(gx, gy, Math.atan2(delta(p.y, gy), delta(p.x, gx)), Math.min(460, 330 + this.wave * 8), 'enemy', 14, 'plasma');
          this.emit('enemyShot', { x: gx, y: gy, kind: 'plasma' });
          enemy.shotTimer = Math.max(1.4, 2.8 - this.wave * .08) + this.random() * .6;
        } else enemy.shotTimer = .3;
      }
      if (d < SHIP.radius + ENEMIES[enemy.kind].radius * .85) { this.damagePlayer(16); this.killEnemy(enemy, false); }
    }
  }

  updateBases(dt) {
    const p = this.player;
    for (const base of this.bases) {
      if (base.dead) continue;
      this.updateHatch(base, dt);
      for (const pod of base.pods) {
        if (pod.hp > 0) {
          pod.shotTimer -= dt;
          if (pod.shotTimer <= 0) {
            const dx = delta(p.x, pod.tipX), dy = delta(p.y, pod.tipY), aim = Math.atan2(dy, dx);
            if (Math.hypot(dx, dy) < 780 && Math.cos(aim - pod.aim) > .2) {
              this.shoot(pod.tipX, pod.tipY, aim, Math.min(430, 290 + this.wave * 9), 'enemy', 14, 'cannon');
              this.emit('enemyShot', { x: pod.tipX, y: pod.tipY, kind: 'cannon' });
              pod.shotTimer = Math.max(1.8, 4.2 - this.wave * .12) + this.random() * 1.5;
            } else pod.shotTimer = .35 + this.random() * .5;
          }
        }
        if (distance(pod, p) < FORTRESS.pod + SHIP.radius * .6) this.collide(pod, 20);
      }
      if (distance(base, p) < FORTRESS.body + SHIP.radius * .6) this.collide(base, 20);
    }
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
          this.emit('spark', { x: shot.x, y: shot.y, kind: 'hit', id: enemy.id });
          if (enemy.hp <= 0) this.killEnemy(enemy);
          break;
        }
      }
      if (shot.dead) continue;
      for (const base of this.bases) {
        if (base.dead) continue;
        const pod = base.pods.find(item => item.hp > 0 && hitsSegment(shot, item, FORTRESS.pod));
        if (pod) {
          shot.dead = true; this.hitBase(base, pod, shot.damage);
          this.emit('spark', { x: shot.x, y: shot.y, kind: 'pod' }); break;
        }
        if (hitsSegment(shot, base, FORTRESS.core)) {
          shot.dead = true;
          this.emit(this.hitBase(base, null, shot.damage) ? 'spark' : 'deflect', { x: shot.x, y: shot.y, kind: 'core' });
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
