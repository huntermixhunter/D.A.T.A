import { WORLD, TAU, FORTRESS, delta, distance, clamp } from './engine.mjs';

const VERSION = 2;
const asset = name => new URL('./assets/' + name + '?v=' + VERSION, import.meta.url).href;

// Artwork geometry, as fractions of each sprite's width. Engines: [x, y, flame length, nozzle height].
const ART = {
  ship: { file: 'ship.webp', width: 150, flame: 'cyan',
    engines: [[-.4611, 0, 1, .115], [-.3745, -.1563, .62, .05], [-.3745, .1555, .62, .05]] },
  chaser: { file: 'interceptor.webp', width: 96, flame: 'cyan', engines: [[-.4872, -.1196, .6, .075], [-.4872, .1019, .6, .075]] },
  gunner: { file: 'manta.webp', width: 112, flame: 'green', engines: [[-.322, -.0823, .6, .06], [-.322, .0713, .6, .06]] },
  fortress: { file: 'fortress.webp', width: 480, pivotY: .4793 },
};
const FORTRESS_SOURCE = 1254;
const FORTRESS_SCALE = ART.fortress.width / FORTRESS_SOURCE;
const CORE_SOURCE = { x: 627, y: 601 };

const PALETTE = {
  white: [255, 255, 255], cyan: [120, 222, 255], blue: [86, 158, 255], orange: [255, 150, 60],
  amber: [255, 196, 107], red: [255, 74, 61], green: [125, 255, 122], violet: [183, 115, 255],
  ember: [255, 110, 40], pink: [255, 90, 120], ftl: [110, 240, 138],
};
const rgba = (name, a) => { const [r, g, b] = PALETTE[name]; return `rgba(${r},${g},${b},${a})`; };
const canvas2d = (w, h) => {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.ceil(w)); c.height = Math.max(1, Math.ceil(h));
  return c;
};
const rand = (a, b) => a + Math.random() * (b - a);
const pick = list => list[Math.floor(Math.random() * list.length)];
const hash = n => { const f = Math.sin(n * 127.1 + 311.7) * 43758.5453; return f - Math.floor(f); };

// A loaded image with a chain of high-quality half-size copies, so rotated sprites
// stay crisp at any zoom instead of shimmering from one-step downscaling.
class Sprite {
  constructor(src) {
    this.image = new Image();
    this.image.decoding = 'async';
    this.levels = [];
    this.aspect = 1;
    this.ready = new Promise(resolve => {
      this.image.onload = () => { this.build(); resolve(true); };
      this.image.onerror = () => resolve(false);
    });
    this.image.src = src;
  }
  build() {
    let source = this.image, w = this.image.naturalWidth, h = this.image.naturalHeight;
    this.aspect = h / w;
    this.levels = [{ canvas: source, w }];
    while (w > 40) {
      const nw = Math.max(1, Math.round(w / 2)), nh = Math.max(1, Math.round(h / 2));
      const canvas = canvas2d(nw, nh), c = canvas.getContext('2d');
      c.imageSmoothingQuality = 'high';
      c.drawImage(source, 0, 0, nw, nh);
      this.levels.push({ canvas, w: nw });
      source = canvas; w = nw; h = nh;
    }
  }
  get loaded() { return this.levels.length > 0; }
  pick(pixels) {
    let best = this.levels[0];
    for (const level of this.levels) if (level.w >= pixels * .9) best = level;
    return best.canvas;
  }
}

function glowTexture(name, size = 64) {
  const c = canvas2d(size, size), g = c.getContext('2d'), r = size / 2;
  const grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, rgba(name, 1)); grad.addColorStop(.2, rgba(name, .6));
  grad.addColorStop(.48, rgba(name, .18)); grad.addColorStop(1, rgba(name, 0));
  g.fillStyle = grad; g.fillRect(0, 0, size, size);
  return c;
}

// Elongated bolt: white-hot core, colored body. The head sits at 62% of the width.
function boltTexture(name, w = 128, h = 32) {
  const c = canvas2d(w, h), g = c.getContext('2d');
  g.translate(w * .62, h / 2); g.scale(w * .62, h / 2);
  const grad = g.createRadialGradient(0, 0, 0, 0, 0, 1);
  grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(.2, 'rgba(255,255,255,.92)');
  grad.addColorStop(.42, rgba(name, .85)); grad.addColorStop(1, rgba(name, 0));
  g.fillStyle = grad; g.beginPath(); g.arc(0, 0, 1, 0, TAU); g.fill();
  return c;
}

// Exhaust plume: bright at the right edge (the nozzle), fading to the left.
function flameTexture(name, w = 128, h = 40) {
  const c = canvas2d(w, h), g = c.getContext('2d');
  g.translate(w, h / 2); g.scale(w, h / 2);
  const grad = g.createRadialGradient(0, 0, 0, 0, 0, 1);
  grad.addColorStop(0, 'rgba(255,255,255,1)'); grad.addColorStop(.1, 'rgba(255,255,255,.85)');
  grad.addColorStop(.3, rgba(name, .7)); grad.addColorStop(.65, rgba(name, .2)); grad.addColorStop(1, rgba(name, 0));
  g.fillStyle = grad; g.beginPath(); g.arc(0, 0, 1, Math.PI / 2, Math.PI * 1.5); g.closePath(); g.fill();
  return c;
}

function smokeTexture(size = 64) {
  const c = canvas2d(size, size), g = c.getContext('2d'), r = size / 2;
  const grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, 'rgba(120,118,116,.55)'); grad.addColorStop(.5, 'rgba(80,78,76,.25)'); grad.addColorStop(1, 'rgba(60,60,60,0)');
  g.fillStyle = grad; g.fillRect(0, 0, size, size);
  return c;
}

// FTL-style shield bubble: clear center, bright rim.
function bubbleTexture(size = 256) {
  const c = canvas2d(size, size), g = c.getContext('2d'), r = size / 2;
  const grad = g.createRadialGradient(r, r, 0, r, r, r);
  grad.addColorStop(0, 'rgba(70,150,255,0)'); grad.addColorStop(.6, 'rgba(70,150,255,.04)');
  grad.addColorStop(.85, 'rgba(100,185,255,.2)'); grad.addColorStop(.95, 'rgba(175,228,255,.62)'); grad.addColorStop(1, 'rgba(175,228,255,0)');
  g.fillStyle = grad; g.fillRect(0, 0, size, size);
  return c;
}

function cogTexture(size = 64) {
  const c = canvas2d(size, size), g = c.getContext('2d'), r = size / 2;
  g.translate(r, r);
  g.beginPath();
  for (let i = 0; i < 16; i++) {
    const a = i / 16 * TAU, outer = i % 2 ? r * .7 : r * .92;
    g.lineTo(Math.cos(a - .12) * outer, Math.sin(a - .12) * outer);
    g.lineTo(Math.cos(a + .12) * outer, Math.sin(a + .12) * outer);
  }
  g.closePath();
  const metal = g.createLinearGradient(-r, -r, r, r);
  metal.addColorStop(0, '#e9e2cf'); metal.addColorStop(.55, '#a9a49a'); metal.addColorStop(1, '#5d5a55');
  g.fillStyle = metal; g.fill();
  g.lineWidth = size * .04; g.strokeStyle = '#1c1b1a'; g.stroke();
  g.beginPath(); g.arc(0, 0, r * .3, 0, TAU); g.fillStyle = '#1c1b1a'; g.fill();
  return c;
}

function repairTexture(size = 64) {
  const c = canvas2d(size, size), g = c.getContext('2d'), u = size / 16;
  g.fillStyle = 'rgba(8,24,14,.85)'; g.strokeStyle = rgba('ftl', 1); g.lineWidth = u;
  g.beginPath(); g.roundRect(u * 1.5, u * 1.5, u * 13, u * 13, u * 2.5); g.fill(); g.stroke();
  g.fillStyle = rgba('ftl', 1);
  g.fillRect(u * 6.5, u * 4, u * 3, u * 8); g.fillRect(u * 4, u * 6.5, u * 8, u * 3);
  return c;
}

function mineTextures(size = 96) {
  const r = size / 2;
  const spikes = canvas2d(size, size), s = spikes.getContext('2d');
  s.translate(r, r);
  for (let i = 0; i < 8; i++) {
    const a = i / 8 * TAU;
    s.save(); s.rotate(a);
    const grad = s.createLinearGradient(r * .3, 0, r * .95, 0);
    grad.addColorStop(0, '#5b5f66'); grad.addColorStop(1, '#25272b');
    s.fillStyle = grad;
    s.beginPath(); s.moveTo(r * .3, -r * .09); s.lineTo(r * .92, -r * .025); s.lineTo(r * .95, 0); s.lineTo(r * .92, r * .025); s.lineTo(r * .3, r * .09); s.closePath(); s.fill();
    s.fillStyle = '#7c2b33'; s.fillRect(r * .86, -r * .04, r * .08, r * .08);
    s.restore();
  }
  const body = canvas2d(size, size), b = body.getContext('2d');
  const shade = b.createRadialGradient(r * .78, r * .72, r * .04, r, r, r * .5);
  shade.addColorStop(0, '#8d929a'); shade.addColorStop(.45, '#3d4148'); shade.addColorStop(1, '#121417');
  b.fillStyle = shade; b.beginPath(); b.arc(r, r, r * .46, 0, TAU); b.fill();
  b.strokeStyle = 'rgba(0,0,0,.7)'; b.lineWidth = size * .02; b.stroke();
  b.strokeStyle = 'rgba(255,255,255,.08)'; b.beginPath(); b.arc(r, r, r * .32, 0, TAU); b.stroke();
  b.fillStyle = '#2a0c10'; b.beginPath(); b.arc(r, r, r * .14, 0, TAU); b.fill();
  return { spikes, body };
}

// Asteroids lit from the upper left. The lighting is baked, so rocks never rotate.
function rockTexture(seed, size = 192) {
  const c = canvas2d(size, size), g = c.getContext('2d'), r = size / 2, R = size * .44;
  const phase = [hash(seed) * TAU, hash(seed + 1) * TAU, hash(seed + 2) * TAU];
  const points = [];
  for (let i = 0; i < 22; i++) {
    const a = i / 22 * TAU;
    const n = .8 + .1 * Math.sin(a * 2 + phase[0]) + .07 * Math.sin(a * 3 + phase[1]) + .05 * Math.sin(a * 7 + phase[2]) + .03 * (hash(seed * 31 + i) - .5);
    points.push([r + Math.cos(a) * R * n, r + Math.sin(a) * R * n * (.86 + hash(seed + 3) * .14)]);
  }
  const shape = () => { g.beginPath(); points.forEach(([x, y], i) => i ? g.lineTo(x, y) : g.moveTo(x, y)); g.closePath(); };
  shape(); g.save(); g.clip();
  const tone = 58 + hash(seed + 4) * 18;
  g.fillStyle = `rgb(${tone + 6},${tone},${tone - 6})`; g.fillRect(0, 0, size, size);
  for (let i = 0; i < 420; i++) {
    const v = hash(seed * 97 + i * 3.1);
    g.fillStyle = v > .5 ? `rgba(255,248,235,${(v - .5) * .12})` : `rgba(0,0,0,${(.5 - v) * .22})`;
    g.fillRect(hash(seed + i * 1.7) * size, hash(seed + i * 2.3) * size, 1 + hash(i + seed) * 3, 1 + hash(i * 2 + seed) * 3);
  }
  for (let i = 0; i < 7; i++) {
    const cx = r + (hash(seed * 7 + i) - .5) * R * 1.3, cy = r + (hash(seed * 11 + i) - .5) * R * 1.3;
    const cr = R * (.07 + hash(seed * 13 + i) * .16);
    g.fillStyle = 'rgba(0,0,0,.34)'; g.beginPath(); g.ellipse(cx, cy, cr, cr * .82, 0, 0, TAU); g.fill();
    g.strokeStyle = 'rgba(255,244,225,.22)'; g.lineWidth = cr * .22;
    g.beginPath(); g.ellipse(cx, cy, cr, cr * .82, 0, .2, 2.2); g.stroke();
  }
  const light = g.createRadialGradient(r - R * .5, r - R * .55, R * .05, r - R * .1, r - R * .1, R * 1.45);
  light.addColorStop(0, 'rgba(255,246,230,.38)'); light.addColorStop(.42, 'rgba(255,246,230,.04)');
  light.addColorStop(.7, 'rgba(0,0,0,.35)'); light.addColorStop(1, 'rgba(0,0,0,.85)');
  g.fillStyle = light; g.fillRect(0, 0, size, size);
  g.restore();
  shape(); g.lineWidth = size * .012; g.strokeStyle = 'rgba(0,0,0,.7)'; g.stroke();
  return c;
}

// Realistic star colors by temperature class, with a weighted mix.
const STAR_COLORS = [
  [155, 176, 255, .08], [202, 215, 255, .18], [248, 247, 255, .24], [255, 244, 234, .22],
  [255, 222, 186, .18], [255, 204, 150, .1],
];
function starColor(v) {
  let t = 0;
  for (let i = 0; i < STAR_COLORS.length; i++) { t += STAR_COLORS[i][3]; if (v <= t) return i; }
  return 2;
}
function starSprite([r, g, b], size) {
  const c = canvas2d(size, size), x = c.getContext('2d'), m = size / 2;
  const grad = x.createRadialGradient(m, m, 0, m, m, m);
  grad.addColorStop(0, `rgba(${Math.min(255, r + 25)},${Math.min(255, g + 25)},${Math.min(255, b + 25)},1)`);
  grad.addColorStop(.25, `rgba(${r},${g},${b},.8)`); grad.addColorStop(.5, `rgba(${r},${g},${b},.22)`);
  grad.addColorStop(1, `rgba(${r},${g},${b},0)`);
  x.fillStyle = grad; x.fillRect(0, 0, size, size);
  return c;
}

// Faint dust: a small tile. Repetition is invisible at this brightness.
function dustTile(spec, dpr, seed) {
  const size = Math.round(spec.tile * dpr), c = canvas2d(size, size), g = c.getContext('2d');
  const count = Math.round(spec.density * spec.tile * spec.tile / 1e6);
  for (let i = 0; i < count; i++) {
    const h = n => hash(seed * 1000 + i * 7.13 + n);
    const x = h(1) * size, y = h(2) * size, [r, gr, b] = STAR_COLORS[starColor(h(4))];
    const mag = spec.bright[0] + Math.pow(h(3), 2.4) * (spec.bright[1] - spec.bright[0]);
    const radius = (spec.size[0] + Math.pow(h(5), 2) * (spec.size[1] - spec.size[0])) * dpr * 1.6;
    const core = g.createRadialGradient(x, y, 0, x, y, radius);
    core.addColorStop(0, `rgba(${r},${gr},${b},${mag})`); core.addColorStop(1, `rgba(${r},${gr},${b},0)`);
    g.fillStyle = core; g.beginPath(); g.arc(x, y, radius, 0, TAU); g.fill();
  }
  return c;
}

// Brighter stars are placed one by one across a large virtual sky, so nothing repeats on screen.
function starField(spec, seed) {
  const count = Math.round(spec.density * spec.field * spec.field / 1e6);
  return Array.from({ length: count }, (_, i) => {
    const h = n => hash(seed * 1000 + i * 7.13 + n);
    return { x: h(1) * spec.field, y: h(2) * spec.field, color: starColor(h(4)),
      mag: spec.bright[0] + Math.pow(h(3), 2.4) * (spec.bright[1] - spec.bright[0]),
      size: spec.size[0] + Math.pow(h(5), 2) * (spec.size[1] - spec.size[0]) };
  });
}

const STAR_LAYERS = [
  { tile: 640, density: 150, bright: [.07, .3], size: [.45, .75], parallax: .025, drift: .5 },
  { image: true, parallax: .055, drift: 1 },
  { field: 3600, density: 34, bright: [.25, .8], size: [.55, 1.05], parallax: .11, drift: 2 },
  { field: 4800, density: 7, bright: [.6, 1], size: [.8, 1.45], parallax: .2, drift: 3.4, halo: true },
];

export class Renderer {
  constructor(canvas, radar) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.radar = radar;
    this.rc = radar.getContext('2d');
    this.sprites = Object.fromEntries(Object.entries(ART).map(([key, art]) => [key, new Sprite(asset(art.file))]));
    this.deepStars = new Image();
    this.deepStars.src = asset('stars.webp');
    this.ready = Promise.all(Object.values(this.sprites).map(sprite => sprite.ready)).then(list => list.every(Boolean));
    this.sprites.fortress.ready.then(ok => { if (ok) this.buildFortressDamage(); });
    const glows = Object.fromEntries(Object.keys(PALETTE).map(name => [name, glowTexture(name)]));
    this.tex = {
      glow: glows, smoke: smokeTexture(), bubble: bubbleTexture(), cog: cogTexture(), repair: repairTexture(),
      bolt: { cyan: boltTexture('cyan'), orange: boltTexture('orange'), green: boltTexture('green', 96, 40), white: boltTexture('white') },
      flame: { cyan: flameTexture('cyan'), green: flameTexture('green'), orange: flameTexture('orange') },
      mine: mineTextures(), rocks: Array.from({ length: 8 }, (_, i) => rockTexture(i * 17.3 + 4)),
    };
    this.particles = []; this.popups = []; this.scheduled = []; this.wrecks = [];
    this.flashes = new Map();
    this.clock = 0; this.shake = 0; this.hurt = 0; this.shieldFlash = 0; this.alarm = 0; this.alarmColor = 'red';
    this.look = { x: 0, y: 0 }; this.cam = { x: WORLD / 2, y: WORLD / 2 };
    this.travel = { x: 0, y: 0 };
    this.shakeX = 0; this.shakeY = 0;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.starDpr = 0;
    this.starSprites = STAR_COLORS.map(color => ({ core: starSprite(color, 8), halo: starSprite(color, 32) }));
    this.starFields = STAR_LAYERS.map((spec, i) => spec.field ? starField(spec, i + 1) : null);
    this.resize();
  }

  resize() {
    const box = this.canvas.getBoundingClientRect();
    this.w = Math.max(1, box.width); this.h = Math.max(1, box.height);
    this.dpr = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.w * this.dpr); this.canvas.height = Math.round(this.h * this.dpr);
    this.zoom = clamp(Math.min(this.w, this.h * 1.78) / 1600, .42, 2.4);
    if (this.starDpr !== this.dpr) {
      this.starDpr = this.dpr;
      this.starTiles = STAR_LAYERS.map((spec, i) => spec.tile ? dustTile(spec, this.dpr, i + 1) : null);
    }
    this.resizeRadar();
  }

  // The radar is hidden on the title screens, so size it whenever it is laid out.
  resizeRadar() {
    const box = this.radar.getBoundingClientRect();
    const size = Math.round(box.width * Math.min(devicePixelRatio || 1, 2));
    if (size > 0 && this.radar.width !== size) { this.radar.width = size; this.radar.height = size; }
  }

  get viewport() { return { w: this.w / this.zoom, h: this.h / this.zoom }; }

  reset() {
    this.particles = []; this.popups = []; this.scheduled = []; this.wrecks = [];
    this.flashes.clear();
    this.shake = 0; this.hurt = 0; this.shieldFlash = 0; this.alarm = 0;
    this.look = { x: 0, y: 0 };
  }

  buildFortressDamage() {
    const img = this.sprites.fortress.image, size = img.naturalWidth, k = size / FORTRESS_SOURCE;
    const charred = canvas2d(size, size), g = charred.getContext('2d');
    g.drawImage(img, 0, 0);
    g.globalCompositeOperation = 'source-atop';
    g.fillStyle = 'rgba(14,9,7,.76)'; g.fillRect(0, 0, size, size);
    for (let i = 0; i < 320; i++) {
      g.fillStyle = `rgba(0,0,0,${.15 + hash(i) * .35})`;
      g.beginPath(); g.arc(hash(i * 3.3) * size, hash(i * 5.7) * size, (4 + hash(i * 9.1) * 22) * k * 1.3, 0, TAU); g.fill();
    }
    for (let i = 0; i < 90; i++) {
      g.fillStyle = `rgba(255,${90 + hash(i * 2.2) * 80},30,${.2 + hash(i * 4.4) * .4})`;
      g.fillRect(hash(i * 7.7) * size, hash(i * 8.8) * size, 2 * k + 1, 2 * k + 1);
    }
    this.charred = charred;
    const toWorld = px => px / k * FORTRESS_SCALE;
    this.podDamage = FORTRESS.pods.map(pod => {
      const cx = (CORE_SOURCE.x + pod.x / FORTRESS_SCALE) * k, cy = (CORE_SOURCE.y + pod.y / FORTRESS_SCALE) * k;
      const tx = (CORE_SOURCE.x + pod.tipX / FORTRESS_SCALE) * k, ty = (CORE_SOURCE.y + pod.tipY / FORTRESS_SCALE) * k;
      const r = 152 * k, half = 52 * k, a = Math.atan2(ty - cy, tx - cx), len = Math.hypot(tx - cx, ty - cy) + 18 * k;
      const ux = Math.cos(a), uy = Math.sin(a), px = -uy * half, py = ux * half;
      const corners = [[cx + px, cy + py], [cx - px, cy - py], [cx + ux * len - px, cy + uy * len - py], [cx + ux * len + px, cy + uy * len + py]];
      const xs = [cx - r, cx + r, ...corners.map(p => p[0])], ys = [cy - r, cy + r, ...corners.map(p => p[1])];
      const x0 = Math.floor(Math.max(0, Math.min(...xs))), y0 = Math.floor(Math.max(0, Math.min(...ys)));
      const x1 = Math.ceil(Math.min(size, Math.max(...xs))), y1 = Math.ceil(Math.min(size, Math.max(...ys)));
      const canvas = canvas2d(x1 - x0, y1 - y0), c = canvas.getContext('2d');
      c.translate(-x0, -y0);
      c.save(); c.beginPath(); c.arc(cx, cy, r, 0, TAU); c.clip(); c.drawImage(charred, 0, 0); c.restore();
      c.save(); c.beginPath(); corners.forEach(([x, y], i) => i ? c.lineTo(x, y) : c.moveTo(x, y)); c.closePath(); c.clip(); c.drawImage(charred, 0, 0); c.restore();
      return { canvas, x: toWorld(x0) - ART.fortress.width / 2, y: toWorld(y0) - ART.fortress.width * ART.fortress.pivotY,
        w: toWorld(x1 - x0), h: toWorld(y1 - y0) };
    });
  }

  // ---------- events ----------

  emitParticle(p) { if (this.particles.length < 1100) this.particles.push(p); }

  burst(x, y, size, style = 'fire') {
    const fire = style === 'violet' ? ['violet', 'pink', 'white'] : style === 'mine' ? ['red', 'pink', 'amber'] : ['amber', 'orange', 'ember'];
    const sparks = style === 'violet' ? ['green', 'white'] : style === 'cyan' ? ['cyan', 'white'] : ['amber', 'white', 'orange'];
    this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: size * 3.4, grow: 0, life: .16, max: .16, color: 'white', alpha: .95 });
    for (let i = 0; i < 3 + size / 9; i++) {
      const a = rand(0, TAU), v = rand(10, 60) * size / 30;
      this.emitParticle({ type: 'glow', x: x + rand(-.3, .3) * size, y: y + rand(-.3, .3) * size, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
        size: size * rand(.9, 1.6), grow: size * 1.2, life: rand(.3, .6), max: .6, color: pick(fire), alpha: .85 });
    }
    for (let i = 0; i < 8 + size * .6; i++) {
      const a = rand(0, TAU), v = rand(140, 520) * (.6 + size / 90);
      this.emitParticle({ type: 'spark', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, size: rand(1.4, 2.6), life: rand(.25, .7), max: .7,
        color: pick(sparks), alpha: 1, drag: 2.6 });
    }
    for (let i = 0; i < 2 + size / 9; i++) {
      const a = rand(0, TAU), v = rand(12, 50);
      this.emitParticle({ type: 'smoke', x: x + rand(-.4, .4) * size, y: y + rand(-.4, .4) * size, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
        size: size * rand(.7, 1.1), grow: size * 1.4, life: rand(.9, 1.7), max: 1.7, alpha: style === 'rock' ? .5 : .42 });
    }
    for (let i = 0; i < 3 + size / 7; i++) {
      const a = rand(0, TAU), v = rand(60, 260);
      this.emitParticle({ type: 'debris', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, angle: rand(0, TAU), spin: rand(-9, 9),
        size: rand(2, 5.5) * (style === 'rock' ? 1.3 : 1), life: rand(.6, 1.4), max: 1.4, shade: Math.floor(rand(55, 140)), drag: 1.2 });
    }
    if (size > 34) this.emitParticle({ type: 'ring', x, y, radius: size * 2.6, life: .55, max: .55, color: style === 'violet' ? 'violet' : 'amber', width: 3 });
    if (size > 50 && !this.reduced) this.shake = Math.max(this.shake, Math.min(16, size / 6));
  }

  dust(x, y, size) {
    for (let i = 0; i < 4 + size / 6; i++) {
      const a = rand(0, TAU), v = rand(15, 60);
      this.emitParticle({ type: 'smoke', x: x + rand(-.5, .5) * size, y: y + rand(-.5, .5) * size, vx: Math.cos(a) * v, vy: Math.sin(a) * v,
        size: size * rand(.6, 1), grow: size, life: rand(.8, 1.5), max: 1.5, alpha: .5 });
    }
    for (let i = 0; i < 5 + size / 5; i++) {
      const a = rand(0, TAU), v = rand(50, 200);
      this.emitParticle({ type: 'debris', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, angle: rand(0, TAU), spin: rand(-6, 6),
        size: rand(2.5, 6), life: rand(.7, 1.5), max: 1.5, shade: Math.floor(rand(60, 110)), drag: 1.4 });
    }
    for (let i = 0; i < 6; i++) {
      const a = rand(0, TAU), v = rand(120, 300);
      this.emitParticle({ type: 'spark', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, size: 1.4, life: rand(.15, .35), max: .35, color: 'amber', alpha: .8, drag: 3 });
    }
  }

  popup(x, y, text, color = '#ffcc00', big = false) {
    if (this.popups.length < 40) this.popups.push({ x, y, text, color, big, life: big ? 1.8 : 1.05, max: big ? 1.8 : 1.05 });
  }

  event(event) {
    const { x, y } = event;
    switch (event.type) {
      case 'shot':
        for (const sign of [1, -1]) {
          const d = sign > 0 ? 74 : 69;
          this.emitParticle({ type: 'glow', x: x + Math.cos(event.angle) * d * sign, y: y + Math.sin(event.angle) * d * sign, vx: 0, vy: 0,
            size: 26, grow: 0, life: .07, max: .07, color: 'cyan', alpha: .9, follow: true, offset: d * sign });
        }
        break;
      case 'enemyShot':
        this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: 30, grow: 0, life: .1, max: .1, color: event.kind === 'plasma' ? 'green' : 'orange', alpha: .9 });
        break;
      case 'spark':
        if (event.id) this.flashes.set('e' + event.id, .1);
        for (let i = 0; i < 6; i++) {
          const a = rand(0, TAU), v = rand(80, 260);
          this.emitParticle({ type: 'spark', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, size: 1.6, life: rand(.1, .28), max: .28,
            color: event.kind === 'rock' ? 'amber' : pick(['cyan', 'white']), alpha: .9, drag: 3 });
        }
        this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: 18, grow: 10, life: .1, max: .1, color: 'white', alpha: .7 });
        break;
      case 'deflect':
        this.emitParticle({ type: 'ring', x, y, radius: 22, life: .25, max: .25, color: 'cyan', width: 1.5 });
        for (let i = 0; i < 5; i++) {
          const a = rand(0, TAU), v = rand(150, 320);
          this.emitParticle({ type: 'spark', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, size: 1.4, life: .2, max: .2, color: 'white', alpha: .8, drag: 3 });
        }
        break;
      case 'podHit': this.flashes.set('p' + event.base + ':' + event.index, .12); break;
      case 'coreHit': this.burst(x, y, 10); break;
      case 'explosion':
        if (event.kind === 'rock') this.dust(x, y, event.size);
        else this.burst(x, y, event.size, event.kind === 'gunner' ? 'violet' : event.kind === 'mine' ? 'mine' : 'fire');
        if (event.points) this.popup(x, y, '+' + event.points.toLocaleString());
        break;
      case 'exposed':
        this.emitParticle({ type: 'ring', x, y, radius: 140, life: .6, max: .6, color: 'red', width: 3 });
        this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: 220, grow: 0, life: .3, max: .3, color: 'red', alpha: .9 });
        break;
      case 'base': {
        this.wrecks.push({ x, y, rot: event.rot, age: 0, life: 1.7, pods: event.pods });
        const c = Math.cos(event.rot), s = Math.sin(event.rot);
        for (let i = 0; i < 11; i++) {
          const a = rand(0, TAU), r = rand(20, 190), ox = Math.cos(a) * r, oy = Math.sin(a) * r * .8;
          this.scheduled.push({ t: i * .075 + rand(0, .05), x: x + ox * c - oy * s, y: y + ox * s + oy * c, size: rand(38, 70) });
        }
        this.scheduled.push({ t: .9, x, y, size: 210, final: true });
        this.popup(x, y, (event.direct ? 'DIRECT HIT  +' : '+') + event.points.toLocaleString(), event.direct ? '#ff9900' : '#ffcc00', true);
        break;
      }
      case 'hurt': this.hurt = 1; if (!this.reduced) this.shake = Math.max(this.shake, 9); break;
      case 'shieldHit': this.shieldFlash = 1; if (!this.reduced) this.shake = Math.max(this.shake, 4); break;
      case 'pulse':
        this.emitParticle({ type: 'ring', x, y, radius: event.radius, life: .55, max: .55, color: 'cyan', width: 6, follow: true });
        this.emitParticle({ type: 'ring', x, y, radius: event.radius * .7, life: .4, max: .4, color: 'white', width: 2, follow: true });
        this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: 260, grow: 200, life: .3, max: .3, color: 'cyan', alpha: .8, follow: true });
        break;
      case 'death':
        this.burst(x, y, 90);
        this.scheduled.push({ t: .18, x: x + 30, y: y - 20, size: 55 }, { t: .34, x: x - 25, y: y + 18, size: 60 });
        this.hurt = 1;
        break;
      case 'pickup':
        this.emitParticle({ type: 'glow', x, y, vx: 0, vy: 0, size: 70, grow: 40, life: .25, max: .25, color: event.kind === 'repair' ? 'ftl' : 'amber', alpha: .9 });
        this.popup(x, y, event.kind === 'repair' ? '+' + event.amount + ' HULL' : '+' + event.amount + ' SALVAGE', event.kind === 'repair' ? '#6ef08a' : '#ff9900');
        break;
      case 'condition':
        this.alarm = event.level === 'GREEN' ? 0 : 1;
        this.alarmColor = event.level === 'RED' ? 'red' : 'amber';
        break;
      default: break;
    }
  }

  // ---------- world drawing ----------

  toScreen(x, y) {
    return { x: this.w / 2 + delta(x, this.cam.x) * this.zoom + this.shakeX, y: this.h / 2 + delta(y, this.cam.y) * this.zoom + this.shakeY };
  }

  // Put the drawing origin on a world point, in world units. False when off screen.
  place(x, y, margin = 200, angle = 0) {
    const s = this.toScreen(x, y), m = margin * this.zoom;
    if (s.x < -m || s.x > this.w + m || s.y < -m || s.y > this.h + m) return false;
    const k = this.zoom * this.dpr;
    if (angle) {
      const c = Math.cos(angle) * k, n = Math.sin(angle) * k;
      this.ctx.setTransform(c, n, -n, c, s.x * this.dpr, s.y * this.dpr);
    } else this.ctx.setTransform(k, 0, 0, k, s.x * this.dpr, s.y * this.dpr);
    return true;
  }

  art(key, alpha = 1) {
    const sprite = this.sprites[key], spec = ART[key];
    if (!sprite.loaded) return false;
    const w = spec.width, h = w * sprite.aspect;
    this.ctx.globalAlpha = alpha;
    this.ctx.drawImage(sprite.pick(w * this.zoom * this.dpr), -w / 2, -h * (spec.pivotY ?? .5), w, h);
    this.ctx.globalAlpha = 1;
    return true;
  }

  flames(key, thrust, seed = 0) {
    const spec = ART[key], w = spec.width, c = this.ctx, tex = this.tex.flame[spec.flame];
    const flicker = this.reduced ? 1 : 1 + Math.sin(this.clock * 53 + seed) * .07 + Math.sin(this.clock * 31 + seed * 2) * .05;
    for (const [fx, fy, length, height] of spec.engines) {
      const len = w * (.12 + thrust * .3) * length * flicker, hgt = w * height * (.9 + thrust * .25);
      c.drawImage(tex, fx * w - len + w * .01, fy * w - hgt / 2, len, hgt);
    }
  }

  starfield(scene) {
    const c = this.ctx, W = this.canvas.width, H = this.canvas.height, dpr = this.dpr;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    c.fillStyle = '#000'; c.fillRect(0, 0, W, H);
    const flying = scene === 'play', slow = this.reduced ? .2 : 1;
    STAR_LAYERS.forEach((spec, i) => {
      // Offsets in CSS px. Travel is unwrapped, so crossing the sector seam never makes the sky jump.
      const ox = flying ? this.travel.x * spec.parallax * this.zoom : this.clock * spec.drift * slow;
      const oy = flying ? this.travel.y * spec.parallax * this.zoom : this.clock * spec.drift * .35 * slow;
      if (spec.field) {
        const f = spec.field;
        c.globalCompositeOperation = 'lighter';
        for (const star of this.starFields[i]) {
          const sprite = this.starSprites[star.color];
          const sx = (((star.x - ox) % f) + f) % f, sy = (((star.y - oy) % f) + f) % f;
          for (let x = sx; x < this.w + 6; x += f) for (let y = sy; y < this.h + 6; y += f) {
            const r = star.size * 2.2 * dpr;
            c.globalAlpha = star.mag;
            c.drawImage(sprite.core, x * dpr - r, y * dpr - r, r * 2, r * 2);
            if (spec.halo && star.mag > .55) {
              const R = r * 3.4;
              c.globalAlpha = star.mag * .2;
              c.drawImage(sprite.halo, x * dpr - R, y * dpr - R, R * 2, R * 2);
            }
          }
        }
        c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
        return;
      }
      let tile, tw, th;
      if (spec.image) {
        if (!this.deepStars.complete || !this.deepStars.naturalWidth) return;
        tile = this.deepStars; tw = this.deepStars.naturalWidth * dpr; th = this.deepStars.naturalHeight * dpr;
      } else { tile = this.starTiles[i]; tw = tile.width; th = tile.height; }
      const px = ox * dpr, py = oy * dpr;
      const sx = -(((px % tw) + tw) % tw), sy = -(((py % th) + th) % th);
      for (let x = sx; x < W; x += tw) for (let y = sy; y < H; y += th) c.drawImage(tile, x, y, tw, th);
    });
  }

  drawRocks(game) {
    const c = this.ctx;
    for (const rock of game.rocks) {
      if (rock.dead || !this.place(rock.x, rock.y, rock.radius * 1.4)) continue;
      const r = rock.radius * 1.14;
      c.drawImage(this.tex.rocks[rock.variant & 7], -r, -r, r * 2, r * 2);
    }
  }

  drawMines(game) {
    const c = this.ctx, t = this.tex.mine;
    for (const mine of game.mines) {
      if (mine.dead) continue;
      if (!this.place(mine.x, mine.y, 60, mine.phase + this.clock * .5)) continue;
      c.drawImage(t.spikes, -24, -24, 48, 48);
      this.place(mine.x, mine.y, 60);
      c.drawImage(t.body, -24, -24, 48, 48);
      const blink = this.reduced ? .7 : Math.pow(.5 + .5 * Math.sin(this.clock * 4.2 + mine.phase), 3);
      c.globalCompositeOperation = 'lighter'; c.globalAlpha = .25 + blink * .75;
      c.drawImage(this.tex.glow.red, -14, -14, 28, 28);
      c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    }
  }

  drawIris(closed) {
    const c = this.ctx, R = 31, inner = R * (1 - closed), twist = .45 * closed;
    for (let i = 0; i < 6; i++) {
      const a = i / 6 * TAU, b = a + TAU / 6;
      const grad = c.createLinearGradient(Math.cos(a) * R, Math.sin(a) * R, 0, 0);
      grad.addColorStop(0, '#3a3f45'); grad.addColorStop(1, '#16191c');
      c.fillStyle = grad;
      c.beginPath();
      c.moveTo(Math.cos(a) * R, Math.sin(a) * R); c.lineTo(Math.cos(b) * R, Math.sin(b) * R);
      c.lineTo(Math.cos(b + twist) * inner, Math.sin(b + twist) * inner); c.lineTo(Math.cos(a + twist) * inner, Math.sin(a + twist) * inner);
      c.closePath(); c.fill();
      c.strokeStyle = 'rgba(255,255,255,.12)'; c.lineWidth = .8; c.stroke();
    }
  }

  drawBase(base) {
    const c = this.ctx;
    if (!this.place(base.x, base.y, 290, base.rot)) return;
    if (!this.art('fortress')) {
      c.strokeStyle = '#6a7a80'; c.lineWidth = 3;
      for (const pod of FORTRESS.pods) { c.beginPath(); c.arc(pod.x, pod.y, FORTRESS.pod, 0, TAU); c.stroke(); }
    }
    const exposed = base.pods.every(pod => pod.hp <= 0);
    if (this.podDamage) for (const pod of base.pods) {
      if (pod.hp > 0) continue;
      const d = this.podDamage[pod.index];
      c.drawImage(d.canvas, d.x, d.y, d.w, d.h);
    }
    c.globalCompositeOperation = 'lighter';
    for (const pod of base.pods) {
      const spec = FORTRESS.pods[pod.index];
      const flash = this.flashes.get('p' + base.id + ':' + pod.index) || 0;
      if (flash > 0) { c.globalAlpha = flash * 6; c.drawImage(this.tex.glow.white, spec.x - 45, spec.y - 45, 90, 90); }
      if (pod.hp <= 0) {
        const flicker = .35 + .3 * Math.sin(this.clock * 9 + pod.index * 2) + .15 * Math.sin(this.clock * 23 + pod.index);
        c.globalAlpha = clamp(flicker, 0, 1) * .6; c.drawImage(this.tex.glow.ember, spec.x - 30, spec.y - 30, 60, 60);
      }
    }
    const beat = .5 + .5 * Math.sin(this.clock * (exposed ? 7 : 2.4) + base.id);
    const power = exposed ? .8 + beat * .4 : .22 + base.hatch * .78 + beat * .08;
    const size = exposed ? 150 + beat * 40 : 70 + base.hatch * 90;
    c.globalAlpha = clamp(power, 0, 1);
    c.drawImage(this.tex.glow.red, -size / 2, -size / 2, size, size);
    if (base.hatch > .5 && !exposed) { c.globalAlpha = (base.hatch - .5) * 2 * (.6 + beat * .4); c.drawImage(this.tex.glow.white, -16, -16, 32, 32); }
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    if (!exposed && base.hatch < 1) this.drawIris(1 - base.hatch);
    if (base.hatch > .55 && !exposed) {
      c.strokeStyle = `rgba(255,74,61,${.45 + beat * .45})`; c.lineWidth = 2; c.setLineDash([6, 6]);
      c.beginPath(); c.arc(0, 0, 50 + beat * 4, 0, TAU); c.stroke(); c.setLineDash([]);
    }
    if (exposed) {
      const segments = 12, filled = Math.ceil(clamp(base.hp / base.maxHp, 0, 1) * segments);
      c.lineWidth = 5;
      for (let i = 0; i < segments; i++) {
        const a = -Math.PI / 2 - base.rot + i / segments * TAU;
        c.strokeStyle = i < filled ? 'rgba(255,120,60,.95)' : 'rgba(80,30,26,.7)';
        c.beginPath(); c.arc(0, 0, 58, a + .05, a + TAU / segments - .05); c.stroke();
      }
    }
  }

  drawWrecks(dt) {
    const c = this.ctx;
    for (const wreck of this.wrecks) {
      wreck.age += dt;
      if (!this.charred || !this.place(wreck.x, wreck.y, 300, wreck.rot)) continue;
      const t = wreck.age / wreck.life, w = ART.fortress.width, scale = 1 + t * .05;
      c.globalAlpha = clamp(1 - t * t, 0, 1);
      c.drawImage(this.charred, -w / 2 * scale, -w * ART.fortress.pivotY * scale, w * scale, w * scale);
      c.globalAlpha = 1;
      if (Math.random() < dt * 30 * (1 - t)) {
        const a = rand(0, TAU), r = rand(0, 170);
        this.emitParticle({ type: 'smoke', x: wreck.x + Math.cos(a) * r, y: wreck.y + Math.sin(a) * r, vx: rand(-20, 20), vy: rand(-20, 20),
          size: rand(40, 70), grow: 60, life: rand(1, 1.8), max: 1.8, alpha: .45 });
      }
    }
    this.wrecks = this.wrecks.filter(wreck => wreck.age < wreck.life);
  }

  drawPickups(game) {
    const c = this.ctx;
    for (const item of game.pickups) {
      const spin = item.kind === 'salvage' ? this.clock * 1.6 + item.id : 0;
      if (!this.place(item.x, item.y, 40)) continue;
      const fading = item.life < 5 ? .55 + .45 * Math.sin(this.clock * 12) : 1;
      c.globalCompositeOperation = 'lighter'; c.globalAlpha = .55 * fading;
      c.drawImage(item.kind === 'repair' ? this.tex.glow.ftl : this.tex.glow.amber, -30, -30, 60, 60);
      c.globalCompositeOperation = 'source-over'; c.globalAlpha = fading;
      this.place(item.x, item.y, 40, spin);
      const tex = item.kind === 'repair' ? this.tex.repair : this.tex.cog;
      c.drawImage(tex, -14, -14, 28, 28);
      c.globalAlpha = 1;
    }
  }

  drawEnemies(game) {
    const c = this.ctx;
    for (const enemy of game.enemies) {
      if (!this.place(enemy.x, enemy.y, 120, enemy.angle)) continue;
      const key = enemy.kind === 'gunner' ? 'gunner' : 'chaser';
      c.globalCompositeOperation = 'lighter';
      this.flames(key, .55, enemy.id);
      c.globalCompositeOperation = 'source-over';
      if (!this.art(key)) {
        c.fillStyle = '#4a3a4a'; c.beginPath(); c.moveTo(40, 0); c.lineTo(-30, -30); c.lineTo(-18, 0); c.lineTo(-30, 30); c.closePath(); c.fill();
      }
      const flash = this.flashes.get('e' + enemy.id) || 0;
      if (flash > 0) { c.globalCompositeOperation = 'lighter'; this.art(key, flash * 6); c.globalCompositeOperation = 'source-over'; }
      if (enemy.leader) {
        this.place(enemy.x, enemy.y, 120, this.clock * 1.4);
        c.strokeStyle = 'rgba(255,204,0,.75)'; c.lineWidth = 1.6;
        for (let i = 0; i < 4; i++) { c.beginPath(); c.arc(0, 0, 66, i * Math.PI / 2 + .2, i * Math.PI / 2 + 1.1); c.stroke(); }
      }
    }
  }

  drawPlayer(game) {
    const p = game.player, c = this.ctx, w = ART.ship.width;
    if (game.mode === 'dead' || !this.place(p.x, p.y, 200, p.angle)) return;
    c.globalCompositeOperation = 'lighter';
    this.flames('ship', p.boosting ? 1.45 : .6, 3);
    c.globalCompositeOperation = 'source-over';
    let alpha = 1;
    if (p.invulnerable > 0 && game.mode === 'playing') alpha = this.reduced ? .75 : .55 + .45 * Math.abs(Math.sin(game.time * 14));
    if (!this.art('ship', alpha)) {
      c.fillStyle = '#7d8790'; c.beginPath(); c.moveTo(74, 0); c.lineTo(-60, -48); c.lineTo(-40, 0); c.lineTo(-60, 48); c.closePath(); c.fill();
    }
    if (p.shield > 1) {
      const strength = clamp(p.shield / game.maxShield, 0, 1);
      c.globalCompositeOperation = 'lighter';
      c.globalAlpha = clamp(.16 + strength * .26 + this.shieldFlash * .9, 0, 1);
      c.drawImage(this.tex.bubble, -w * .72, -w * .56, w * 1.44, w * 1.12);
      c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
    }
  }

  drawShots(game) {
    const c = this.ctx;
    c.globalCompositeOperation = 'lighter';
    for (const shot of game.shots) {
      if (shot.kind === 'plasma') {
        if (!this.place(shot.x, shot.y, 30)) continue;
        const pulse = 1 + Math.sin(this.clock * 30 + shot.x) * .1;
        c.drawImage(this.tex.glow.green, -16 * pulse, -16 * pulse, 32 * pulse, 32 * pulse);
        c.drawImage(this.tex.glow.white, -5, -5, 10, 10);
        continue;
      }
      if (!this.place(shot.x, shot.y, 40, shot.angle)) continue;
      if (shot.owner === 'player') c.drawImage(this.tex.bolt.cyan, -46 * .62, -7, 46, 14);
      else c.drawImage(this.tex.bolt.orange, -36 * .62, -7, 36, 14);
    }
    c.globalCompositeOperation = 'source-over';
  }

  updateParticles(dt, game) {
    const p = game.player;
    for (const part of this.particles) {
      part.life -= dt;
      if (part.follow) { part.x = p.x + (part.offset ? Math.cos(p.angle) * part.offset : 0); part.y = p.y + (part.offset ? Math.sin(p.angle) * part.offset : 0); }
      if (part.vx !== undefined) {
        if (part.drag) { const k = Math.exp(-part.drag * dt); part.vx *= k; part.vy *= k; }
        part.x += part.vx * dt; part.y += part.vy * dt;
      }
      if (part.grow) part.size += part.grow * dt;
      if (part.spin) part.angle += part.spin * dt;
    }
    this.particles = this.particles.filter(part => part.life > 0);
    for (const item of this.scheduled) item.t -= dt;
    for (const item of this.scheduled.filter(entry => entry.t <= 0)) {
      this.burst(item.x, item.y, item.size);
      if (item.final) {
        this.emitParticle({ type: 'ring', x: item.x, y: item.y, radius: 560, life: .9, max: .9, color: 'amber', width: 5 });
        this.emitParticle({ type: 'ring', x: item.x, y: item.y, radius: 380, life: .7, max: .7, color: 'white', width: 2 });
        if (!this.reduced) this.shake = 18;
      }
    }
    this.scheduled = this.scheduled.filter(entry => entry.t > 0);
    for (const [key, value] of this.flashes) { if (value - dt <= 0) this.flashes.delete(key); else this.flashes.set(key, value - dt); }
    for (const popup of this.popups) { popup.life -= dt; popup.y -= dt * (popup.big ? 26 : 48); }
    this.popups = this.popups.filter(popup => popup.life > 0);
  }

  drawParticles() {
    const c = this.ctx;
    for (const part of this.particles) {
      const t = part.life / part.max;
      if (part.type === 'smoke') {
        if (!this.place(part.x, part.y, part.size)) continue;
        c.globalAlpha = part.alpha * Math.min(1, t * 1.6) * Math.min(1, (1 - t) * 5 + .2);
        c.drawImage(this.tex.smoke, -part.size / 2, -part.size / 2, part.size, part.size);
      } else if (part.type === 'debris') {
        if (!this.place(part.x, part.y, 20, part.angle)) continue;
        c.globalAlpha = Math.min(1, t * 2);
        const v = part.shade;
        c.fillStyle = `rgb(${v},${v - 4},${v - 10})`;
        c.beginPath(); c.moveTo(part.size, 0); c.lineTo(-part.size * .7, -part.size * .6); c.lineTo(-part.size * .4, part.size * .7); c.closePath(); c.fill();
      }
    }
    c.globalAlpha = 1;
    c.globalCompositeOperation = 'lighter';
    for (const part of this.particles) {
      const t = part.life / part.max;
      if (part.type === 'glow') {
        if (!this.place(part.x, part.y, part.size)) continue;
        c.globalAlpha = part.alpha * t;
        c.drawImage(this.tex.glow[part.color], -part.size / 2, -part.size / 2, part.size, part.size);
      } else if (part.type === 'spark') {
        const speed = Math.hypot(part.vx, part.vy);
        if (!this.place(part.x, part.y, 30, Math.atan2(part.vy, part.vx))) continue;
        const len = clamp(speed * .045, 4, 26);
        c.globalAlpha = part.alpha * t;
        c.drawImage(this.tex.bolt[part.color === 'cyan' ? 'cyan' : part.color === 'white' ? 'white' : 'orange'], -len * .62, -part.size * 1.5, len, part.size * 3);
      } else if (part.type === 'ring') {
        if (!this.place(part.x, part.y, part.radius + 20)) continue;
        const r = part.radius * Math.sqrt(1 - t);
        c.globalAlpha = t * .9;
        c.strokeStyle = rgba(part.color, 1); c.lineWidth = part.width * (.4 + t);
        c.beginPath(); c.arc(0, 0, Math.max(1, r), 0, TAU); c.stroke();
      }
    }
    c.globalAlpha = 1; c.globalCompositeOperation = 'source-over';
  }

  drawPopups() {
    const c = this.ctx;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    c.textAlign = 'center'; c.textBaseline = 'middle';
    for (const popup of this.popups) {
      const s = this.toScreen(popup.x, popup.y), t = popup.life / popup.max;
      c.globalAlpha = Math.min(1, t * 3);
      c.font = (popup.big ? '600 20px' : '13px') + ' "Share Tech Mono", ui-monospace, monospace';
      c.fillStyle = 'rgba(0,0,0,.7)'; c.fillText(popup.text, s.x + 1, s.y + 1);
      c.fillStyle = popup.color; c.fillText(popup.text, s.x, s.y);
    }
    c.globalAlpha = 1;
  }

  // Off-screen station markers, clamped to the playfield edge.
  drawMarkers(game) {
    const c = this.ctx, margin = 34;
    const bases = game.bases.filter(base => !base.dead).sort((a, b) => distance(a, game.player) - distance(b, game.player));
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    for (const base of bases.slice(0, 4)) {
      const s = this.toScreen(base.x, base.y);
      if (s.x > -60 && s.x < this.w + 60 && s.y > -60 && s.y < this.h + 60) continue;
      const angle = Math.atan2(s.y - this.h / 2, s.x - this.w / 2);
      const rx = this.w / 2 - margin, ry = this.h / 2 - margin - 40;
      const t = Math.min(rx / (Math.abs(Math.cos(angle)) || 1e-3), ry / (Math.abs(Math.sin(angle)) || 1e-3));
      const x = this.w / 2 + Math.cos(angle) * t, y = this.h / 2 + 20 + Math.sin(angle) * t;
      const exposed = base.pods.every(pod => pod.hp <= 0);
      const color = exposed ? '#ff4a3d' : '#6ef08a';
      c.save(); c.translate(x, y); c.rotate(angle);
      c.fillStyle = color; c.globalAlpha = .9;
      c.beginPath(); c.moveTo(10, 0); c.lineTo(-4, -7); c.lineTo(-1, 0); c.lineTo(-4, 7); c.closePath(); c.fill();
      c.restore();
      c.font = '11px "Share Tech Mono", ui-monospace, monospace'; c.textBaseline = 'middle';
      c.textAlign = x < this.w / 2 ? 'left' : 'right';
      c.fillStyle = color; c.globalAlpha = .85;
      const range = distance(base, game.player);
      c.fillText((range / 1000).toFixed(1) + 'K', x + (x < this.w / 2 ? 14 : -14), y);
      c.globalAlpha = 1;
    }
  }

  drawScreenEffects(game, dt) {
    const c = this.ctx, w = this.w, h = this.h;
    c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const low = game.mode === 'playing' && game.player.hull < game.maxHull * .3;
    const lowPulse = low ? (.25 + .2 * Math.sin(this.clock * 5)) : 0;
    const red = Math.max(this.hurt * .55, lowPulse);
    if (red > .01) {
      const g = c.createRadialGradient(w / 2, h / 2, Math.min(w, h) * .3, w / 2, h / 2, Math.max(w, h) * .75);
      g.addColorStop(0, 'rgba(255,40,30,0)'); g.addColorStop(1, `rgba(255,40,30,${red})`);
      c.fillStyle = g; c.fillRect(0, 0, w, h);
    }
    if (this.alarm > .01) {
      const g = c.createRadialGradient(w / 2, h / 2, Math.min(w, h) * .4, w / 2, h / 2, Math.max(w, h) * .8);
      g.addColorStop(0, rgba(this.alarmColor, 0)); g.addColorStop(1, rgba(this.alarmColor, this.alarm * .35));
      c.fillStyle = g; c.fillRect(0, 0, w, h);
    }
    this.hurt = Math.max(0, this.hurt - dt * 2.2);
    this.shieldFlash = Math.max(0, this.shieldFlash - dt * 2.8);
    this.alarm = Math.max(0, this.alarm - dt * .9);
  }

  drawRadar(game) {
    const c = this.rc, size = this.radar.width, p = game.player, scale = size / WORLD;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.fillStyle = '#03050a'; c.fillRect(0, 0, size, size);
    c.strokeStyle = 'rgba(153,153,255,.12)'; c.lineWidth = 1;
    c.beginPath();
    for (let i = 1; i < 4; i++) { const v = Math.round(size * i / 4) + .5; c.moveTo(v, 0); c.lineTo(v, size); c.moveTo(0, v); c.lineTo(size, v); }
    c.stroke();
    const point = obj => ({ x: size / 2 + delta(obj.x, p.x) * scale, y: size / 2 + delta(obj.y, p.y) * scale });
    const view = this.viewport;
    c.strokeStyle = 'rgba(233,226,207,.32)';
    c.strokeRect(size / 2 - view.w * scale / 2, size / 2 - view.h * scale / 2, view.w * scale, view.h * scale);
    const u = Math.max(2, size / 70);
    for (const base of game.bases) {
      if (base.dead) continue;
      const s = point(base), exposed = base.pods.every(pod => pod.hp <= 0);
      const blink = exposed && Math.sin(this.clock * 8) < 0;
      c.fillStyle = exposed ? (blink ? '#5a1a14' : '#ff4a3d') : '#6ef08a';
      c.fillRect(s.x - u * 1.6, s.y - u * 1.6, u * 3.2, u * 3.2);
    }
    for (const enemy of game.enemies) {
      const s = point(enemy);
      c.fillStyle = enemy.leader ? '#ffcc00' : '#ff5a4a';
      c.fillRect(s.x - u * .55, s.y - u * .55, u * 1.1, u * 1.1);
    }
    c.translate(size / 2, size / 2); c.rotate(p.angle);
    c.fillStyle = '#ffffff';
    c.beginPath(); c.moveTo(u * 2.4, 0); c.lineTo(-u * 1.6, -u * 1.5); c.lineTo(-u * .8, 0); c.lineTo(-u * 1.6, u * 1.5); c.closePath(); c.fill();
    c.setTransform(1, 0, 0, 1, 0, 0);
  }

  heroShip() {
    const c = this.ctx, w = this.w, h = this.h, sprite = this.sprites.ship;
    const mobile = w < 700;
    const size = mobile ? Math.min(w * 1.05, 560) : Math.min(w * .44, h * .8, 780);
    const x = mobile ? w * .62 : w * .64, y = mobile ? h * .56 : h * .58;
    const bob = this.reduced ? 0 : Math.sin(this.clock * .6) * 6;
    const angle = -.42 + (this.reduced ? 0 : Math.sin(this.clock * .25) * .015);
    const k = this.dpr * size / ART.ship.width;
    const cs = Math.cos(angle) * k, sn = Math.sin(angle) * k;
    c.setTransform(cs, sn, -sn, cs, x * this.dpr, (y + bob) * this.dpr);
    c.globalCompositeOperation = 'lighter';
    this.flames('ship', .9, 1);
    c.globalCompositeOperation = 'source-over';
    if (sprite.loaded) c.drawImage(sprite.pick(size * this.dpr), -ART.ship.width / 2, -ART.ship.width * sprite.aspect / 2, ART.ship.width, ART.ship.width * sprite.aspect);
    c.setTransform(1, 0, 0, 1, 0, 0);
  }

  draw(game, dt, scene = 'play') {
    this.clock += dt;
    const p = game.player;
    if (scene === 'play') {
      const ease = 1 - Math.exp(-dt * 2.5);
      this.look.x += (p.vx * .24 - this.look.x) * ease; this.look.y += (p.vy * .24 - this.look.y) * ease;
      const next = { x: p.x + this.look.x, y: p.y + this.look.y };
      this.travel.x += delta(next.x, this.cam.x); this.travel.y += delta(next.y, this.cam.y);
      this.cam = next;
      if (this.shake > .2 && !this.reduced) {
        this.shakeX = (Math.random() - .5) * this.shake; this.shakeY = (Math.random() - .5) * this.shake;
        this.shake *= Math.pow(.015, dt);
      } else { this.shakeX = this.shakeY = 0; this.shake = 0; }
    }
    this.starfield(scene);
    if (scene !== 'play') {
      if (scene === 'menu') this.heroShip();
      return;
    }
    const c = this.ctx;
    this.updateParticles(dt, game);
    this.drawRocks(game);
    this.drawMines(game);
    for (const base of game.bases) if (!base.dead) this.drawBase(base);
    this.drawWrecks(dt);
    for (const base of game.bases) {
      if (base.dead || !this.place(base.x, base.y, 300)) continue;
      for (const pod of base.pods) {
        if (pod.hp <= 0 && Math.random() < dt * 1.6) {
          this.emitParticle({ type: 'smoke', x: pod.x + rand(-14, 14), y: pod.y + rand(-14, 14), vx: rand(-12, 12), vy: rand(-12, 12),
            size: rand(18, 30), grow: 34, life: rand(1, 1.6), max: 1.6, alpha: .35 });
        }
      }
    }
    this.drawPickups(game);
    this.drawEnemies(game);
    this.drawPlayer(game);
    this.drawShots(game);
    this.drawParticles();
    c.setTransform(1, 0, 0, 1, 0, 0);
    this.drawPopups();
    this.drawMarkers(game);
    this.drawScreenEffects(game, dt);
    this.drawRadar(game);
  }
}
