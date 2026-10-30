import { Game, HANGAR, UPGRADES, cleanProfile, purchase, hangarCost, clamp } from './engine.mjs';
import { Renderer } from './renderer.mjs';

const $ = id => document.getElementById(id);
const paths = {
  sound: '<path d="M11 4 6 8H3v8h3l5 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',
  pause: '<path d="M8 5v14M16 5v14"/>',
  play: '<path d="m8 5 11 7-11 7z"/>',
  expand: '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6"/>',
  collapse: '<path d="M3 9h6V3m6 0v6h6M9 21v-6H3m18 0h-6v6"/>',
  burst: '<path d="m13 2-8 12h6l-1 8 9-13h-7zM3 3h4M2 7h3M18 17h4M17 21h4"/>',
  split: '<path d="M12 21V3m0 7-7 5V7m7 3 7 5V7M9 6l3-3 3 3M3 10l2-3 2 3m10 0 2-3 2 3"/>',
  bolt: '<path d="m13 2-8 12h6l-1 8 9-13h-7z"/>',
  hex: '<path d="m12 2 9 5v10l-9 5-9-5V7zM12 7l4 2v6l-4 2-4-2V9z"/>',
  shield: '<path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6zM8 12l3 3 5-6"/>',
  arrow: '<path d="m4 19 8-16 8 16-8-4zM9 20l3 2 3-2"/>',
  orbit: '<circle cx="12" cy="12" r="4"/><ellipse cx="12" cy="12" rx="11" ry="6" transform="rotate(-35 12 12)"/><path d="M17 2v4h4"/>',
  ring: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8"/><path d="M12 0v3m0 18v3M0 12h3m18 0h3"/>',
};
const icon = name => '<svg viewBox="0 0 24 24" aria-hidden="true">' + (paths[name] || paths.hex) + '</svg>';
document.querySelectorAll('[data-icon]').forEach(node => { node.innerHTML = icon(node.dataset.icon); });

// Local combat samples, synthesized system cues, and optional voice callouts.
class Sound {
  constructor() {
    this.enabled = false; this.context = null; this.master = null; this.noise = null; this.last = {}; this.voiceAt = 0;
    this.samples = {}; this.loading = null; this.voices = new Map(); this.loops = new Map();
  }
  unlock() {
    if (!this.enabled) return;
    try {
      const Type = window.AudioContext || window.webkitAudioContext;
      if (!Type) return;
      if (!this.context) {
        this.context = new Type();
        const compressor = this.context.createDynamicsCompressor();
        compressor.threshold.value = -12; compressor.knee.value = 12;
        compressor.ratio.value = 4; compressor.release.value = .18;
        this.master = this.context.createGain(); this.master.gain.value = .55;
        this.master.connect(compressor); compressor.connect(this.context.destination);
        const length = this.context.sampleRate, buffer = this.context.createBuffer(1, length, length);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
        this.noise = buffer;
        this.loadSamples();
      }
      if (this.context.state === 'suspended') this.context.resume().catch(() => {});
    } catch (_) { /* Audio is optional. */ }
  }
  loadSamples() {
    if (this.loading) return this.loading;
    const files = { shot: 'laser-shot.wav', explosion: 'explosion.wav', engine: 'engine-loop.wav', boost: 'boost-loop.wav', pulse: 'pulse.wav' };
    this.loading = Promise.all(Object.entries(files).map(async ([name, file]) => {
      try {
        const response = await fetch(new URL('assets/sounds/' + file + '?v=2', import.meta.url));
        if (!response.ok) return;
        this.samples[name] = await this.context.decodeAudioData(await response.arrayBuffer());
      } catch (_) { /* Keep synthesized effects if a local sample cannot load. */ }
    }));
    return this.loading;
  }
  track(source, nodes, kind = 'cue') {
    const clean = () => {
      source.disconnect(); nodes.forEach(node => node.disconnect()); this.voices.delete(source);
      if (this.loops.get(kind)?.source === source) this.loops.delete(kind);
    };
    source.onended = clean;
    this.voices.set(source, { kind, clean });
  }
  stop(source) {
    const voice = this.voices.get(source);
    if (!voice) return;
    try { source.stop(); } catch (_) { /* Already ended. */ }
    voice.clean();
  }
  suspend() {
    // Discard tails and scheduled cues so resuming never replays an old blast.
    for (const source of this.voices.keys()) this.stop(source);
    this.last = {};
    this.context?.suspend().catch(() => {});
    if ('speechSynthesis' in window) speechSynthesis.cancel();
  }
  get live() { return this.enabled && this.context && this.context.state === 'running'; }
  flight(playing, boosting) {
    if (!this.live) return;
    if (!playing) { this.stopLoop('engine'); this.stopLoop('boost'); return; }
    this.loop('engine', boosting ? .12 : .23);
    if (boosting) this.loop('boost', .38);
    else this.stopLoop('boost');
  }
  loop(name, volume) {
    let voice = this.loops.get(name);
    const c = this.context;
    // Swap a temporary noise bed for the decoded recording once it is ready.
    if (voice && voice.sample !== this.samples[name]) { this.stop(voice.source); voice = null; }
    if (!voice) {
      // Rapid trigger changes cannot accumulate fading engine loops.
      for (const [source, item] of this.voices) if (item.kind === name) this.stop(source);
      const source = c.createBufferSource(), gain = c.createGain(), filter = c.createBiquadFilter();
      source.buffer = this.samples[name] || this.noise; source.loop = true;
      filter.type = 'lowpass'; filter.frequency.value = this.samples[name] ? 12000 : name === 'engine' ? 180 : 650;
      gain.gain.value = 0;
      source.connect(filter); filter.connect(gain); gain.connect(this.master);
      this.track(source, [filter, gain], name);
      voice = { source, gain, sample: this.samples[name], volume: -1 };
      this.loops.set(name, voice); source.start();
    }
    if (voice.volume !== volume) {
      voice.gain.gain.cancelScheduledValues(c.currentTime);
      voice.gain.gain.setTargetAtTime(volume, c.currentTime, .045);
      voice.volume = volume;
    }
  }
  stopLoop(name) {
    const voice = this.loops.get(name);
    if (!voice) return;
    this.loops.delete(name);
    const t = this.context.currentTime;
    voice.gain.gain.cancelScheduledValues(t);
    voice.gain.gain.setTargetAtTime(0, t, .03);
    voice.source.stop(t + .18);
  }
  tone(from, to, duration, volume, type = 'sine', delay = 0) {
    const c = this.context, t = c.currentTime + delay, osc = c.createOscillator(), gain = c.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(from, t); osc.frequency.exponentialRampToValueAtTime(Math.max(1, to), t + duration);
    gain.gain.setValueAtTime(.0001, t); gain.gain.exponentialRampToValueAtTime(volume, t + .008);
    gain.gain.exponentialRampToValueAtTime(.0001, t + duration);
    osc.connect(gain); gain.connect(this.master); osc.start(t); osc.stop(t + duration + .03);
    this.track(osc, [gain]);
  }
  hiss(duration, volume, from, to, type = 'lowpass', delay = 0) {
    const c = this.context, t = c.currentTime + delay;
    const source = c.createBufferSource(), filter = c.createBiquadFilter(), gain = c.createGain();
    source.buffer = this.noise; source.loop = true;
    filter.type = type; filter.frequency.setValueAtTime(from, t); filter.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + duration);
    gain.gain.setValueAtTime(volume, t); gain.gain.exponentialRampToValueAtTime(.0001, t + duration);
    source.connect(filter); filter.connect(gain); gain.connect(this.master);
    source.start(t, Math.random() * .5); source.stop(t + duration + .03);
    this.track(source, [filter, gain]);
  }
  sample(name, volume, rate = 1, maxDuration = Infinity) {
    const buffer = this.samples[name];
    if (!buffer) return false;
    // Bound overlapping tails during rapid fire and mine-chain explosions.
    const playing = [...this.voices].filter(([, voice]) => voice.kind === name);
    if (playing.length >= (name === 'shot' ? 8 : 10)) this.stop(playing[0][0]);
    const c = this.context, t = c.currentTime, duration = Math.min(buffer.duration / rate, maxDuration);
    const source = c.createBufferSource(), gain = c.createGain();
    source.buffer = buffer; source.playbackRate.value = rate;
    gain.gain.setValueAtTime(volume, t);
    gain.gain.setValueAtTime(volume, t + Math.max(0, duration - .06));
    gain.gain.linearRampToValueAtTime(0, t + duration);
    source.connect(gain); gain.connect(this.master); this.track(source, [gain], name);
    source.start(t); source.stop(t + duration);
    return true;
  }
  throttled(key, gap) {
    const now = this.context.currentTime;
    if (now - (this.last[key] ?? -1) < gap) return true;
    this.last[key] = now; return false;
  }
  play(event) {
    if (!this.live) return;
    switch (event.type) {
      case 'shot':
        if (!this.throttled('shot', .05) && !this.sample('shot', .65)) {
          this.tone(1500, 320, .08, .05, 'square'); this.tone(760, 190, .07, .03, 'triangle');
        }
        break;
      case 'enemyShot': if (!this.throttled('enemy', .12)) this.tone(event.kind === 'plasma' ? 300 : 520, event.kind === 'plasma' ? 120 : 200, .14, .025, 'sawtooth'); break;
      case 'explosion': {
        if (this.throttled('boom', .04)) break;
        const big = (event.size || 30) > 45;
        if (!this.sample('explosion', big ? .85 : .6, big ? .9 : 1.1, big ? 1.8 : 1.25)) {
          this.hiss(big ? .8 : .45, big ? .32 : .2, big ? 1600 : 2400, 70); this.tone(big ? 120 : 160, 35, big ? .6 : .35, big ? .12 : .07);
        }
        break;
      }
      case 'base': {
        if (this.throttled('base', .08)) break;
        if (!this.sample('explosion', 1, .72)) {
          this.hiss(1.8, .5, 2600, 50); this.tone(95, 24, 1.6, .2); this.hiss(1.1, .3, 1200, 60, 'lowpass', .85); this.tone(70, 20, 1.2, .18, 'sine', .85);
        }
        break;
      }
      case 'hurt': this.hiss(.2, .2, 3200, 500, 'bandpass'); this.tone(190, 60, .22, .07, 'square'); break;
      case 'shieldHit': this.tone(900, 1700, .14, .05); this.tone(1700, 800, .18, .03, 'triangle'); break;
      case 'pickup': this.tone(880, 1320, .09, .05); this.tone(1320, 1760, .1, .04, 'sine', .07); break;
      case 'pulse':
        if (!this.sample('pulse', .85, .85)) { this.tone(60, 900, .5, .12); this.hiss(.5, .18, 300, 5000, 'bandpass'); }
        break;
      case 'clear': [523, 659, 784, 1046].forEach((f, i) => this.tone(f, f, .22, .06, 'triangle', i * .11)); break;
      case 'death':
        if (!this.sample('explosion', 1, .8, 1.65)) { this.hiss(1.6, .5, 2000, 40); this.tone(240, 30, 1.4, .15, 'sawtooth'); }
        break;
      case 'sector': this.tone(330, 660, .3, .05); break;
      case 'exposed': this.tone(200, 900, .35, .06, 'sawtooth'); break;
      case 'deflect': if (!this.throttled('deflect', .08)) this.tone(2400, 1800, .05, .02, 'triangle'); break;
      case 'formation': this.tone(440, 440, .08, .04, 'square'); this.tone(440, 440, .08, .04, 'square', .12); break;
      case 'condition': {
        if (event.level === 'GREEN') break;
        const red = event.level === 'RED', hi = red ? 880 : 700, lo = red ? 660 : 560;
        for (let i = 0; i < (red ? 3 : 2); i++) { this.tone(hi, hi, .16, .06, 'square', i * .36); this.tone(lo, lo, .16, .06, 'square', i * .36 + .18); }
        break;
      }
      default: break;
    }
  }
  say(text) {
    if (!this.enabled || !('speechSynthesis' in window)) return;
    const now = performance.now();
    if (now - this.voiceAt < 1200) return;
    this.voiceAt = now;
    try {
      speechSynthesis.cancel();
      const line = new SpeechSynthesisUtterance(text);
      line.rate = 1.05; line.pitch = .5; line.volume = .9;
      speechSynthesis.speak(line);
    } catch (_) { /* Voice is optional. */ }
  }
}

const embedded = new URLSearchParams(location.search).get('embedded') === '1' && window.parent !== window;
let profileId = '', profile = cleanProfile(null), profileReady = !embedded;
let game = new Game(), credited = 0, active = !embedded, screen = 'menu', raf = 0, lastTime = 0, redraw = false;
let hudTime = 0, toastUntil = 0, formationNotice = -100, lastBank = 0, startingBest = 0, pendingClear = 0, pendingDeath = 0;
let stillFrame = false, fireTap = false, leavingFullscreen = false;
const keys = new Set();
const touch = { x: 0, y: 0, boost: false, fire: false, pointer: null };
const mouse = { fire: false, steering: false, x: 0, y: 0 };
const pad = { x: 0, y: 0, fire: false, boost: false, pulse: false, start: false };
const audio = new Sound();
const renderer = new Renderer($('field'), $('radar'));

const cells = (id, count) => { const node = $(id); node.replaceChildren(...Array.from({ length: count }, () => document.createElement('i'))); return [...node.children]; };
const hullCells = cells('hull-bar', 20), shieldCells = cells('shield-pips', 4), boostCells = cells('boost-bars', 10), pulseCells = cells('pulse-bars', 6);

function storageKey() { return 'data-void-patrol-v1:' + profileId; }
function notice(message) { $('storage-notice').textContent = message; $('storage-notice').hidden = false; }
function save() {
  if (!profileReady) return;
  try { localStorage.setItem(storageKey(), JSON.stringify(profile)); }
  catch (_) { notice('Browser storage is unavailable. Upgrades and records will last only for this visit.'); }
}
function loadProfile(id) {
  profileId = id;
  profileReady = true;
  let raw = null;
  try {
    const text = localStorage.getItem(storageKey());
    if (text) raw = JSON.parse(text);
  } catch (_) { notice('Saved progress could not be read. This visit starts with a fresh hangar.'); }
  profile = cleanProfile(raw);
  audio.enabled = profile.sound;
  game = new Game({ hangar: profile.hangar });
  $('launch').disabled = false; $('hangar-launch').disabled = false; $('open-hangar').disabled = false;
  $('launch-note').textContent = 'Your ship flies at all times. Steer it, pull the trigger, and keep moving.';
  updatePreferences(); renderHangar(); updateRecords(); updateHud(); wake();
}

// Another tab may have banked or bought since this one loaded; start every write from the saved record.
function refreshProfile() {
  if (!profileReady) return;
  try {
    const text = localStorage.getItem(storageKey());
    if (text) profile = { ...cleanProfile(JSON.parse(text)), sound: profile.sound };
  } catch (_) { /* Keep the in-memory profile. */ }
}

function bank() {
  if (!profileReady || game.mode === 'ready') return;
  const gained = Math.max(0, game.salvage - credited);
  refreshProfile();
  profile.salvage = Math.min(1e9, profile.salvage + gained);
  credited = game.salvage;
  profile.best = Math.max(profile.best, Math.floor(game.score));
  profile.sector = Math.max(profile.sector, game.wave);
  save();
}

function updatePreferences() {
  $('sound').setAttribute('aria-pressed', String(profile.sound));
  $('sound').setAttribute('aria-label', profile.sound ? 'Mute sound and voice' : 'Enable sound and voice');
  $('sound').title = (profile.sound ? 'Sound on' : 'Sound off') + ' (M)';
}

function updateRecords() {
  $('record-best').textContent = profile.best.toLocaleString();
  $('record-sector').textContent = String(profile.sector);
  $('record-runs').textContent = String(profile.runs);
}

function renderHangar() {
  $('wallet').textContent = profile.salvage.toLocaleString();
  $('hangar-items').replaceChildren();
  for (const item of HANGAR) {
    const level = profile.hangar[item.id], maxed = level >= item.max, cost = hangarCost(item.id, level);
    const card = document.createElement('article');
    card.className = 'hangar-item';
    card.innerHTML = '<div class="hi-head">' + icon(item.icon) + '<div><h3>' + item.name.toUpperCase() + '</h3><span class="sysname">' + item.system + ' SYSTEM</span></div></div>'
      + '<div class="levels" role="img" aria-label="Level ' + level + ' of ' + item.max + '">' + Array.from({ length: item.max }, (_, i) => '<i class="' + (i < level ? 'filled' : '') + '"></i>').join('') + '</div>'
      + '<p>' + item.detail + '</p>';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'purchase'; button.dataset.upgrade = item.id;
    button.innerHTML = maxed ? '<span>MAX LEVEL</span>' : '<span>UPGRADE</span><span class="scrap"><i class="cog"></i>' + cost.toLocaleString() + '</span>';
    button.disabled = !profileReady || maxed || profile.salvage < cost;
    button.setAttribute('aria-label', maxed ? item.name + ' at maximum level' : 'Upgrade ' + item.name + ' for ' + cost + ' salvage');
    button.title = maxed ? 'Fully upgraded' : profile.salvage < cost ? 'Earn ' + (cost - profile.salvage) + ' more salvage' : 'Applies to every new run';
    button.addEventListener('click', () => {
      if (screen !== 'hangar') return;
      refreshProfile();
      if (!purchase(profile, item.id)) { renderHangar(); return; }
      save(); renderHangar(); updateHud();
      $('hangar-feedback').textContent = item.name + ' upgraded to level ' + profile.hangar[item.id] + '. Ready for your next run.';
      audio.unlock(); audio.play({ type: 'pickup' });
      $('hangar-items').querySelector('[data-upgrade="' + item.id + '"]')?.focus({ preventScroll: true });
    });
    card.append(button);
    $('hangar-items').append(card);
  }
}

function showScreen(next, focus = true) {
  screen = next; document.body.dataset.state = next; stillFrame = false;
  const screens = { menu: 'menu', hangar: 'hangar', paused: 'paused', upgrade: 'upgrade-screen', dead: 'gameover' };
  for (const [state, id] of Object.entries(screens)) $(id).hidden = state !== next;
  $('play-hud').hidden = next === 'menu' || next === 'hangar';
  $('pause').disabled = !['playing', 'paused'].includes(next);
  $('pause').setAttribute('aria-label', next === 'paused' ? 'Resume game' : 'Pause game');
  $('pause').querySelector('[data-icon]').innerHTML = icon(next === 'paused' ? 'play' : 'pause');
  $('pause').querySelector('.tool-label').textContent = next === 'paused' ? 'RESUME' : 'PAUSE';
  if (next === 'hangar') renderHangar();
  if (next === 'menu') updateRecords();
  if (focus) {
    const target = { playing: 'field', menu: 'launch', hangar: 'hangar-launch', paused: 'resume', dead: 'retry' }[next];
    if (target) $(target).focus({ preventScroll: true });
    if (next === 'upgrade') $('upgrade-choices').querySelector('button')?.focus({ preventScroll: true });
  }
  updateHud(); wake();
}

function clearInput() {
  keys.clear(); touch.x = touch.y = 0; touch.boost = touch.fire = false; touch.pointer = null; mouse.fire = mouse.steering = false; fireTap = false;
  $('stick').style.transform = '';
  $('touch-fire').classList.remove('held'); $('touch-boost').classList.remove('held');
  $('weapons').classList.remove('firing');
}

function toast(message, tone = '', seconds = 2.5) {
  const node = $('toast');
  node.textContent = message;
  node.className = 'toast visible' + (tone ? ' ' + tone : '');
  toastUntil = performance.now() + seconds * 1000;
}

function launch() {
  if (!profileReady || !['menu', 'hangar', 'dead'].includes(screen)) return;
  bank(); game = new Game({ hangar: profile.hangar }); credited = 0; lastBank = 0; pendingClear = pendingDeath = 0;
  renderer.reset(); refreshProfile();
  formationNotice = -100; startingBest = profile.best; profile.runs++; save(); clearInput();
  audio.unlock(); game.start(); showScreen('playing');
  toast('BREAK THE SIX CANNONS. THEN DESTROY THE CORE.', '', 4.5);
  audio.say('Blast off!');
}
function pause(focus = true) {
  if (game.mode !== 'playing') return;
  game.pause(); clearInput(); bank(); audio.suspend(); showScreen('paused', focus);
}
function resume() {
  if (!active || document.hidden || game.mode !== 'paused') return;
  clearInput(); audio.unlock(); game.resume(); showScreen('playing');
}
function goHangar() {
  bank(); clearInput(); audio.suspend(); game = new Game({ hangar: profile.hangar }); credited = 0; pendingClear = pendingDeath = 0;
  renderer.reset(); showScreen('hangar');
}
function firePulse() {
  if (game.mode !== 'playing') return;
  if (!game.pulse()) toast('NO PULSE CHARGES. CLEAR A SECTOR TO RECHARGE.', 'warn', 2);
}

function showUpgrades() {
  $('clear-label').textContent = 'SECTOR ' + String(game.wave).padStart(2, '0') + ' CLEAR';
  $('upgrade-choices').replaceChildren();
  for (const id of game.choices) {
    const upgrade = UPGRADES.find(item => item.id === id) || { id: 'repair', name: 'Field service', tag: 'SUPPORT', icon: 'shield', detail: 'Fully repair your hull and bank 60 extra salvage.', max: 0 };
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'upgrade-card'; button.dataset.choice = id;
    button.innerHTML = icon(upgrade.icon) + '<span class="eyebrow">' + upgrade.tag + (upgrade.max ? ' / LEVEL ' + (game.upgrades[id] + 1) + ' OF ' + upgrade.max : '') + '</span><strong>' + upgrade.name.toUpperCase() + '</strong><span class="description">' + upgrade.detail + '</span><span class="choose">INSTALL AND JUMP</span>';
    button.addEventListener('click', () => {
      if (!active || !game.chooseUpgrade(id)) return;
      renderer.reset(); clearInput(); audio.unlock(); showScreen('playing');
      toast(upgrade.name.toUpperCase() + ' INSTALLED', 'good', 3);
      audio.say('Blast off!');
    });
    $('upgrade-choices').append(button);
  }
  showScreen('upgrade');
}

function showGameOver() {
  const record = game.score > startingBest && game.score > 0;
  $('record-label').textContent = record ? 'NEW HIGH SCORE' : 'SHIP DESTROYED';
  $('final-score').textContent = Math.floor(game.score).toLocaleString();
  $('final-sector').textContent = game.wave;
  $('final-salvage').textContent = game.salvage.toLocaleString();
  showScreen('dead');
}

function updateHud() {
  const p = game.player, inFlight = screen !== 'menu' && screen !== 'hangar';
  $('score').textContent = String(Math.floor(game.score)).padStart(6, '0');
  $('best').textContent = String(Math.max(profile.best, Math.floor(game.score))).padStart(6, '0');
  $('brand-tag').textContent = inFlight ? 'SECTOR ' + String(game.wave).padStart(2, '0') : 'READY';
  $('salvage-label').textContent = inFlight ? 'RUN SALVAGE' : 'SALVAGE';
  $('salvage').textContent = (inFlight ? game.salvage : profile.salvage).toLocaleString();
  const hull = clamp(p.hull / game.maxHull, 0, 1), lit = Math.ceil(hull * hullCells.length);
  hullCells.forEach((cell, i) => cell.classList.toggle('on', i < lit));
  $('hull-bar').classList.toggle('warn', hull < .5 && hull >= .25);
  $('hull-bar').classList.toggle('crit', hull < .25);
  $('hull-bar').setAttribute('aria-valuemax', String(game.maxHull)); $('hull-bar').setAttribute('aria-valuenow', String(Math.ceil(p.hull)));
  $('hull-value').textContent = Math.ceil(p.hull);
  const per = game.maxShield / shieldCells.length;
  shieldCells.forEach((cell, i) => {
    const fill = clamp((p.shield - i * per) / per, 0, 1);
    cell.classList.toggle('on', fill >= .999);
    cell.classList.toggle('part', fill > 0 && fill < .999);
    cell.style.setProperty('--p', fill.toFixed(3));
  });
  $('shield-pips').setAttribute('aria-valuemax', String(game.maxShield)); $('shield-pips').setAttribute('aria-valuenow', String(Math.floor(p.shield)));
  $('shield-value').textContent = Math.floor(p.shield);
  const energy = Math.round(p.energy / 10);
  boostCells.forEach((cell, i) => cell.classList.toggle('on', i < energy));
  $('sys-boost').classList.toggle('low', p.energy < 30);
  pulseCells.forEach((cell, i) => cell.classList.toggle('on', i < p.pulses));
  $('sys-pulse').classList.toggle('empty', p.pulses <= 0);
  $('condition').dataset.level = game.condition; $('condition').textContent = 'CONDITION ' + game.condition;
  const left = game.remaining;
  $('objective').textContent = left ? 'DESTROY ' + left + (left === 1 ? ' STATION' : ' STATIONS') : 'SECTOR CLEAR';
  $('combo').textContent = 'CHAIN x' + game.multiplier;
  $('radar-bases').textContent = left + (left === 1 ? ' STATION' : ' STATIONS');
  const mods = [['rapid', 'RAPID'], ['spread', 'SPLIT'], ['power', 'HEAVY']].filter(([id]) => game.upgrades[id]);
  $('weapon-mods').innerHTML = mods.length ? mods.map(([id, name]) => '<span>' + name + ' +' + game.upgrades[id] + '</span>').join('') : '<span class="none">STANDARD LOADOUT</span>';
  $('touch-pulse-count').textContent = p.pulses;
  $('touch-pulse').disabled = p.pulses <= 0;
  $('touch-boost').style.setProperty('--energy', (p.energy / 100).toFixed(3));
}

function consumeEvents() {
  for (const event of game.drainEvents()) {
    renderer.event(event); audio.play(event);
    switch (event.type) {
      case 'formation':
        if (game.time - formationNotice > 14) { toast('FORMATION INBOUND', 'warn', 2.2); audio.say('Battle station!'); formationNotice = game.time; }
        break;
      case 'condition':
        if (event.level === 'RED') { toast('CONDITION RED', 'alert', 2.6); audio.say('Condition red!'); }
        else if (event.level === 'YELLOW') { toast('CONDITION YELLOW', 'warn', 2.2); audio.say('Alert! Alert!'); }
        break;
      case 'exposed': toast('CORE EXPOSED. FINISH IT.', 'alert', 2.4); break;
      case 'leader': toast('FORMATION LEADER DOWN', 'good', 1.8); break;
      case 'base': if (game.remaining) toast((event.direct ? 'DIRECT HIT. ' : '') + 'STATION DESTROYED / ' + game.remaining + ' LEFT', 'good', 2.4); break;
      case 'clear': bank(); clearInput(); pendingClear = 1.5; toast('SECTOR CLEAR', 'good', 1.5); break;
      case 'death': bank(); clearInput(); pendingDeath = 1.7; break;
      default: break;
    }
  }
}

// ---------- input ----------
const padHeld = { pulse: false, start: false, confirm: false, dir: 0 };
function upgradeCards() { return [...$('upgrade-choices').querySelectorAll('.upgrade-card')]; }
function moveUpgradeFocus(dir) {
  const cards = upgradeCards(), index = cards.indexOf(document.activeElement);
  if (cards.length) cards[index < 0 ? 0 : (index + dir + cards.length) % cards.length].focus();
}
function chooseFocusedUpgrade() {
  const cards = upgradeCards();
  (cards.includes(document.activeElement) ? document.activeElement : cards[0])?.click();
}
function pollPad() {
  const gp = navigator.getGamepads ? [...navigator.getGamepads()].find(Boolean) : null;
  if (!gp) { pad.x = pad.y = 0; pad.fire = pad.boost = false; return false; }
  const down = i => !!gp.buttons[i]?.pressed;
  const ax = gp.axes[0] || 0, ay = gp.axes[1] || 0;
  // A circular dead zone preserves the stick's exact direction near either axis.
  if (Math.hypot(ax, ay) > .18) { pad.x = ax; pad.y = ay; }
  else {
    pad.x = (down(15) ? 1 : 0) - (down(14) ? 1 : 0);
    pad.y = (down(13) ? 1 : 0) - (down(12) ? 1 : 0);
  }
  pad.fire = down(0) || down(5);
  pad.boost = down(1) || (gp.buttons[7]?.value || 0) > .35;
  const pulse = down(2) || down(3), start = down(9), confirm = down(0) || start;
  const dir = pad.x > .5 || pad.y > .5 ? 1 : pad.x < -.5 || pad.y < -.5 ? -1 : 0;
  if (pulse && !padHeld.pulse) firePulse();
  if (screen === 'upgrade' && dir && dir !== padHeld.dir) moveUpgradeFocus(dir);
  if (start && !padHeld.start && game.mode === 'playing') pause();
  else if (confirm && !padHeld.confirm) {
    // A or Start: launch from the title or game-over screen, resume from pause, install an upgrade.
    if (game.mode === 'paused') resume();
    else if (screen === 'menu' || screen === 'dead') launch();
    else if (screen === 'upgrade') chooseFocusedUpgrade();
  }
  padHeld.pulse = pulse; padHeld.start = start; padHeld.confirm = confirm; padHeld.dir = dir;
  return true;
}
function readInput() {
  pollPad();
  // Consume each buffered press even while a trigger is held, so release cannot replay it.
  const tapped = takeTap();
  const kx = (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) - (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0);
  const ky = (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0) - (keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0);
  let x = 0, y = 0;
  // Select one complete vector so idle axes cannot borrow another device's input.
  // Moving a stick or pressing a direction takes over until the mouse moves again.
  if ([...keys].some(key => steeringKeys.has(key))) { x = kx; y = ky; mouse.steering = false; }
  else if (touch.pointer !== null) { x = touch.x; y = touch.y; mouse.steering = false; }
  else if (pad.x || pad.y) { x = pad.x; y = pad.y; mouse.steering = false; }
  else if (mouse.steering) {
    const box = field.getBoundingClientRect(), ship = renderer.toScreen(game.player.x, game.player.y);
    // Use the actual ship position, including camera look-ahead, but ignore shake.
    const dx = (mouse.x - box.left) * renderer.w / box.width - (ship.x - renderer.shakeX);
    const dy = (mouse.y - box.top) * renderer.h / box.height - (ship.y - renderer.shakeY);
    const length = Math.hypot(dx, dy);
    // Keep flying straight when the pointer is over the hull, instead of circling it.
    if (length > Math.max(24, 80 * renderer.zoom)) { x = dx / length; y = dy / length; }
  }
  return {
    x, y,
    fire: keys.has('Space') || mouse.fire || touch.fire || pad.fire || tapped,
    boost: keys.has('ShiftLeft') || keys.has('ShiftRight') || touch.boost || pad.boost,
  };
}

// A press and release inside one frame still fires once.
function takeTap() { const tapped = fireTap; fireTap = false; return tapped; }

function tick(time) {
  raf = 0;
  if (!active || document.hidden) return;
  const dt = lastTime ? Math.min(.05, (time - lastTime) / 1000) : 1 / 60;
  lastTime = time;
  if (screen === 'paused' || (screen === 'upgrade' && stillFrame)) {
    if (redraw) { renderer.draw(game, 0, 'play'); redraw = false; }
    if (pollPad() && !raf) raf = requestAnimationFrame(tick);
    return;
  }
  const padLive = (screen === 'menu' || screen === 'dead' || screen === 'upgrade') && pollPad();
  if (game.mode === 'playing') {
    const input = readInput();
    game.step(dt, input);
    $('weapons').classList.toggle('firing', !!input.fire);
    if (game.time - lastBank > 5) { bank(); lastBank = game.time; }
  }
  consumeEvents();
  audio.flight(game.mode === 'playing', game.player.boosting);
  if (performance.now() > toastUntil) $('toast').classList.remove('visible');
  const scene = screen === 'menu' ? 'menu' : screen === 'hangar' ? 'hangar' : 'play';
  const moving = screen === 'playing' || screen === 'menu' || screen === 'hangar' || screen === 'dead';
  renderer.draw(game, moving ? dt : 0, scene);
  redraw = false; stillFrame = screen === 'upgrade';
  $('cannon-charge').style.transform = 'scaleX(' + (1 - clamp(game.player.shotTimer / game.fireDelay, 0, 1)).toFixed(3) + ')';
  hudTime += dt;
  if (hudTime > .08) { updateHud(); hudTime = 0; }
  if (pendingClear > 0 && (pendingClear -= dt) <= 0) { pendingClear = 0; showUpgrades(); }
  if (pendingDeath > 0 && (pendingDeath -= dt) <= 0) { pendingDeath = 0; showGameOver(); }
  const busy = renderer.particles.length || renderer.scheduled.length;
  if (!raf && (screen === 'playing' || padLive || ((screen === 'menu' || screen === 'hangar') && !renderer.reduced) || (screen === 'dead' && busy))) raf = requestAnimationFrame(tick);
}
function wake() {
  if (!active || document.hidden || raf) return;
  lastTime = 0; raf = requestAnimationFrame(tick);
}
function setActive(value) {
  active = !!value;
  if (!active) { pause(false); bank(); clearInput(); audio.suspend(); cancelAnimationFrame(raf); raf = 0; }
  else { renderer.resize(); redraw = true; wake(); }
}

// ---------- fullscreen ----------
const fullscreenElement = () => document.fullscreenElement || document.webkitFullscreenElement;
async function toggleFullscreen() {
  try {
    if (fullscreenElement()) { leavingFullscreen = true; await (document.exitFullscreen ? document.exitFullscreen() : document.webkitExitFullscreen?.()); }
    else {
      const root = document.documentElement;
      await (root.requestFullscreen ? root.requestFullscreen({ navigationUI: 'hide' }) : root.webkitRequestFullscreen?.());
    }
  } catch (_) { leavingFullscreen = false; toast('FULLSCREEN IS UNAVAILABLE HERE', 'warn', 2.5); }
}
function syncFullscreen() {
  const on = !!fullscreenElement();
  $('fullscreen').setAttribute('aria-label', on ? 'Exit fullscreen' : 'Enter fullscreen');
  $('fullscreen').setAttribute('aria-pressed', String(on));
  $('fullscreen').querySelector('[data-icon]').innerHTML = icon(on ? 'collapse' : 'expand');
  $('fullscreen').querySelector('.tool-label').textContent = on ? 'EXIT FULL' : 'FULLSCREEN';
  renderer.resize(); redraw = true; wake();
  // In fullscreen the browser takes Escape to exit, so an exit the game did not ask for pauses the run.
  if (!on && !leavingFullscreen && game.mode === 'playing') pause();
  leavingFullscreen = false;
  if (screen === 'playing') $('field').focus({ preventScroll: true });
}
if (!(document.fullscreenEnabled || document.webkitFullscreenEnabled)) {
  $('fullscreen').disabled = true; $('fullscreen').title = 'Fullscreen is unavailable in this browser';
}
document.addEventListener('fullscreenchange', syncFullscreen);
document.addEventListener('webkitfullscreenchange', syncFullscreen);

// ---------- wiring ----------
$('launch').addEventListener('click', launch);
$('hangar-launch').addEventListener('click', launch);
$('retry').addEventListener('click', launch);
$('open-hangar').addEventListener('click', () => { if (screen === 'menu' && profileReady) showScreen('hangar'); });
$('hangar-back').addEventListener('click', () => { if (screen === 'hangar') showScreen('menu'); });
$('pause').addEventListener('click', () => game.mode === 'paused' ? resume() : pause());
$('resume').addEventListener('click', resume);
$('return-hangar').addEventListener('click', goHangar);
$('gameover-hangar').addEventListener('click', goHangar);
$('fullscreen').addEventListener('click', toggleFullscreen);
$('sound').addEventListener('click', () => {
  profile.sound = !profile.sound; audio.enabled = profile.sound;
  if (profile.sound) { audio.unlock(); audio.play({ type: 'pickup' }); } else audio.suspend();
  updatePreferences(); save();
});

const steeringKeys = new Set(['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight']);
const controls = new Set([...steeringKeys, 'Space', 'ShiftLeft', 'ShiftRight', 'KeyE']);
window.addEventListener('keydown', event => {
  if (event.ctrlKey || event.metaKey || event.altKey || !active || /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
  // A trigger held through a screen change must not press the button that just took focus.
  if (screen !== 'playing' && event.repeat && ['Space', 'Enter', 'NumpadEnter'].includes(event.code)) { event.preventDefault(); return; }
  if (event.code === 'KeyF' && !event.repeat) { event.preventDefault(); if (!$('fullscreen').disabled) toggleFullscreen(); return; }
  if (event.code === 'KeyM' && !event.repeat) { event.preventDefault(); $('sound').click(); return; }
  if (event.code === 'KeyP' || event.code === 'Escape') {
    event.preventDefault(); if (event.repeat) return;
    if (game.mode === 'playing') pause();
    else if (game.mode === 'paused') resume();
    else if (screen === 'hangar' && event.code === 'Escape') showScreen('menu');
    return;
  }
  if ((event.code === 'Enter' || event.code === 'NumpadEnter') && !(event.target instanceof HTMLButtonElement) && ['menu', 'dead'].includes(screen)) {
    event.preventDefault(); launch(); return;
  }
  if (game.mode !== 'playing' || !controls.has(event.code)) return;
  event.preventDefault(); keys.add(event.code);
  if (steeringKeys.has(event.code)) mouse.steering = false;
  if (event.code === 'Space' && !event.repeat) fireTap = true;
  if (event.code === 'KeyE' && !event.repeat) firePulse();
});
window.addEventListener('keyup', event => { keys.delete(event.code); });
window.addEventListener('blur', () => { clearInput(); pause(false); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { pause(false); clearInput(); audio.suspend(); cancelAnimationFrame(raf); raf = 0; }
  else { redraw = true; wake(); }
});
window.addEventListener('pagehide', () => { bank(); clearInput(); audio.suspend(); cancelAnimationFrame(raf); raf = 0; });
new ResizeObserver(() => { renderer.resize(); redraw = true; wake(); }).observe($('stage'));
new ResizeObserver(() => { renderer.resizeRadar(); redraw = true; wake(); }).observe($('radar'));
renderer.ready.then(ok => { if (!ok) notice('Some ship artwork could not load. Flight silhouettes stand in until it returns.'); redraw = true; wake(); });
window.addEventListener('gamepadconnected', () => { if (['paused', 'menu', 'dead', 'upgrade'].includes(screen)) wake(); });
// Re-render crisply when the window moves to a screen with a different pixel density.
(function watchDensity() {
  const query = window.matchMedia?.('(resolution: ' + (devicePixelRatio || 1) + 'dppx)');
  query?.addEventListener?.('change', () => { renderer.resize(); redraw = true; wake(); watchDensity(); }, { once: true });
})();
window.addEventListener('storage', event => {
  if (!profileReady || event.key !== storageKey()) return;
  refreshProfile(); renderHangar(); updateRecords(); updateHud();
});

// Point anywhere in the playfield to steer; the left button remains the trigger.
const field = $('field');
function steerMouse(event) {
  if (event.pointerType === 'touch' || !active || game.mode !== 'playing') return;
  const box = field.getBoundingClientRect();
  mouse.x = event.clientX; mouse.y = event.clientY;
  mouse.steering = mouse.x >= box.left && mouse.x <= box.right && mouse.y >= box.top && mouse.y <= box.bottom;
}
field.addEventListener('pointermove', steerMouse);
field.addEventListener('pointerleave', () => { mouse.steering = false; });
field.addEventListener('pointercancel', () => { mouse.steering = false; });
field.addEventListener('pointerdown', event => {
  if (event.pointerType === 'touch' || event.button !== 0 || game.mode !== 'playing') return;
  steerMouse(event);
  event.preventDefault(); mouse.fire = true; fireTap = true;
  field.setPointerCapture(event.pointerId); field.focus({ preventScroll: true });
});
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) field.addEventListener(type, event => { if (event.pointerType !== 'touch') mouse.fire = false; });
field.addEventListener('contextmenu', event => { if (screen === 'playing') event.preventDefault(); });

// Touch: virtual stick plus hold-to-fire and hold-to-boost buttons.
function moveStick(event) {
  const box = $('joystick').getBoundingClientRect();
  let x = (event.clientX - box.left - box.width / 2) / 40, y = (event.clientY - box.top - box.height / 2) / 40;
  const length = Math.hypot(x, y); if (length > 1) { x /= length; y /= length; }
  touch.x = x; touch.y = y; $('stick').style.transform = 'translate(' + x * 34 + 'px,' + y * 34 + 'px)';
}
$('joystick').addEventListener('pointerdown', event => {
  if (game.mode !== 'playing' || touch.pointer !== null) return;
  event.preventDefault(); touch.pointer = event.pointerId; $('joystick').setPointerCapture(event.pointerId); moveStick(event);
});
$('joystick').addEventListener('pointermove', event => { if (touch.pointer === event.pointerId) moveStick(event); });
for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) $('joystick').addEventListener(type, event => {
  if (touch.pointer !== event.pointerId) return;
  touch.pointer = null; touch.x = touch.y = 0; $('stick').style.transform = '';
});
function holdButton(id, key) {
  const button = $(id);
  button.addEventListener('pointerdown', event => {
    if (game.mode !== 'playing') return;
    event.preventDefault(); touch[key] = true; if (key === 'fire') fireTap = true;
    button.classList.add('held'); button.setPointerCapture(event.pointerId);
  });
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(type, () => { touch[key] = false; button.classList.remove('held'); });
  button.addEventListener('contextmenu', event => event.preventDefault());
}
holdButton('touch-fire', 'fire');
holdButton('touch-boost', 'boost');
$('touch-pulse').addEventListener('click', firePulse);

if (embedded) {
  $('launch').disabled = true; $('hangar-launch').disabled = true; $('open-hangar').disabled = true;
  $('launch-note').textContent = 'Connecting to your dashboard profile...';
  window.addEventListener('message', event => {
    if (event.source !== window.parent || event.origin !== location.origin) return;
    if (event.data?.type === 'data-arcade-init' && typeof event.data.profile === 'string' && event.data.profile.length > 0 && event.data.profile.length <= 100) {
      if (profileReady && profileId !== event.data.profile) { setActive(false); return; }
      if (!profileReady) loadProfile(event.data.profile);
      setActive(event.data.active !== false);
    }
    if (event.data?.type === 'data-arcade-visibility') setActive(event.data.active === true);
  });
  window.parent.postMessage({ type: 'data-arcade-ready' }, location.origin);
} else loadProfile('solo');
renderHangar(); updatePreferences(); updateRecords(); updateHud(); wake();
