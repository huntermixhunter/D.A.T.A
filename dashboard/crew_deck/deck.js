'use strict';
/* Crew Deck: engine.
   Rendering, navigation (rooms are convex, crew only cross walls through doors),
   sliding doors, crew behaviour, speech bubbles and the inspector UI. */
(async function () {
  await window.CrewAccess.ready;
  const CD = window.CD;
  const params = new URLSearchParams(location.search);
  const BRIDGE = location.origin;
  const KEY = ''; // Authentication is carried in headers.
  const dialogue = window.CrewDialogue ? new window.CrewDialogue() : null;
  let missionState = null, lastMissionEvent = 0, dialogueEpoch = 0;
  const conversationTimers = new Set();
  const deckSettings = () => missionState?.settings || {};
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const rand = (a, b) => a + Math.random() * (b - a);
  const nowMs = () => performance.now();
  const hexA = (hex, a) => { const n = parseInt(hex.slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };

  // ------------------------------------------------------------- assets
  const getJSON = (u) => fetch(u, { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`); return r.json(); });
  const loadImg = (src) => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('image ' + src)); i.src = src; });
  const [layout, spriteMeta] = await Promise.all([getJSON('assets/layout.json'), getJSON('assets/crew/sprites.json')]);
  const shipImg = await loadImg('assets/ship.webp');
  const spriteImgs = {};
  await Promise.all(spriteMeta.sprites.map(async (s) => { spriteImgs[s.slot] = await loadImg(`assets/crew/${s.slot}.png`); }));
  try { await document.fonts.ready; } catch (e) { /* fonts are optional */ }

  const SC = layout.sprite.scale, MX = layout.sprite.marginX, MY = layout.sprite.marginY;
  const FW = spriteMeta.w, FH = spriteMeta.h;
  const DW = FW * SC, DH = FH * SC;
  const SPEED = 58; // image px per second

  // ------------------------------------------------------------ geometry
  const rooms = {};
  for (const r of layout.rooms) {
    const [x0, y0, x1, y1] = r.rect;
    rooms[r.id] = Object.assign({}, r, { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0,
      ix0: x0 + MX, iy0: y0 + MY, ix1: x1 - MX, iy1: y1 - MY, doors: [], claims: new Map() });
  }
  const ROOM_LIST = Object.values(rooms);
  const CORE = rooms.core.core;

  // Pixel access to the ship art, used to sample floor colour for open doorways.
  const art = document.createElement('canvas');
  art.width = shipImg.naturalWidth; art.height = shipImg.naturalHeight;
  const artCtx = art.getContext('2d', { willReadFrequently: true });
  artCtx.drawImage(shipImg, 0, 0);
  function floorAt(x, y) {
    const d = artCtx.getImageData(Math.round(x) - 4, Math.round(y) - 4, 9, 9).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 200 || (d[i] + d[i + 1] + d[i + 2]) / 3 < 160) continue;
      r += d[i]; g += d[i + 1]; b += d[i + 2]; n++;
    }
    return n ? [r / n, g / n, b / n] : [204, 200, 195];
  }

  const doors = {};
  const layoutDoors = {};
  for (const d of layout.doors) layoutDoors[d.id] = d;
  for (const d of layout.doors) {
    const [bx, by, bw, bh] = d.bar;
    const orient = bw < bh ? 'v' : 'h';
    const sealed = d.a === 'space' || d.b === 'space';
    const door = { id: d.id, orient, sealed, added: !!d.added, a: d.a, b: d.b, cx: bx + bw / 2, cy: by + bh / 2, o: 0, want: -1e9, entry: {} };
    doors[d.id] = door;
    if (sealed) continue;
    const A = rooms[d.a], B = rooms[d.b];
    if (orient === 'v') {
      door.region = { x: A.x1, y: by - 3, w: B.x0 - A.x1, h: bh + 6 };
      door.entry[A.id] = { x: A.ix1, y: clamp(door.cy, A.iy0, A.iy1) };
      door.entry[B.id] = { x: B.ix0, y: clamp(door.cy, B.iy0, B.iy1) };
    } else {
      door.region = { x: bx - 3, y: A.y1, w: bw + 6, h: B.y0 - A.y1 };
      door.entry[A.id] = { x: clamp(door.cx, A.ix0, A.ix1), y: A.iy1 };
      door.entry[B.id] = { x: clamp(door.cx, B.ix0, B.ix1), y: B.iy0 };
    }
    if (door.added) {
      // Not in the art: stamp a copy of an existing door (bar plus frame).
      const [sx, sy, sw, sh] = layoutDoors[d.stamp].bar;
      door.src = { x: sx - 3, y: sy - 3, w: sw + 6, h: sh + 6 };
      door.dst = { x: door.cx - door.src.w / 2, y: door.cy - door.src.h / 2, w: door.src.w, h: door.src.h };
      door.clip = door.dst;
    } else {
      door.src = door.dst = door.clip = door.region;
    }
    const fa = floorAt(door.entry[A.id].x, door.entry[A.id].y);
    const fb = floorAt(door.entry[B.id].x, door.entry[B.id].y);
    door.floor = `rgb(${Math.round((fa[0] + fb[0]) / 2)},${Math.round((fa[1] + fb[1]) / 2)},${Math.round((fa[2] + fb[2]) / 2)})`;
    A.doors.push(door); B.doors.push(door);
  }
  const DOOR_LIST = Object.values(doors).filter((d) => !d.sealed);

  // Shortest route through doors. Rooms are convex rectangles, so a straight
  // line between two points inside one room never crosses a wall; the only
  // way between rooms is the entry point -> exit point segment of a door.
  function planPath(sx, sy, sroom, tx, ty, troom) {
    if (sroom === troom) return [{ x: tx, y: ty, room: troom }];
    const open = [{ room: sroom, x: sx, y: sy, cost: 0, prev: null, via: null }];
    const best = new Map([[sroom + '|', 0]]);
    let goal = null;
    while (open.length) {
      let bi = 0;
      for (let i = 1; i < open.length; i++) if (open[i].cost < open[bi].cost) bi = i;
      const cur = open.splice(bi, 1)[0];
      if (goal && cur.cost >= goal.total) break;
      if (cur.room === troom) {
        const total = cur.cost + Math.hypot(tx - cur.x, ty - cur.y);
        if (!goal || total < goal.total) goal = { node: cur, total };
        continue;
      }
      for (const d of rooms[cur.room].doors) {
        const other = d.a === cur.room ? d.b : d.a;
        const en = d.entry[cur.room], ex = d.entry[other];
        const c = cur.cost + Math.hypot(en.x - cur.x, en.y - cur.y) + Math.hypot(ex.x - en.x, ex.y - en.y) + 6;
        const k = other + '|' + d.id;
        if (best.has(k) && best.get(k) <= c) continue;
        best.set(k, c);
        open.push({ room: other, x: ex.x, y: ex.y, cost: c, prev: cur, via: d });
      }
    }
    if (!goal) return null;
    const pts = [{ x: tx, y: ty, room: troom }];
    for (let n = goal.node; n.prev; n = n.prev) {
      const d = n.via;
      pts.unshift({ x: d.entry[n.room].x, y: d.entry[n.room].y, room: n.room, door: d.id });
      pts.unshift({ x: d.entry[n.prev.room].x, y: d.entry[n.prev.room].y, room: n.prev.room });
    }
    return pts;
  }

  // --------------------------------------------------------------- crew
  const rosterKey = 'retail';
  const rosterForced = true;
  const roster = () => CD.ROSTERS[rosterKey];
  const assistantName = () => (S.mode === 'live' && missionState?.assistant?.name) || roster().computer;
  const capt = () => 'you';

  const torso = {};
  for (const s of spriteMeta.sprites) torso[s.slot] = s.torso_rgb;
  const rgb = (a, k = 1) => `rgb(${Math.round(a[0] * k)},${Math.round(a[1] * k)},${Math.round(a[2] * k)})`;
  // Readable text tint for dark panels: lift dark uniform colours toward white.
  const textTint = (a) => {
    const lum = 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
    const k = lum > 170 ? 0.15 : lum > 110 ? 0.35 : 0.5;
    return `rgb(${a.map((v) => Math.round(v + (255 - v) * k)).join(',')})`;
  };

  const crew = [];
  const crewByRole = {};

  function randomPoint(room, who) {
    let best = null, bestD = -1;
    for (let k = 0; k < 10; k++) {
      const x = rand(room.ix0, room.ix1), y = rand(room.iy0, room.iy1);
      let minD = 1e9;
      for (const o of crew) {
        if (o === who) continue;
        const end = o.path.length ? o.path[o.path.length - 1] : o;
        minD = Math.min(minD, Math.hypot(end.x - x, end.y - y));
      }
      if (minD > 24) return [x, y];
      if (minD > bestD) { bestD = minD; best = [x, y]; }
    }
    return best || [(room.ix0 + room.ix1) / 2, (room.iy0 + room.iy1) / 2];
  }
  function nearPoint(room, target) {
    for (let k = 0; k < 10; k++) {
      const a = rand(0, Math.PI * 2), r = rand(22, 30);
      const x = clamp(target.x + Math.cos(a) * r, room.ix0, room.ix1);
      const y = clamp(target.y + Math.sin(a) * r * 0.6, room.iy0, room.iy1);
      if (Math.hypot(x - target.x, y - target.y) > 16) return [x, y];
    }
    return null;
  }

  class Crew {
    constructor(role, idx) {
      this.role = role; this.idx = idx; this.def = CD.ROLES[role];
      this.img = spriteImgs[this.def.sprite];
      const t = torso[this.def.sprite] || [200, 200, 200];
      this.color = rgb(t); this.text = textTint(t);
      const p = randomPoint(rooms[this.def.home], null);
      this.x = p[0]; this.y = p[1]; this.room = this.def.home;
      this.path = []; this.claim = null; this.arriveMode = 'idle'; this.mode = 'idle'; this.dest = this.room;
      this.duty = null; this.queue = []; this.activity = 'Reporting for duty';
      this.idleUntil = nowMs() + rand(1200, 5000);
      this.face = Math.random() < 0.5 ? 1 : -1; this.phase = rand(0, 10);
      this.moving = false; this.waiting = false; this.bubble = null; this.history = []; this.chatWith = null;
    }
    get name() { return (S.mode === 'live' && missionState?.roster?.find(o => o.id === this.agentId)?.name) || roster().names[this.role]; }
    get agentId() { return roster().ids[this.role]; }
  }
  CD.ROLE_ORDER.forEach((role, i) => { const c = new Crew(role, i); crew.push(c); crewByRole[role] = c; });

  function releaseClaim(c) {
    if (!c.claim) return;
    const r = rooms[c.claim.room];
    if (r.claims.get(c.claim.i) === c) r.claims.delete(c.claim.i);
    c.claim = null;
  }
  function claimStation(c, room, pref) {
    const n = room.stations.length;
    for (let k = 0; k < n; k++) {
      const i = (pref + k) % n;
      const holder = room.claims.get(i);
      if (!holder || holder === c) { room.claims.set(i, c); c.claim = { room: room.id, i }; return room.stations[i]; }
    }
    return null;
  }
  function goTo(c, roomId, opts = {}) {
    const room = rooms[roomId];
    if (!room) return false;
    releaseClaim(c);
    let pt = null;
    if (opts.station != null) { const s = claimStation(c, room, opts.station); if (s) pt = [s[0], s[1]]; }
    if (!pt && opts.near) pt = nearPoint(room, opts.near);
    if (!pt) pt = randomPoint(room, c);
    pt = [clamp(pt[0], room.ix0, room.ix1), clamp(pt[1], room.iy0, room.iy1)];
    // Never abandon a door crossing half way: finish it, then re-route.
    let keep = [], sx = c.x, sy = c.y, sroom = c.room;
    if (c.path.length && c.path[0].door) { keep = [c.path[0]]; sx = keep[0].x; sy = keep[0].y; sroom = keep[0].room; }
    const p = planPath(sx, sy, sroom, pt[0], pt[1], roomId);
    if (!p) return false;
    c.path = keep.concat(p);
    c.arriveMode = opts.mode || 'idle';
    if (opts.activity) c.activity = opts.activity;
    c.mode = 'walk';
    c.dest = roomId;
    return true;
  }
  function arrive(c) {
    c.mode = c.arriveMode;
    if (c.chatWith) {
      const mate = c.chatWith;
      c.chatWith = null;
      if (!mate.duty && !mate.path.length && mate.room === c.room && mate.mode !== 'sleep') {
        c.face = mate.x < c.x ? -1 : 1;
        mate.face = c.x < mate.x ? -1 : 1;
        ambientExchange(c, mate);
      }
    }
  }
  function updateCrew(c, dt, t) {
    c.moving = false; c.waiting = false;
    if (!c.path.length) return;
    const wp = c.path[0];
    if (wp.door) {
      const d = doors[wp.door];
      d.want = t;
      if (d.o < 0.7) { c.waiting = true; return; } // wait for the door, FTL style
    } else {
      for (let k = 1; k < Math.min(3, c.path.length); k++) {
        const w = c.path[k];
        if (!w.door) continue;
        const d = doors[w.door];
        if (Math.hypot(d.cx - c.x, d.cy - c.y) < 46) d.want = t;
        break;
      }
    }
    const dx = wp.x - c.x, dy = wp.y - c.y, dist = Math.hypot(dx, dy);
    const step = SPEED * dt * (c.duty && c.duty.kind === 'alert' ? 1.5 : 1);
    if (dist <= step) {
      c.x = wp.x; c.y = wp.y; c.room = wp.room; c.path.shift();
      if (!c.path.length) arrive(c);
    } else {
      c.x += (dx / dist) * step; c.y += (dy / dist) * step; c.moving = true;
      if (Math.abs(dx) > 0.4) c.face = dx < 0 ? -1 : 1;
    }
  }
  function updateDoors(dt, t) {
    for (const d of DOOR_LIST) {
      const target = t - d.want < 420 ? 1 : 0;
      const sp = target ? 6 : 3.4;
      if (d.o < target) d.o = Math.min(target, d.o + sp * dt);
      else if (d.o > target) d.o = Math.max(target, d.o - sp * dt);
    }
  }

  // -------------------------------------------------------- ship state
  const S = {
    mode: 'live', labels: false, sel: null, hover: null,
    duties: [], roomAlerts: {}, inflight: 0, coreLabel: '', coreTarget: null, quiet: false, cond: 'green',
    firstSeen: new Map(), acks: loadAcks(), roomSt: {}, summarized: false,
    demo: null,
    exact: false, inferred: false, core: null, crewState: 'pending', crewScope: null,
  };
  function loadAcks() { try { return JSON.parse(localStorage.getItem('crewdeck.acks') || '{}'); } catch (e) { return {}; } }
  function saveAcks() { try { localStorage.setItem('crewdeck.acks', JSON.stringify(S.acks)); } catch (e) { /* private mode */ } }

  function applyDuties(list) {
    const seen = new Set();
    for (const d of list) {
      seen.add(d.key);
      if (!S.firstSeen.has(d.key)) S.firstSeen.set(d.key, d.since || Date.now());
      if (d.kind !== 'job' && d.kind !== 'alert') d.since = S.firstSeen.get(d.key);
    }
    for (const k of [...S.firstSeen.keys()]) if (!seen.has(k)) S.firstSeen.delete(k);
    S.duties = list;
    const byRole = {};
    for (const d of list) (byRole[d.role] = byRole[d.role] || []).push(d);
    for (const c of crew) {
      const mine = (byRole[c.role] || []).sort((a, b) => Number(!!b.exact) - Number(!!a.exact) || b.priority - a.priority || (b.since || 0) - (a.since || 0));
      const top = mine[0] || null;
      c.queue = mine.slice(1);
      if (!top) { if (c.duty) endDuty(c); continue; }
      if (!c.duty || c.duty.key !== top.key) startDuty(c, top);
      else {
        const before = c.duty.detail;
        c.duty = top;
        if (top.exact && top.detail && top.detail !== before) noteProgress(c, top);
      }
    }
  }
  // Exact mode: an officer at their station calls out each new step
  // ("Reading app.js."), throttled so the deck never turns into a wall of text.
  function noteProgress(c, d) {
    const t = nowMs();
    if (S.mode !== 'live' || c.bubble || c.path.length || t - (c.lastProgressSay || 0) < 6500) return;
    const step = String(d.detail || '').split(' · ')[0];
    if (!step || step === 'Working' || step === 'Listening') return;
    c.lastProgressSay = t;
    say(c, CD.short(step, 40) + '.');
  }
  function startDuty(c, duty) {
    c.duty = duty;
    const room = rooms[duty.room];
    const pref = room.id === c.def.home ? 0 : 1 + (c.idx % Math.max(1, room.stations.length - 1));
    goTo(c, duty.room, { station: pref, mode: 'work', activity: duty.label });
    remember(c, `on duty: ${duty.label}`);
  }
  function endDuty(c) {
    c.duty = null; c.queue = [];
    if (c.mode === 'work') c.mode = 'idle';
    if (c.path.length) c.arriveMode = 'idle';
    c.activity = 'Standing by';
    c.idleUntil = nowMs() + rand(1500, 4000);
  }

  const VISIT = ['sensors', 'memory', 'reactor', 'ops', 'cargo', 'storage', 'life', 'core', 'engineering', 'comms', 'shields', 'tactical', 'sanctuary', 'bridge', 'medbay'];
  function idleThink(c, t) {
    if (c.duty || c.path.length || t < c.idleUntil) return;
    if (deckSettings().reduced_motion) return;
    if (S.quiet) {
      if (c.mode !== 'sleep') goTo(c, c.idx % 2 === 0 ? 'quarters' : 'lounge', { station: c.idx % 4, mode: 'sleep', activity: 'Off duty, resting' });
      c.idleUntil = t + 30000;
      return;
    }
    const r = Math.random();
    let dest, station = null, activity, near = null;
    if (c.role === 'advisor') {
      if (r < 0.8) { dest = 'lounge'; station = 0; activity = 'Keeping the lounge'; }
      else { dest = CD.pick(VISIT); activity = 'Walking the ship'; }
    } else if (r < 0.4) {
      dest = c.def.home; activity = 'On station, standing by';
    } else if (r < 0.64) {
      dest = 'lounge'; activity = 'Off duty in the lounge';
    } else if (r < 0.84) {
      dest = CD.pick(VISIT.filter((v) => v !== c.room)); activity = `Checking ${rooms[dest].name.toLowerCase()}`;
    } else {
      const pals = crew.filter((o) => o !== c && !o.duty && !o.path.length && o.mode !== 'sleep');
      if (pals.length) {
        const p = CD.pick(pals);
        dest = p.room; near = { x: p.x, y: p.y }; c.chatWith = p; activity = `Catching up with ${p.name}`;
      } else {
        dest = 'lounge'; activity = 'Off duty in the lounge';
      }
    }
    goTo(c, dest, { station, near, mode: 'idle', activity });
    c.idleUntil = t + rand(9000, 22000);
  }

  // ------------------------------------------------------ crew dialogue
  const ordersSummary = () => { const os = bridge.snap.orders || []; return { active: os.filter((o) => o.enabled).length, total: os.length }; };
  const runningJobs = () => (bridge.snap.jobs || []).filter((j) => j.status === 'running');

  function dialogueContext(c) {
    const run = missionState?.runs?.find(r => ['running', 'waiting'].includes(r.status));
    return { room: rooms[c.room]?.name || c.room, mission: run?.title || '',
      activity: c.activity, condition: S.cond, jobs: runningJobs().length, orders: ordersSummary().active };
  }
  function ambientExchange(c, mate) {
    if (!dialogue || !missionState || deckSettings().banter_enabled === false || S.mode !== 'live') return;
    const lines = dialogue.exchange(c.agentId, mate.agentId, dialogueContext(c));
    const epoch = dialogueEpoch, room = c.room;
    lines.forEach((line, i) => {
      const speak = () => {
        const who = crew.find(o => o.agentId === line.officer);
        if (epoch !== dialogueEpoch || S.mode !== 'live' || !who || who.duty || who.path.length ||
            who.mode === 'sleep' || who.room !== room || deckSettings().banter_enabled === false) return;
        say(who, line.text, (deckSettings().bubble_seconds || 7) * 1000, 'ambient');
      };
      if (!i) speak();
      else { const timer = setTimeout(() => { conversationTimers.delete(timer); speak(); }, i * ((deckSettings().bubble_seconds || 7) * 1000 + 250)); conversationTimers.add(timer); }
    });
  }
  function progressLine(c) {
    const d = c.duty;
    const el = CD.fmtDur(Date.now() - (d.since || Date.now()));
    const step = String(d.detail || '').split(' · ')[0];
    switch (d.kind) {
      case 'mission': return `${CD.short(d.label, 42)}: ${CD.short(d.detail, 60)}`;
      case 'job': return `${CD.short(d.label, 30)}: ${el} in.`;
      case 'agent': return step && step !== 'Working' ? `${CD.short(step, 40)}.` : `${CD.short(d.label, 30)}. ${el} in.`;
      case 'voice': return CD.pick(['Still on the line.', `Listening to ${capt()}.`]);
      case 'chat':
        if (d.exact) return step && step !== 'Listening' ? `${CD.short(step, 40)}.` : CD.pick([`Talking with ${capt()}.`, 'Working the channel.']);
        return CD.pick(['Channels are busy.', `Monitoring the line with ${capt()}.`, 'Keeping the helm steady.']);
      case 'alert': return 'Still tracing it.';
      case 'standby': return 'Standing by.';
      default: return `${CD.short(d.label, 30)}. ${el} in.`;
    }
  }

  let nextChatter = nowMs() + 4000;
  function chatterTick(t) {
    if (t < nextChatter) return;
    const interval = (deckSettings().banter_interval_seconds || 18) * 1000;
    nextChatter = t + rand(interval * 0.8, interval * 1.2);
    if (S.mode !== 'live' || deckSettings().banter_enabled === false) return;
    if (bubbles.size >= 3) return;
    const working = crew.filter((c) => c.duty && c.mode === 'work' && !c.bubble);
    const idle = crew.filter((c) => !c.duty && !c.path.length && !c.bubble && c.mode !== 'sleep');
    if (working.length && (Math.random() < 0.35 || !idle.length)) {
      const c = CD.pick(working), text = progressLine(c);
      if (c.lastSpokenWork !== text) { c.lastSpokenWork = text; say(c, text); }
      return;
    }
    if (!idle.length || !dialogue || !missionState) return;
    const c = CD.pick(idle);
    const mate = idle.find((o) => o !== c && o.room === c.room);
    if (mate) ambientExchange(c, mate);
    else say(c, dialogue.solo(c.agentId, dialogueContext(c)), (deckSettings().bubble_seconds || 7) * 1000, 'ambient');
  }

  // ---------------------------------------------------- speech bubbles
  const overlay = $('#overlay');
  const bubbles = new Set();
  function say(c, text, ms = (deckSettings().bubble_seconds || 7) * 1000, kind = 'status') {
    if (!c || !text) return;
    if (c.bubble) killBubble(c.bubble);
    if (bubbles.size >= 4) killBubble([...bubbles].sort((a, b) => a.t0 - b.t0)[0]);
    const el = document.createElement('div');
    el.className = 'bubble' + (c.duty && c.duty.kind === 'alert' ? ' alert' : '');
    el.style.borderLeft = `5px solid ${c.color}`;
    el.title = kind === 'ambient' ? 'Ambient dialogue: a personality-based scene, not an agent task' : 'Reported work or mission conversation';
    el.innerHTML = `<span class="who">${esc(c.name)}</span>${esc(text)}`;
    overlay.appendChild(el);
    const b = { el, c, kind, t0: nowMs(), until: nowMs() + ms };
    c.bubble = b;
    bubbles.add(b);
    placeBubble(b);
  }
  function killBubble(b) { if (!b) return; b.el.remove(); bubbles.delete(b); if (b.c.bubble === b) b.c.bubble = null; }
  function placeBubble(b) {
    b.el.style.left = `${V.ox + b.c.x * V.s}px`;
    b.el.style.top = `${V.oy + (b.c.y - DH / 2 - 6) * V.s}px`;
  }
  function updateBubbles(t) {
    const live = [];
    for (const b of [...bubbles]) {
      if (t > b.until) { killBubble(b); continue; }
      live.push(b);
    }
    // Anchor each bubble over its speaker, then nudge later ones upward so no
    // two bubbles overlap (bubbles are centred horizontally on the speaker).
    live.sort((a, b) => b.c.y - a.c.y);
    const placed = [];
    for (const b of live) {
      const w = b.el.offsetWidth, h = b.el.offsetHeight;
      const cx = V.ox + b.c.x * V.s;
      let bottom = V.oy + (b.c.y - DH / 2 - 6) * V.s;
      for (let guard = 0; guard < 8; guard++) {
        const hit = placed.find((p) => Math.abs(p.cx - cx) < (p.w + w) / 2 + 4 && bottom > p.bottom - p.h - 4 && bottom - h < p.bottom + 4);
        if (!hit) break;
        bottom = hit.bottom - hit.h - 6;
      }
      bottom = Math.max(bottom, h + 6);
      placed.push({ cx, bottom, w, h });
      b.el.style.left = `${cx}px`;
      b.el.style.top = `${bottom}px`;
      b.el.style.opacity = t > b.until - 350 ? String((b.until - t) / 350) : '1';
    }
  }

  // ---------------------------------------------------------------- log
  const LOG = [];
  const logList = $('#log-list');
  function remember(c, text) { c.history.unshift({ t: Date.now(), text }); if (c.history.length > 12) c.history.pop(); }
  function log(text, opts = {}) {
    const e = { t: Date.now(), text, role: opts.role || null, room: opts.room || null, level: opts.level || 'info' };
    LOG.unshift(e);
    if (LOG.length > 150) LOG.pop();
    if (e.role && crewByRole[e.role]) remember(crewByRole[e.role], text);
    renderLog();
  }
  function logText(e) { const c = e.role ? crewByRole[e.role] : null; return (c ? c.name + ' ' : '') + e.text; }
  function renderLog() {
    logList.innerHTML = LOG.slice(0, 90).map((e) => {
      const c = e.role ? crewByRole[e.role] : null;
      return `<li class="${e.level}"><span class="t">${CD.fmtClockS(e.t)}</span><span>${c ? `<span class="who" style="color:${c.text}">${esc(c.name)}</span>` : ''}<span class="x">${esc(e.text)}</span></span></li>`;
    }).join('');
  }
  function emitEvent(ev) {
    log(ev.log, { role: ev.role, room: ev.room, level: ev.level });
    if (ev.say && ev.role && S.mode === 'live') say(crewByRole[ev.role], ev.say);
  }

  // ---------------------------------------------------------- rendering
  const stage = $('#stage');
  const canvas = $('#deck');
  const ctx = canvas.getContext('2d');
  const V = { w: 0, h: 0, dpr: 1, s: 1, ox: 0, oy: 0 };
  let bg = null;
  let theme = {};
  function readTheme() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue('--' + name).trim();
    theme = { bg: token('bg'), panel: token('panel'), text: token('text'), font: token('font-main') };
    if (bg) buildBackground();
  }
  readTheme();
  window.addEventListener('dashboard-theme-change', readTheme);
  const STARS = Array.from({ length: 240 }, () => ({ x: Math.random(), y: Math.random(), z: Math.random() ** 2, tw: rand(0, 6) }));

  function resize() {
    const r = stage.getBoundingClientRect();
    V.w = Math.max(200, r.width); V.h = Math.max(200, r.height);
    V.dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(V.w * V.dpr); canvas.height = Math.round(V.h * V.dpr);
    const v = layout.view, pad = 14;
    V.s = Math.min((V.w - pad * 2) / v.w, (V.h - pad * 2) / v.h);
    V.ox = (V.w - v.w * V.s) / 2 - v.x * V.s;
    V.oy = (V.h - v.h * V.s) / 2 - v.y * V.s;
    buildBackground();
  }
  function buildBackground() {
    bg = document.createElement('canvas');
    bg.width = canvas.width; bg.height = canvas.height;
    const g = bg.getContext('2d'), W = bg.width, H = bg.height;
    const lin = g.createLinearGradient(0, 0, W, H);
    lin.addColorStop(0, theme.bg); lin.addColorStop(1, theme.panel);
    g.fillStyle = lin; g.fillRect(0, 0, W, H);
    const blob = (x, y, r, col) => { const rg = g.createRadialGradient(x, y, 0, x, y, r); rg.addColorStop(0, col); rg.addColorStop(1, 'rgba(0,0,0,0)'); g.fillStyle = rg; g.fillRect(0, 0, W, H); };
    blob(W * 0.16, H * 0.84, Math.max(W, H) * 0.45, 'rgba(90,40,140,0.22)');
    blob(W * 0.84, H * 0.12, Math.max(W, H) * 0.40, 'rgba(20,110,140,0.16)');
    const px = W * 0.93, py = H * 1.05, pr = Math.min(W, H) * 0.44;
    const pg = g.createRadialGradient(px - pr * 0.35, py - pr * 0.5, pr * 0.08, px, py, pr);
    pg.addColorStop(0, '#6d4d82'); pg.addColorStop(0.55, '#2c1f3e'); pg.addColorStop(1, '#0a0812');
    g.fillStyle = pg; g.beginPath(); g.arc(px, py, pr, 0, Math.PI * 2); g.fill();
    g.strokeStyle = 'rgba(180,150,255,0.28)'; g.lineWidth = 3 * V.dpr;
    g.beginPath(); g.arc(px, py, pr, Math.PI * 1.04, Math.PI * 1.62); g.stroke();
  }
  new ResizeObserver(resize).observe(stage);
  resize();

  function drawStars(dt, t) {
    const W = canvas.width, H = canvas.height;
    for (const s of STARS) {
      s.x -= (0.002 + s.z * 0.012) * dt;
      if (s.x < 0) { s.x += 1; s.y = Math.random(); }
      const a = 0.22 + 0.7 * s.z * (0.8 + 0.2 * Math.sin(t / 650 + s.tw));
      ctx.fillStyle = `rgba(215,225,255,${a.toFixed(3)})`;
      const sz = (s.z > 0.75 ? 2 : 1) * V.dpr;
      ctx.fillRect(s.x * W, s.y * H, sz, sz);
    }
  }

  function roundRect(g, x, y, w, h, r) {
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }

  // Text is drawn in screen space so it stays crisp at any zoom.
  let labelQueue = [];
  function queueLabel(text, x, y, color, align = 'center') { labelQueue.push({ text, x, y, color, align }); }
  function flushLabels() {
    ctx.setTransform(V.dpr, 0, 0, V.dpr, 0, 0);
    ctx.font = '600 12px ' + theme.font;
    ctx.textBaseline = 'middle';
    for (const l of labelQueue) {
      const sx = V.ox + l.x * V.s, sy = V.oy + l.y * V.s;
      const w = ctx.measureText(l.text).width + 10;
      const x0 = l.align === 'left' ? sx : sx - w / 2;
      ctx.fillStyle = theme.panel;
      roundRect(ctx, x0, sy - 8, w, 16, 3); ctx.fill();
      ctx.fillStyle = l.color || theme.text;
      ctx.textAlign = 'left';
      ctx.fillText(l.text, x0 + 5, sy + 0.5);
    }
    labelQueue = [];
  }

  // Room system icons (12px box, drawn into any context).
  const PI = Math.PI;
  const ln = (g, ...p) => { g.beginPath(); g.moveTo(p[0], p[1]); for (let i = 2; i < p.length; i += 2) g.lineTo(p[i], p[i + 1]); g.stroke(); };
  const ci = (g, x, y, r, fill) => { g.beginPath(); g.arc(x, y, r, 0, PI * 2); if (fill) g.fill(); else g.stroke(); };
  const ICONS = {
    helm(g, x, y) { ci(g, x, y, 4.5); ci(g, x, y, 1.2, true); for (let k = 0; k < 4; k++) { const a = k * PI / 2 + PI / 4; ln(g, x + Math.cos(a) * 1.5, y + Math.sin(a) * 1.5, x + Math.cos(a) * 5.8, y + Math.sin(a) * 5.8); } },
    eye(g, x, y) { g.beginPath(); g.moveTo(x - 5.5, y); g.quadraticCurveTo(x, y - 5, x + 5.5, y); g.quadraticCurveTo(x, y + 5, x - 5.5, y); g.stroke(); ci(g, x, y, 1.6, true); },
    target(g, x, y) { ci(g, x, y, 3.8); ln(g, x - 6, y, x - 2, y); ln(g, x + 2, y, x + 6, y); ln(g, x, y - 6, x, y - 2); ln(g, x, y + 2, x, y + 6); },
    shield(g, x, y) { g.beginPath(); g.moveTo(x, y - 5.5); g.lineTo(x + 4.5, y - 3.5); g.lineTo(x + 4, y + 1.5); g.quadraticCurveTo(x + 2, y + 4.5, x, y + 5.5); g.quadraticCurveTo(x - 2, y + 4.5, x - 4, y + 1.5); g.lineTo(x - 4.5, y - 3.5); g.closePath(); g.stroke(); },
    antenna(g, x, y) { ln(g, x, y - 1, x, y + 5.5); ci(g, x, y - 2, 1.2, true); g.beginPath(); g.arc(x, y - 2, 3.4, -PI * 0.8, -PI * 0.2); g.stroke(); g.beginPath(); g.arc(x, y - 2, 5.8, -PI * 0.8, -PI * 0.2); g.stroke(); },
    wrench(g, x, y) { ln(g, x - 4.5, y + 4.5, x + 1, y - 1); ci(g, x + 2.6, y - 2.6, 2.4); },
    banks(g, x, y) { for (let k = -1; k <= 1; k++) { g.beginPath(); g.ellipse(x, y + k * 3.4, 4.6, 1.5, 0, 0, PI * 2); g.stroke(); } },
    chip(g, x, y) { g.strokeRect(x - 3.5, y - 3.5, 7, 7); for (let k = -1; k <= 1; k++) { ln(g, x + k * 2.3, y - 3.5, x + k * 2.3, y - 5.5); ln(g, x + k * 2.3, y + 3.5, x + k * 2.3, y + 5.5); ln(g, x - 3.5, y + k * 2.3, x - 5.5, y + k * 2.3); ln(g, x + 3.5, y + k * 2.3, x + 5.5, y + k * 2.3); } },
    lotus(g, x, y) { g.beginPath(); g.moveTo(x, y + 4); g.quadraticCurveTo(x - 3, y - 1, x, y - 5); g.quadraticCurveTo(x + 3, y - 1, x, y + 4); g.stroke(); g.beginPath(); g.moveTo(x, y + 4); g.quadraticCurveTo(x - 6, y + 2.5, x - 5.5, y - 2); g.stroke(); g.beginPath(); g.moveTo(x, y + 4); g.quadraticCurveTo(x + 6, y + 2.5, x + 5.5, y - 2); g.stroke(); },
    roster(g, x, y) { g.strokeRect(x - 4, y - 4.5, 8, 10); ln(g, x - 2, y - 1.5, x + 2, y - 1.5); ln(g, x - 2, y + 1, x + 2, y + 1); ln(g, x - 2, y + 3.5, x + 1, y + 3.5); },
    bolt(g, x, y) { g.beginPath(); g.moveTo(x + 1.5, y - 6); g.lineTo(x - 3, y + 1); g.lineTo(x, y + 1); g.lineTo(x - 1.5, y + 6); g.lineTo(x + 3, y - 1); g.lineTo(x, y - 1); g.closePath(); g.fill(); },
    cup(g, x, y) { g.beginPath(); g.moveTo(x - 4, y - 2); g.lineTo(x - 3, y + 4); g.lineTo(x + 2, y + 4); g.lineTo(x + 3, y - 2); g.closePath(); g.stroke(); g.beginPath(); g.arc(x + 3.6, y + 0.8, 1.8, -PI / 2, PI / 2); g.stroke(); ln(g, x - 1, y - 5.5, x - 1, y - 3.6); ln(g, x + 1, y - 5.5, x + 1, y - 3.6); },
    chevrons(g, x, y) { ln(g, x - 5, y - 4, x - 1, y, x - 5, y + 4); ln(g, x, y - 4, x + 4, y, x, y + 4); },
    cross(g, x, y) { g.fillRect(x - 1.6, y - 5, 3.2, 10); g.fillRect(x - 5, y - 1.6, 10, 3.2); },
    fan(g, x, y) { ci(g, x, y, 1.3, true); for (let k = 0; k < 3; k++) { const a = k * PI * 2 / 3; g.beginPath(); g.ellipse(x + Math.cos(a) * 3, y + Math.sin(a) * 3, 2.8, 1.4, a, 0, PI * 2); g.stroke(); } },
    crate(g, x, y) { g.strokeRect(x - 4.5, y - 4.5, 9, 9); ln(g, x - 4.5, y - 4.5, x + 4.5, y + 4.5); ln(g, x + 4.5, y - 4.5, x - 4.5, y + 4.5); },
    bed(g, x, y) { g.strokeRect(x - 5, y - 1, 10, 4.5); g.fillRect(x - 4.5, y - 3.5, 3.5, 2.5); ln(g, x - 5, y + 3.5, x - 5, y + 5.5); ln(g, x + 5, y + 3.5, x + 5, y + 5.5); },
    boxes(g, x, y) { g.strokeRect(x - 5, y, 4.5, 4.5); g.strokeRect(x + 0.5, y, 4.5, 4.5); g.strokeRect(x - 2.25, y - 4.5, 4.5, 4.5); },
  };

  function drawEngineGlow(t) {
    const h = bridge.snap.health;
    const load = h ? clamp((h.engine_load || 0) / 100, 0, 1) : 0.4;
    const flick = 0.86 + 0.14 * Math.sin(t / 90) * Math.sin(t / 37);
    const glow = (x, y, r, a) => {
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, `rgba(130,225,255,${a})`); g.addColorStop(0.4, `rgba(60,150,255,${a * 0.5})`); g.addColorStop(1, 'rgba(40,90,255,0)');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r, 0, PI * 2); ctx.fill();
    };
    ctx.save();
    ctx.globalCompositeOperation = 'lighter';
    const a = (0.2 + 0.3 * load) * flick;
    glow(200, 456, 72 + 22 * load, a);
    glow(318, 242, 32 + 10 * load, a * 0.8);
    glow(318, 684, 32 + 10 * load, a * 0.8);
    ctx.restore();
  }

  function roomState() {
    const st = {};
    for (const r of ROOM_LIST) st[r.id] = { active: false, alert: !!(S.roomAlerts[r.id] && S.roomAlerts[r.id].length), crew: [] };
    for (const c of crew) {
      if (st[c.room]) st[c.room].crew.push(c);
      if (c.duty && c.mode === 'work' && st[c.duty.room]) {
        st[c.duty.room].active = true;
        if (c.duty.kind === 'alert') st[c.duty.room].alert = true;
      }
    }
    if (S.inflight > 0 || S.coreLabel) st.core.active = true;
    if (S.coreTarget && st[S.coreTarget]) st[S.coreTarget].active = true;
    return st;
  }

  function drawRooms(t) {
    const st = S.roomSt;
    for (const r of ROOM_LIST) {
      const s = st[r.id];
      if (S.quiet && !s.active && !s.alert) { ctx.fillStyle = 'rgba(6,10,26,0.34)'; ctx.fillRect(r.x0, r.y0, r.w, r.h); }
      if (s.alert) {
        const a = 0.14 + 0.13 * (0.5 + 0.5 * Math.sin(t / 210));
        ctx.fillStyle = `rgba(255,40,40,${a.toFixed(3)})`; ctx.fillRect(r.x0, r.y0, r.w, r.h);
        ctx.strokeStyle = 'rgba(255,70,70,0.9)'; ctx.lineWidth = 2; ctx.strokeRect(r.x0 + 1, r.y0 + 1, r.w - 2, r.h - 2);
      } else if (s.active) {
        const a = 0.07 + 0.05 * (0.5 + 0.5 * Math.sin(t / 520 + r.x0));
        ctx.fillStyle = hexA(r.color, a.toFixed(3)); ctx.fillRect(r.x0, r.y0, r.w, r.h);
        ctx.strokeStyle = hexA(r.color, 0.6); ctx.lineWidth = 1.5; ctx.strokeRect(r.x0 + 1, r.y0 + 1, r.w - 2, r.h - 2);
      }
      const selected = S.sel && S.sel.type === 'room' && S.sel.id === r.id;
      const hovered = S.hover && S.hover.type === 'room' && S.hover.id === r.id;
      if (selected) {
        ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,0.95)'; ctx.lineWidth = 2; ctx.setLineDash([5, 3]); ctx.lineDashOffset = -t / 60;
        ctx.strokeRect(r.x0 + 1, r.y0 + 1, r.w - 2, r.h - 2); ctx.restore();
      } else if (hovered) {
        ctx.strokeStyle = 'rgba(255,255,255,0.45)'; ctx.lineWidth = 1.5; ctx.strokeRect(r.x0 + 1, r.y0 + 1, r.w - 2, r.h - 2);
      }
    }
    if (S.cond === 'red' || S.cond === 'yellow') {
      const a = (S.cond === 'red' ? 0.11 : 0.06) * (0.5 + 0.5 * Math.sin(t / 300));
      ctx.fillStyle = S.cond === 'red' ? `rgba(255,30,30,${a.toFixed(3)})` : `rgba(255,200,0,${a.toFixed(3)})`;
      for (const r of ROOM_LIST) ctx.fillRect(r.x0, r.y0, r.w, r.h);
    }
  }

  function drawDoors() {
    for (const d of DOOR_LIST) {
      if (d.o <= 0.002) {
        if (d.added) ctx.drawImage(shipImg, d.src.x, d.src.y, d.src.w, d.src.h, d.dst.x, d.dst.y, d.dst.w, d.dst.h);
        continue;
      }
      const o = d.o * d.o * (3 - 2 * d.o);
      const { src, dst, clip } = d;
      ctx.save();
      ctx.beginPath(); ctx.rect(clip.x, clip.y, clip.w, clip.h); ctx.clip();
      ctx.fillStyle = d.floor; ctx.fillRect(d.region.x, d.region.y, d.region.w, d.region.h);
      if (d.orient === 'v') {
        const hs = src.h / 2, hd = dst.h / 2, sh = hd * o;
        ctx.drawImage(shipImg, src.x, src.y, src.w, hs, dst.x, dst.y - sh, dst.w, hd);
        ctx.drawImage(shipImg, src.x, src.y + hs, src.w, hs, dst.x, dst.y + hd + sh, dst.w, hd);
      } else {
        const ws = src.w / 2, wd = dst.w / 2, sw = wd * o;
        ctx.drawImage(shipImg, src.x, src.y, ws, src.h, dst.x - sw, dst.y, wd, dst.h);
        ctx.drawImage(shipImg, src.x + ws, src.y, ws, src.h, dst.x + wd + sw, dst.y, wd, dst.h);
      }
      ctx.restore();
    }
  }

  let coreAct = 0;
  function drawCore(t, dt) {
    coreAct += (clamp((S.inflight + (S.coreLabel ? 1 : 0)) / 3, 0, 1) - coreAct) * Math.min(1, dt * 2);
    const [cx, cy] = CORE, act = coreAct;
    ctx.fillStyle = 'rgba(8,16,30,0.55)'; ci(ctx, cx, cy, 27, true);
    ctx.strokeStyle = 'rgba(80,120,160,0.6)'; ctx.lineWidth = 1; ci(ctx, cx, cy, 27);
    const pulse = 0.5 + 0.5 * Math.sin(t / (520 - act * 300));
    const gr = 22 + 10 * act + 2 * pulse;
    const g = ctx.createRadialGradient(cx, cy, 1, cx, cy, gr);
    g.addColorStop(0, `rgba(205,250,255,${(0.7 + 0.25 * act).toFixed(3)})`);
    g.addColorStop(0.3, `rgba(90,200,255,${(0.32 + 0.38 * act).toFixed(3)})`);
    g.addColorStop(1, 'rgba(40,120,255,0)');
    ctx.fillStyle = g; ci(ctx, cx, cy, gr, true);
    ctx.save();
    ctx.lineWidth = 1.4;
    for (let k = 0; k < 3; k++) {
      ctx.strokeStyle = `rgba(150,235,255,${(0.45 + 0.35 * act).toFixed(3)})`;
      ctx.setLineDash([5 + k * 3, 4 + k]);
      ctx.lineDashOffset = (t / (70 - act * 45)) * (k % 2 ? 1 : -1);
      ci(ctx, cx, cy, 9 + k * 6);
    }
    ctx.restore();
    const n = Math.min(8, S.inflight);
    ctx.fillStyle = '#eafcff';
    for (let i = 0; i < n; i++) {
      const a = t / (900 - act * 400) + (i * PI * 2) / n;
      ci(ctx, cx + Math.cos(a) * 24, cy + Math.sin(a) * 24, 2.2, true);
    }
    const show = S.labels || S.coreLabel || (S.hover && S.hover.type === 'core') || (S.sel && S.sel.type === 'core');
    if (show) queueLabel(S.coreLabel ? `${assistantName()} · ${S.coreLabel}` : assistantName(), cx, cy + 40, '#66E0FF');
  }

  const packets = [];
  let nextPacket = 0;
  function spawnPackets(t) {
    if (t < nextPacket) return;
    nextPacket = t + 900;
    if (!(S.inflight > 0 || S.coreLabel)) return;
    for (const r of ROOM_LIST) {
      if (r.id === 'core' || !S.roomSt[r.id].active || Math.random() < 0.45) continue;
      const [tx, ty] = r.stations[0];
      packets.push({ x0: CORE[0], y0: CORE[1], x1: tx, y1: ty, t0: t, dur: 900 + Math.hypot(tx - CORE[0], ty - CORE[1]) * 2.4, col: r.color });
    }
  }
  function drawPackets(t) {
    for (let i = packets.length - 1; i >= 0; i--) {
      const p = packets[i], u = (t - p.t0) / p.dur;
      if (u >= 1) { packets.splice(i, 1); continue; }
      for (let k = 0; k < 3; k++) {
        const v = u - k * 0.025;
        if (v < 0) continue;
        ctx.fillStyle = hexA(p.col, (0.85 - k * 0.27).toFixed(2));
        ci(ctx, p.x0 + (p.x1 - p.x0) * v, p.y0 + (p.y1 - p.y0) * v, 2.1 - k * 0.5, true);
      }
    }
  }

  function drawRoomBadges() {
    const st = S.roomSt;
    for (const r of ROOM_LIST) {
      const x = r.x0 + 4, y = r.y0 + 4, s = st[r.id];
      const on = s.active || s.alert;
      ctx.fillStyle = 'rgba(12,14,22,0.6)';
      roundRect(ctx, x, y, 15, 15, 3); ctx.fill();
      const col = s.alert ? '#ff6b6b' : on ? r.color : 'rgba(228,222,206,0.78)';
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1.25;
      if (ICONS[r.icon]) ICONS[r.icon](ctx, x + 7.5, y + 7.5);
      if (S.labels || (S.hover && S.hover.type === 'room' && S.hover.id === r.id)) {
        queueLabel(r.name, x + 18, y + 7.5, on ? r.color : '#e9e2cf', 'left');
      }
    }
  }

  function drawCrew(t) {
    const list = crew.slice().sort((a, b) => a.y - b.y);
    for (const c of list) {
      const sel = S.sel && S.sel.type === 'crew' && S.sel.role === c.role;
      const hov = S.hover && S.hover.type === 'crew' && S.hover.role === c.role;
      const fy = c.y + DH / 2 - 3;
      ctx.fillStyle = 'rgba(0,0,0,0.30)';
      ctx.beginPath(); ctx.ellipse(c.x, fy, 11, 3.6, 0, 0, PI * 2); ctx.fill();
      if (sel) {
        ctx.strokeStyle = '#6ef08a'; ctx.lineWidth = 1.7;
        ctx.beginPath(); ctx.ellipse(c.x, fy, 15, 5.2, 0, 0, PI * 2); ctx.stroke();
      }
      let frame = 0, bob = 0;
      if (c.moving) frame = 1 + (Math.floor((t + c.phase * 100) / 140) % 2);
      else if (c.mode === 'work') bob = Math.sin(t / 160 + c.phase) > 0.2 ? -0.8 : 0;
      else if (c.mode !== 'sleep') bob = Math.sin(t / 1100 + c.phase) > 0.75 ? -0.7 : 0;
      ctx.save();
      ctx.translate(Math.round(c.x * 2) / 2, Math.round((c.y + bob) * 2) / 2);
      if (c.face < 0) ctx.scale(-1, 1);
      if (c.mode === 'sleep') ctx.globalAlpha = 0.85;
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(c.img, frame * FW, 0, FW, FH, -DW / 2, -DH / 2, DW, DH);
      ctx.restore();
      ctx.imageSmoothingEnabled = true;
      const working = c.duty && c.mode === 'work';
      if (working) {
        const bw = 22, bx = c.x - bw / 2, by = c.y - DH / 2 - 5;
        const alert = c.duty.kind === 'alert';
        ctx.fillStyle = 'rgba(0,0,0,0.65)'; ctx.fillRect(bx - 1, by - 1, bw + 2, 4);
        ctx.fillStyle = alert ? '#ff5555' : c.duty.kind === 'standby' ? '#99CCFF' : '#6ef08a';
        const u = alert ? 1 : (t / 2400 + c.phase) % 1;
        if (!alert || Math.sin(t / 110) > -0.3) ctx.fillRect(bx, by, bw * u, 2);
        if (!alert && c.duty.kind !== 'standby' && Math.sin(t / 90 + c.phase * 3) > 0.55) {
          ctx.fillStyle = 'rgba(160,240,255,0.95)';
          ctx.fillRect(c.x + (c.face > 0 ? 7 : -9), c.y + 3 + Math.sin(t / 60) * 1.5, 2, 2);
        }
      }
      if (c.mode === 'sleep' && !c.path.length) {
        for (let k = 0; k < 2; k++) {
          const u = (t / 1700 + c.phase + k * 0.5) % 1;
          queueLabelZ(c.x + 7 + u * 6, c.y - 16 - u * 14, 1 - u);
        }
      }
      if (hov || sel || S.labels) queueLabel(c.name, c.x, c.y - DH / 2 - (working ? 13 : 7), c.text);
    }
  }
  let zQueue = [];
  function queueLabelZ(x, y, a) { zQueue.push({ x, y, a }); }
  function flushZ() {
    ctx.setTransform(V.dpr, 0, 0, V.dpr, 0, 0);
    ctx.font = '700 11px "Share Tech Mono", monospace';
    ctx.textAlign = 'center';
    for (const z of zQueue) { ctx.fillStyle = `rgba(205,215,255,${(z.a * 0.9).toFixed(2)})`; ctx.fillText('z', V.ox + z.x * V.s, V.oy + z.y * V.s); }
    zQueue = [];
  }

  function draw(t, dt) {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (bg) ctx.drawImage(bg, 0, 0);
    drawStars(dt, t);
    const k = V.dpr * V.s;
    ctx.setTransform(k, 0, 0, k, V.dpr * V.ox, V.dpr * V.oy);
    drawEngineGlow(t);
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(shipImg, 0, 0);
    drawRooms(t);
    drawDoors();
    drawPackets(t);
    drawCore(t, dt);
    drawRoomBadges();
    drawCrew(t);
    flushZ();
    flushLabels();
  }

  // -------------------------------------------------------------- input
  const tooltip = $('#tooltip');
  function toImage(ev) {
    const r = canvas.getBoundingClientRect();
    const sx = ev.clientX - r.left, sy = ev.clientY - r.top;
    return { x: (sx - V.ox) / V.s, y: (sy - V.oy) / V.s, sx, sy };
  }
  function hitTest(p) {
    let best = null, bd = 1e9;
    for (const c of crew) {
      const dx = p.x - c.x, dy = p.y - c.y;
      if (Math.abs(dx) < 15 && dy > -DH / 2 && dy < DH / 2) {
        const d = Math.abs(dx) + Math.abs(dy) * 0.5;
        if (d < bd) { bd = d; best = c; }
      }
    }
    if (best) return { type: 'crew', role: best.role };
    if (Math.hypot(p.x - CORE[0], p.y - CORE[1]) < 26) return { type: 'core' };
    for (const r of ROOM_LIST) if (p.x >= r.x0 && p.x <= r.x1 && p.y >= r.y0 && p.y <= r.y1) return { type: 'room', id: r.id };
    return null;
  }
  function tooltipHTML(h) {
    if (!h) return '';
    if (h.type === 'crew') { const c = crewByRole[h.role]; return `<b>${esc(c.name)}</b> · ${esc(statusOf(c).text)}`; }
    if (h.type === 'core') return `<b>${esc(assistantName())}</b> · ${S.inflight} chat turn${S.inflight === 1 ? '' : 's'} in flight${S.exact && S.coreLabel ? ` · ${esc(S.coreLabel.toLowerCase())}` : ''}`;
    const r = rooms[h.id], m = metricFor(r.id);
    const n = S.roomSt[r.id] ? S.roomSt[r.id].crew.length : 0;
    return `<b>${esc(r.name)}</b>${m ? ` · ${esc(m.label)}: ${esc(m.value)}` : ''}${n ? ` · ${n} aboard` : ''}`;
  }
  canvas.addEventListener('mousemove', (ev) => {
    const p = toImage(ev);
    S.hover = hitTest(p);
    canvas.style.cursor = S.hover ? 'pointer' : 'default';
    const html = tooltipHTML(S.hover);
    if (!html) { tooltip.style.display = 'none'; return; }
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
    const tw = tooltip.offsetWidth;
    tooltip.style.left = `${Math.min(p.sx + 14, V.w - tw - 8)}px`;
    tooltip.style.top = `${p.sy + 16}px`;
  });
  canvas.addEventListener('mouseleave', () => { S.hover = null; tooltip.style.display = 'none'; });
  canvas.addEventListener('click', (ev) => select(hitTest(toImage(ev))));
  window.addEventListener('keydown', (e) => {
    if (document.querySelector('dialog[open]') || (e.target && (/INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable))) return;
    if (e.key === 'l' || e.key === 'L') toggleLabels();
    else if (e.key === 'd' || e.key === 'D') setMode(S.mode === 'demo' ? 'live' : 'demo');
    else if (e.key === 'Escape') select(null);
  });
  function select(sel) { S.sel = sel; renderDetail(true); refreshRoster(); refreshSystems(); }
  function toggleLabels() { S.labels = !S.labels; $('#toggle-labels').classList.toggle('on', S.labels); }
  $('#toggle-labels').addEventListener('click', toggleLabels);
  $('#mode-live').addEventListener('click', () => setMode('live'));
  $('#mode-demo').addEventListener('click', () => setMode('demo'));
  document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x === b));
    $('#tab-log').classList.toggle('on', b.dataset.tab === 'log');
    $('#tab-duty').classList.toggle('on', b.dataset.tab === 'duty');
  }));

  // ------------------------------------------------------ readouts / UI
  function statusOf(c) {
    if (c.duty) {
      const alert = c.duty.kind === 'alert';
      const more = c.queue.length ? ` (+${c.queue.length} more)` : '';
      if (c.path.length) return { cls: alert ? 'alert' : 'work', text: `${alert ? 'Responding' : 'Heading'} to ${rooms[c.duty.room].name}: ${c.duty.label}${more}` };
      return { cls: alert ? 'alert' : 'work', text: `${c.duty.label}${more}` };
    }
    if (c.mode === 'sleep' && !c.path.length) return { cls: 'idle', text: `Off duty, resting in ${rooms[c.room].name.toLowerCase()}` };
    return { cls: 'idle', text: c.activity || 'Standing by' };
  }

  function metricFor(id) {
    const h = bridge.snap.health || null;
    const o = ordersSummary();
    const jobs = runningJobs().length;
    switch (id) {
      case 'core': return { label: 'Chat turns in flight', value: String(S.inflight), pct: S.inflight / 4, kind: 'neutral' };
      case 'bridge': return { label: 'Channels open', value: String(S.inflight), pct: null };
      case 'reactor': return h && { label: 'Engine load', value: `${Math.round(h.engine_load)}%`, pct: h.engine_load / 100, kind: 'load' };
      case 'engines': return { label: 'Background jobs', value: `${jobs} running`, short: String(jobs), pct: jobs / 3, kind: 'neutral' };
      case 'memory': return h && { label: 'RAM', value: `${h.ram_used_gb} / ${h.ram_total_gb} GB (${Math.round(h.ram)}%)`, short: `${Math.round(h.ram)}%`, pct: h.ram / 100, kind: 'load' };
      case 'cargo': return h && { label: 'Disk', value: `${Math.round(h.disk_used_pct)}% used, ${Math.round(h.disk_free_gb)} GB free`, short: `${Math.round(h.disk_used_pct)}%`, pct: h.disk_used_pct / 100, kind: 'load' };
      case 'storage': return h && h.gpu_mem_total_mb ? { label: 'Video memory', value: `${(h.gpu_mem_used_mb / 1024).toFixed(1)} / ${(h.gpu_mem_total_mb / 1024).toFixed(1)} GB`, short: `${Math.round((100 * h.gpu_mem_used_mb) / h.gpu_mem_total_mb)}%`, pct: h.gpu_mem_used_mb / h.gpu_mem_total_mb, kind: 'load' } : null;
      case 'life': return h && h.gpu_temp_c != null ? { label: 'GPU temperature', value: `${h.gpu_temp_c}°C`, pct: h.gpu_temp_c / 95, kind: 'load' } : null;
      case 'sensors': {
        if (!h) return null;
        const b = (h.net_in_bps || 0) + (h.net_out_bps || 0);
        return { label: 'Network', value: `down ${CD.fmtBps(h.net_in_bps)}, up ${CD.fmtBps(h.net_out_bps)}`, short: CD.fmtBps(b), pct: b > 0 ? Math.log10(b) / 7 : 0, kind: 'neutral' };
      }
      case 'comms': return h && { label: 'Remote link', value: h.tunnel ? 'Tunnel up' : 'Tunnel down', short: h.tunnel ? 'UP' : 'DOWN', pct: h.tunnel ? 1 : 0.0001, kind: 'good' };
      case 'shields': return h && { label: 'Shield strength', value: `${Math.round(h.shield)}%`, pct: h.shield / 100, kind: 'good' };
      case 'medbay': return h && { label: 'Hull integrity', value: `${Math.round(h.hull)}%`, pct: h.hull / 100, kind: 'good' };
      case 'ops': return { label: 'Standing orders', value: `${o.active} active of ${o.total}`, short: `${o.active}/${o.total}`, pct: o.total ? o.active / o.total : 0, kind: 'neutral' };
      default: return null;
    }
  }
  function meterClass(m) {
    if (!m || m.pct == null) return '';
    if (m.kind === 'neutral') return 'neutral';
    if (m.kind === 'good') return m.pct > 0.6 ? '' : m.pct > 0.3 ? 'warn' : 'crit';
    return m.pct < 0.7 ? '' : m.pct < 0.88 ? 'warn' : 'crit';
  }
  function meterColor(m) { const k = meterClass(m); return k === 'crit' ? '#FF4444' : k === 'warn' ? '#FFCC00' : k === 'neutral' ? '#99CCFF' : '#6ef08a'; }

  // Roster cards
  const rosterList = $('#roster-list');
  function makePortrait(img, scale = 1) {
    const cv = document.createElement('canvas');
    cv.width = FW * scale; cv.height = FH * scale;
    const g = cv.getContext('2d'); g.imageSmoothingEnabled = false;
    g.drawImage(img, 0, 0, FW, FH, 0, 0, cv.width, cv.height);
    return cv;
  }
  function buildRoster() {
    rosterList.innerHTML = '';
    for (const c of crew) {
      const card = document.createElement('div');
      card.className = 'card';
      const stripe = document.createElement('i'); stripe.className = 'stripe'; stripe.style.background = c.color;
      const info = document.createElement('div');
      info.innerHTML = '<div class="nm"></div><div class="tt"></div><div class="st"></div>';
      card.append(stripe, makePortrait(c.img), info);
      card.addEventListener('click', () => select({ type: 'crew', role: c.role }));
      rosterList.appendChild(card);
      c.card = card;
    }
  }
  function refreshRoster() {
    for (const c of crew) {
      const s = statusOf(c);
      c.card.querySelector('.nm').textContent = c.name;
      c.card.querySelector('.tt').textContent = c.def.title;
      const st = c.card.querySelector('.st');
      st.textContent = s.text; st.className = 'st ' + s.cls; st.title = s.text;
      c.card.classList.toggle('sel', !!(S.sel && S.sel.type === 'crew' && S.sel.role === c.role));
    }
    $('#roster-mode').textContent = roster().label;
  }

  // Inspector
  const detail = $('#detail');
  let lastDetail = '';
  const ownedOrders = (role) => (bridge.snap.orders || []).filter((o) => CD.classifyOrder(o).role === role);
  const paneTag = (src) => `${src.project || 'main channel'}${src.pane ? ` · pane ${String(src.pane).split('-').pop()}` : ''}`;
  const sourceText = (src) => {
    if (src.type === 'mission') return `crew mission · ${src.provider || 'model'} · ${src.project || ''}`;
    if (src.type === 'job') return `background job ${src.id}${src.project ? ' · ' + src.project : ''}`;
    if (src.type === 'agent') {
      const n = src.tools || 0;
      return `subagent ${src.agentType}${src.background ? ' (background)' : ''} · ${paneTag(src)} · ${n} tool call${n === 1 ? '' : 's'}`;
    }
    if (src.type === 'turn') return `${src.provider || 'chat'} · ${paneTag(src)}`;
    return `standing order ${src.id}`;
  };

  function crewHTML(c) {
    const s = statusOf(c), d = c.duty, owned = ownedOrders(c.role);
    const avatar = S.mode === 'live' && missionState?.roster?.find(o => o.id === c.agentId)?.avatar;
    const picture = typeof avatar === 'string' && /^data:image\/(png|jpeg);base64,[A-Za-z0-9+/=]+$/.test(avatar)
      ? `<img class="crew-profile-picture" src="${avatar}" alt="">`
      : `<canvas data-portrait="${c.role}" width="${FW * 3}" height="${FH * 3}"></canvas>`;
    let h = `<div class="head">${picture}
      <div><h2 style="color:${c.text}">${esc(c.name)}</h2><div class="sub">${esc(c.def.title)}</div>
      <div class="sub">Home: ${esc(rooms[c.def.home].name)} · Now: ${esc(rooms[c.room].name)}</div></div></div>`;
    h += '<h3>CURRENT</h3>';
    if (d) {
      const cls = d.kind === 'alert' ? 'alert' : d.kind === 'standby' ? 'standby' : '';
      h += `<div class="duty ${cls}"><div class="lbl">${esc(d.label)}</div>
        <div class="det">${esc(d.detail || '')}${d.since ? ` · ${esc(CD.fmtDur(Date.now() - d.since))}` : ''} · ${esc(rooms[d.room].name)}</div>
        ${d.source && d.source.summary ? `<div class="det">${esc(d.source.summary)}</div>` : ''}
        <div class="src">${d.source ? esc(sourceText(d.source)) + ' · ' : ''}${S.mode === 'demo' ? 'scripted' : d.exact ? 'reported officer' : 'assigned by rule'}</div></div>`;
      if (d.kind === 'alert' && d.source && d.source.type === 'job' && S.mode === 'live') h += `<button class="btn" data-ack="${esc(d.source.id)}">ACKNOWLEDGE</button>`;
    } else {
      h += `<div class="duty standby"><div class="lbl">${esc(s.text)}</div><div class="det">No live duty right now.</div></div>`;
    }
    if (c.queue.length) h += `<h3>ALSO HANDLING</h3><ul class="mini-list">${c.queue.map((q) => `<li>${esc(q.label)}</li>`).join('')}</ul>`;
    h += `<h3>STANDING ORDERS (${owned.length})</h3>`;
    h += owned.length
      ? `<ul class="mini-list">${owned.map((o) => `<li><span class="state ${o.enabled ? 'active' : 'paused'}">${o.enabled ? 'ACTIVE' : 'PAUSED'}</span>${esc(o.name)} <span class="t">${esc(CD.humanCron(o.cron))}</span></li>`).join('')}</ul>`
      : '<div class="sub">None assigned.</div>';
    h += '<h3>RECENT</h3>';
    h += c.history.length
      ? `<ul class="mini-list">${c.history.slice(0, 6).map((e) => `<li><span class="t">${CD.fmtClock(e.t)}</span>${esc(e.text)}</li>`).join('')}</ul>`
      : '<div class="sub">Nothing yet this session.</div>';
    h += `<div class="sub" style="margin-top:10px">Officer file: ~/.claude/agents/${esc(c.agentId)}.md</div>`;
    return h;
  }
  function roomHTML(r) {
    const m = metricFor(r.id), alerts = S.roomAlerts[r.id] || [];
    const here = crew.filter((c) => c.room === r.id);
    const working = crew.filter((c) => c.duty && c.duty.room === r.id);
    let h = `<h2 style="color:${r.color}">${esc(r.name)}</h2><div class="colorbar" style="background:${r.color}"></div><div class="about">${esc(r.about)}</div>`;
    if (m) {
      h += `<h3>READOUT</h3><div class="kv"><span class="k">${esc(m.label)}</span><span class="v">${esc(m.value)}</span></div>`;
      if (m.pct != null) h += `<div class="meter"><i style="width:${Math.round(clamp(m.pct, 0, 1) * 100)}%;background:${meterColor(m)}"></i></div>`;
    }
    if (alerts.length) {
      h += '<h3>ALERTS</h3>' + alerts.map((a) => `<div class="duty alert"><div class="lbl">${esc(a.text)}</div><div class="det">${esc(CD.fmtAgo(a.since))}</div></div>` +
        (S.mode === 'live' ? `<button class="btn" data-ack="${esc(a.key)}">ACKNOWLEDGE</button>` : '')).join('');
    }
    h += '<h3>ON DUTY HERE</h3>' + (working.length
      ? `<ul class="mini-list">${working.map((c) => `<li><b style="color:${c.text}">${esc(c.name)}</b>: ${esc(c.duty.label)}</li>`).join('')}</ul>`
      : '<div class="sub">Nobody is working here right now.</div>');
    h += '<h3>PRESENT</h3>' + (here.length
      ? `<div class="chips">${here.map((c) => `<span class="chip" data-crew="${c.role}" style="border-color:${c.color}">${esc(c.name)}</span>`).join('')}</div>`
      : '<div class="sub">Empty.</div>');
    const evs = LOG.filter((e) => e.room === r.id).slice(0, 5);
    if (evs.length) h += `<h3>RECENT</h3><ul class="mini-list">${evs.map((e) => `<li><span class="t">${CD.fmtClock(e.t)}</span>${esc(logText(e))}</li>`).join('')}</ul>`;
    return h;
  }
  function coreHTML() {
    const m = bridge.snap.msd, st = bridge.snap.status;
    let x = `<h2 style="color:#66E0FF">${esc(assistantName())}</h2><div class="sub">Main computer · Computer Core</div>
      <div class="colorbar" style="background:#66E0FF"></div>
      <div class="about">The ship itself: every officer is summoned from here. The core brightens and gains an orbiting light for each chat turn in flight across your panes.</div>`;
    x += `<h3>LIVE</h3><div class="kv"><span class="k">Channels</span><span class="v">${S.inflight} in flight</span>`;
    if (m && m.llm) x += `<span class="k">Provider</span><span class="v">${esc(m.llm.provider_label)}</span><span class="k">Model</span><span class="v">${esc(m.llm.model)}</span>`;
    if (st) x += `<span class="k">Main channel</span><span class="v">${esc(st.step === 'idle' ? 'idle' : `${st.step}${st.detail ? ': ' + st.detail : ''}`)}</span>`;
    if (m) x += `<span class="k">Uptime</span><span class="v">${esc(CD.fmtDur(m.uptime_secs * 1000))}</span><span class="k">Last input</span><span class="v">${esc(CD.fmtDur(m.last_user_activity_secs * 1000))} ago</span>`;
    if (m && m.memory) x += `<span class="k">Context</span><span class="v">${esc(m.memory.used_pct)}% of window</span>`;
    x += '</div>';
    const cr = S.exact && bridge.snap.crew;
    if (cr) {
      const who = (t) => (CD.isOfficerRole(t.role) ? crewByRole[t.role].name : assistantName());
      const ts = cr.turns || [];
      x += `<h3>CHANNELS (${ts.length})</h3>` + (ts.length
        ? `<ul class="mini-list">${ts.map((t) => `<li><span class="t">${esc(CD.fmtDur(Date.now() - t.started_at * 1000))}</span>${esc(t.source === 'order' ? 'Order: ' + t.label : paneTag(t))} · <b>${esc(who(t))}</b>: ${esc(t.doing || '')}${t.provider ? ` <span class="t">${esc(t.provider)}</span>` : ''}</li>`).join('')}</ul>`
        : '<div class="sub">No channels open.</div>');
      const hs = (cr.agents || []).filter((a) => !a.role || a.role === 'core');
      if (hs.length) {
        x += `<h3>HELPERS (${hs.length})</h3><ul class="mini-list">${hs.map((a) => `<li><span class="t">${esc(CD.fmtDur(Date.now() - a.started_at * 1000))}</span>${esc(a.role === 'core' ? assistantName() : CD.helperLabel(a.type))}: ${esc(a.description || a.type)} · ${esc(a.doing || '')}</li>`).join('')}</ul>`;
      }
    }
    if (m && m.recent_tools && m.recent_tools.length) {
      x += `<h3>RECENT TOOLS</h3><ul class="mini-list">${m.recent_tools.slice(0, 6).map((r) => `<li><span class="t">${esc(r.ago)}s ago</span>${esc(r.name)}${r.ok ? '' : ' (failed)'}</li>`).join('')}</ul>`;
    }
    return x;
  }
  function overviewHTML() {
    const o = ordersSummary(), jobs = runningJobs(), onDuty = crew.filter((c) => c.duty).length;
    const src = S.mode === 'demo' ? 'Scripted demo mission. Press LIVE to return to real data.'
      : bridge.snap.online ? `Activity feed: ${esc(BRIDGE.replace(/^https?:\/\//, ''))}. Missions run in this page's local service.` : 'Bridge offline.';
    let x = `<h2 style="color:var(--orange)">${esc(roster().ship)}</h2><div class="sub">${src}</div>`;
    x += `<h3>RIGHT NOW</h3><div class="kv">
      <span class="k">Condition</span><span class="v">${esc(String(S.cond || '-').toUpperCase())}</span>
      <span class="k">Channels</span><span class="v">${S.inflight} in flight</span>
      <span class="k">Jobs</span><span class="v">${S.mode === 'live' ? (jobs.length ? jobs.map((j) => esc(CD.short(j.label, 44))).join('<br>') : 'none running') : 'demo'}</span>
      <span class="k">Orders</span><span class="v">${bridge.snap.orders ? `${o.active} active of ${o.total}${o.total && !o.active ? ' (all paused)' : ''}` : '-'}</span>
      <span class="k">On duty</span><span class="v">${onDuty} of ${crew.length} crew</span>
      <span class="k">Tracking</span><span class="v">${esc(trackingText())}</span></div>`;
    x += `<h3>HOW TO READ THE DECK</h3><ul class="legend">
      <li>Crew at a station with a <b>green bar</b> are working on something real. Click them to see what.</li>
      <li><b>Reported officer</b> identifies the actual chat or subagent runner and its current step.</li>
      <li><b>Assigned by rule</b> means a visual assignment for jobs, alerts or native orders; it does not identify their actual runner.</li>
      <li>A <b>glowing room</b> is an active system. <b>Pulsing red</b> needs you.</li>
      <li>The core gains <b>one orbiting light per chat turn</b> in flight.</li>
      <li>Off-duty crew roam and gather in the lounge. When the ship goes quiet they rest in quarters.</li>
      <li><b>L</b> toggles labels, <b>D</b> runs the demo mission, <b>Esc</b> clears selection.</li></ul>`;
    return x;
  }
  function renderDetail(force) {
    const sel = S.sel;
    let html;
    if (!sel) html = overviewHTML();
    else if (sel.type === 'crew') html = crewHTML(crewByRole[sel.role]);
    else if (sel.type === 'room') html = roomHTML(rooms[sel.id]);
    else html = coreHTML();
    if (!force && html === lastDetail) return;
    lastDetail = html;
    detail.innerHTML = html;
    detail.querySelectorAll('canvas[data-portrait]').forEach((cv) => {
      const c = crewByRole[cv.dataset.portrait];
      const g = cv.getContext('2d'); g.imageSmoothingEnabled = false;
      g.clearRect(0, 0, cv.width, cv.height);
      g.drawImage(c.img, 0, 0, FW, FH, 0, 0, cv.width, cv.height);
    });
  }
  detail.addEventListener('click', (e) => {
    const ack = e.target.closest('[data-ack]');
    if (ack) {
      S.acks[ack.dataset.ack] = Date.now(); saveAcks();
      log('Alert acknowledged', { level: 'ok' });
      recomputeLive(); renderDetail(true);
      return;
    }
    const chip = e.target.closest('[data-crew]');
    if (chip) select({ type: 'crew', role: chip.dataset.crew });
  });

  // Duty roster tab
  const dutyList = $('#duty-list');
  function renderDutyRoster() {
    const orders = bridge.snap.orders;
    if (!orders) { dutyList.innerHTML = `<div class="sub">${bridge.snap.online === false ? 'Bridge offline.' : 'Loading standing orders...'}</div>`; return; }
    if (!orders.length) { dutyList.innerHTML = '<div class="sub">No standing orders on the roster.</div>'; return; }
    const groups = {};
    for (const o of orders) { const c = CD.classifyOrder(o); (groups[c.role] = groups[c.role] || []).push({ o, room: c.room }); }
    let h = '';
    for (const role of CD.ROLE_ORDER) {
      const g = groups[role];
      if (!g) continue;
      const c = crewByRole[role];
      h += `<div class="group-h" style="color:${c.text}"><span class="dot" style="background:${c.color}"></span>${esc(c.name.toUpperCase())}<span class="sub">· ${g.length}</span></div>`;
      for (const { o, room } of g) {
        const running = S.duties.some((d) => d.key === 'order:' + o.id);
        const state = running ? 'running' : o.enabled ? 'active' : 'paused';
        const meta = [CD.humanCron(o.cron), rooms[room].name,
          o.enabled && o.next_run ? `next ${CD.fmtClock(o.next_run * 1000)}` : null,
          o.last_run ? `last ran ${CD.fmtAgo(o.last_run * 1000)}` : 'never run'].filter(Boolean).join(' · ');
        h += `<div class="order" data-crew="${role}"><div class="nm"><span class="state ${state}">${state.toUpperCase()}</span>${esc(o.name)}</div><div class="meta">${esc(meta)}</div></div>`;
      }
    }
    dutyList.innerHTML = h;
  }
  dutyList.addEventListener('click', (e) => { const o = e.target.closest('[data-crew]'); if (o) select({ type: 'crew', role: o.dataset.crew }); });

  // Systems footer (FTL power bars)
  const SYSTEMS = [
    ['core', 'CORE'], ['reactor', 'REACTOR'], ['engines', 'ENGINES'], ['memory', 'MEMORY'], ['cargo', 'CARGO'], ['storage', 'VRAM'],
    ['life', 'THERMAL'], ['sensors', 'SENSORS'], ['comms', 'COMMS'], ['shields', 'SHIELDS'], ['medbay', 'HULL'], ['ops', 'ORDERS'],
  ].map(([room, nm]) => ({ room, nm }));
  const sysEl = $('#systems');
  function buildSystems() {
    sysEl.innerHTML = '';
    for (const s of SYSTEMS) {
      const el = document.createElement('div'); el.className = 'sys';
      const bars = document.createElement('div'); bars.className = 'bars'; bars.innerHTML = '<i></i>'.repeat(6);
      const cv = document.createElement('canvas'); cv.width = 40; cv.height = 40;
      const g = cv.getContext('2d'); g.scale(40 / 15, 40 / 15);
      g.strokeStyle = g.fillStyle = rooms[s.room].color; g.lineWidth = 1.2;
      ICONS[rooms[s.room].icon](g, 7.5, 7.5);
      const nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = s.nm;
      const vl = document.createElement('div'); vl.className = 'vl'; vl.textContent = '-';
      el.append(bars, cv, nm, vl);
      el.addEventListener('click', () => select({ type: 'room', id: s.room }));
      sysEl.appendChild(el);
      Object.assign(s, { el, vl, bars: [...bars.children] });
    }
  }
  function refreshSystems() {
    for (const s of SYSTEMS) {
      const m = metricFor(s.room);
      const n = m && m.pct != null ? Math.round(clamp(m.pct, 0, 1) * 6) : 0;
      s.bars.forEach((b, i) => b.classList.toggle('on', i < n));
      s.el.className = `sys ${meterClass(m)}${S.sel && S.sel.type === 'room' && S.sel.id === s.room ? ' sel' : ''}`;
      s.vl.textContent = m ? (m.short || m.value) : '-';
      s.el.title = m ? `${rooms[s.room].name}: ${m.label}, ${m.value}` : rooms[s.room].name;
    }
  }

  // Top bar
  const hullBar = $('#hull-bar'), shieldPips = $('#shield-pips');
  hullBar.innerHTML = '<i></i>'.repeat(20);
  shieldPips.innerHTML = '<i></i>'.repeat(4);
  function refreshTop() {
    const h = bridge.snap.health;
    $('#ship-name').textContent = roster().ship;
    const hull = h ? h.hull : null;
    const n = hull != null ? Math.round(hull / 5) : 0;
    [...hullBar.children].forEach((el, i) => el.classList.toggle('on', i < n));
    hullBar.className = 'hull-bar' + (hull != null && hull < 30 ? ' crit' : hull != null && hull < 60 ? ' warn' : '');
    const sn = h ? Math.round(h.shield / 25) : 0;
    [...shieldPips.children].forEach((el, i) => el.classList.toggle('on', i < sn));
    const cond = $('#condition');
    if (S.mode === 'live' && bridge.snap.online === false) { cond.className = 'pill offline'; cond.textContent = 'NO LINK'; }
    else { cond.className = 'pill' + (S.cond === 'red' ? ' red' : S.cond === 'yellow' ? ' yellow' : ''); cond.textContent = `CONDITION ${String(S.cond || 'green').toUpperCase()}`; }
    cond.title = h && h.red_alert_reasons && h.red_alert_reasons.length ? h.red_alert_reasons.join(', ') : '';
    $('#ro-channels').textContent = String(S.inflight);
    const tr = $('#ro-tracking');
    tr.textContent = S.mode === 'demo' ? 'DEMO' : S.exact ? (S.inferred ? 'MIXED' : 'EXACT') : 'RULES';
    tr.className = 'v' + (S.mode !== 'demo' && S.exact ? ' exact' : '');
    $('#ro-tracking-box').title = trackingText();
    $('#ro-jobs').textContent = S.mode === 'demo' ? String(S.duties.filter((d) => d.kind === 'job').length) : String(runningJobs().length);
    const o = ordersSummary();
    $('#ro-orders').textContent = bridge.snap.orders ? `${o.active}/${o.total}` : '-';
    $('#ro-clock').textContent = CD.fmtClock(Date.now());
    const link = $('#link-state');
    if (bridge.snap.online) { link.className = 'link ok'; link.textContent = `LINK ${BRIDGE.replace(/^https?:\/\//, '')}`; }
    else if (bridge.snap.online === false) { link.className = 'link bad'; link.textContent = 'LINK LOST'; }
    else { link.className = 'link'; link.textContent = 'LINKING...'; }
  }
  function updateBanner() {
    const b = $('#banner');
    if (S.mode === 'demo') { b.className = 'banner show demo'; b.textContent = 'DEMO MISSION · scripted, not live data · press LIVE or D to return'; }
    else if (bridge.snap.online === false) { b.className = 'banner show'; b.textContent = `Bridge offline at ${BRIDGE.replace(/^https?:\/\//, '')}. Crew are idling. Press DEMO to watch a scripted mission.`; }
    else { b.className = 'banner'; b.textContent = ''; }
  }

  // ------------------------------------------------------- live & demo
  const bridge = new CD.Bridge(BRIDGE, KEY);

  function statusRoom(st) {
    if (!st || !st.step || st.step === 'idle') return null;
    const s = `${st.step} ${st.detail || ''}`.toLowerCase();
    if (/search|reading page|web/.test(s)) return 'sensors';
    if (/mail|inbox|email/.test(s)) return 'comms';
    if (/calendar|event|availability|screenshot|click|typing|keys|scroll|cursor|drag/.test(s)) return 'bridge';
    if (/memory/.test(s)) return 'memory';
    return null;
  }
  const FORCE_QUIET = params.get('quiet') === '1';
  function updateQuiet() {
    if (S.mode !== 'live') { S.quiet = false; return; }
    const m = bridge.snap.msd;
    const was = S.quiet;
    S.quiet = FORCE_QUIET || !!(bridge.snap.online && S.inflight === 0 && runningJobs().length === 0 && m && m.last_user_activity_secs > 1800);
    if (was && !S.quiet) {
      for (const c of crew) if (c.mode === 'sleep') { c.mode = 'idle'; c.idleUntil = nowMs() + rand(0, 3000); }
      log('Ship waking up. Crew back to stations', { level: 'info' });
    } else if (!was && S.quiet) {
      log('Ship is quiet. Off-duty crew turning in', { level: 'info' });
    }
  }
  function recomputeLive() {
    if (S.mode !== 'live') return;
    const snap = bridge.snap;
    if (!snap.online) { S.inflight = 0; S.roomAlerts = {}; S.coreLabel = ''; S.coreTarget = null; S.exact = false; S.core = null; S.inferred = false; applyDuties(missionDuties()); return; }
    const r = CD.deriveLive(snap, Date.now(), S.acks);
    S.inflight = r.inflight;
    S.roomAlerts = r.roomAlerts;
    S.cond = (snap.health && snap.health.alert) || 'green';
    S.exact = r.exact;
    S.inferred = r.inferred;
    S.core = r.core;
    if (r.exact) {
      // The core shows what the main computer itself is doing right now, and
      // lights the room that kind of work belongs to (web search: Sensors).
      const n = r.core.helpers.length;
      const label = r.core.doing || (n ? `${n} helper${n === 1 ? '' : 's'} working` : '');
      S.coreLabel = r.core.turns || n ? CD.short(label, 26).toUpperCase() : '';
      S.coreTarget = (r.core.zone && CD.ZONE_ROOM[r.core.zone]) || null;
    } else {
      const st = snap.status;
      S.coreLabel = st && st.step && st.step !== 'idle' ? CD.short(st.detail || st.step, 26).toUpperCase() : '';
      S.coreTarget = statusRoom(st);
    }
    applyDuties([...r.duties, ...missionDuties()]);
    updateQuiet();
  }
  function trackingText() {
    if (S.mode === 'demo') return 'Scripted demo';
    if (S.exact) return 'Chats, named subagents and crew missions report their officer and current step. Jobs, alerts and native orders use labeled visual assignments.';
    const st = bridge.snap.crewState;
    if (st === 'missing') return 'Keyword rules: this bridge has no crew feed yet (restart pending)';
    if (st === 'pending') return 'Checking for the crew feed';
    return 'Keyword rules: crew feed unavailable';
  }
  function missionDuties() {
    return (missionState?.runs || []).filter(r => r.status === 'running' && r.current_officer).flatMap(r => {
      const c = crew.find(o => o.agentId === r.current_officer);
      if (!c) return [];
      return [{ key: `mission:${r.id}:${r.turn}`, role: c.role, room: c.def.home, kind: 'mission',
        label: r.title, detail: r.current_step || 'Working with the crew', since: r.updated_at * 1000,
        exact: true, priority: 6, source: { type: 'mission', provider: r.provider, project: r.workspace } }];
    });
  }
  function receiveMissionState(state) {
    if (!state || (missionState && missionState.scope !== state.scope)) {
      dialogueEpoch++;
      for (const timer of conversationTimers) clearTimeout(timer);
      conversationTimers.clear();
      for (const b of [...bubbles]) if (b.kind === 'ambient' || b.kind === 'mission') killBubble(b);
      lastMissionEvent = 0;
      dialogue?.reset(state?.scope || 'unavailable');
    }
    missionState = state;
    if (!state) { recomputeLive(); return; }
    dialogue?.configure(state);
    document.documentElement.classList.toggle('crew-reduced-motion', !!state.settings.reduced_motion);
    if (!state.settings.banter_enabled) {
      dialogueEpoch++;
      for (const b of [...bubbles]) if (b.kind === 'ambient') killBubble(b);
    }
    const events = state.events || [];
    const first = lastMissionEvent === 0;
    for (const ev of events) {
      if (ev.id <= lastMissionEvent || S.mode !== 'live' || (first && ev.at * 1000 < Date.now() - 10000)) continue;
      const c = crew.find(o => o.agentId === ev.officer);
      if (c && ev.kind === 'message') { say(c, CD.short(ev.text, 200), 9000, 'mission'); remember(c, CD.short(ev.text, 180)); }
      if (ev.kind === 'learning') log(`${c?.name || ev.officer} learned: ${CD.short(ev.text, 140)}`, { level: 'info', role: c?.role });
    }
    lastMissionEvent = Math.max(lastMissionEvent, ...events.map(e => e.id || 0));
    recomputeLive();
  }
  window.addEventListener('crew-control-state', e => receiveMissionState(e.detail));
  function onCrew(prev, next) {
    const st = bridge.snap.crewState;
    if (st !== S.crewState) {
      if (st === 'live') log('Officer tracking online. Chats and named subagents now follow the bridge', { level: 'ok' });
      else if (S.crewState === 'live') log('Exact tracking lost. Falling back to keyword rules', { level: 'warn' });
      S.crewState = st;
    }
    if (!next) return;
    if (missionState && next.scope !== missionState.scope) receiveMissionState(null);
    if (S.crewScope !== null && S.crewScope !== next.scope) {
      LOG.length = 0;
      for (const c of crew) { c.history.length = 0; c.duty = null; c.queue = []; }
      S.firstSeen.clear();
      renderLog();
    }
    S.crewScope = next.scope;
    if (!prev || prev.since !== next.since || prev.scope !== next.scope) {
      // First sight of this feed: report what is already under way, no replay.
      for (const a of next.agents || []) {
        const officer = CD.isOfficerRole(a.role) ? a.role : null;
        log(`${officer ? 'is working on' : CD.helperLabel(a.type) + ' working on'}: ${a.description || a.type} (${a.doing})`,
          { role: officer, room: officer ? CD.ROLES[officer].home : 'core' });
      }
      return;
    }
    CD.diffCrew(prev, next).forEach(emitEvent);
  }
  function summarizeOnce() {
    if (S.summarized || !bridge.snap.jobs || !bridge.snap.orders) return;
    S.summarized = true;
    const jobs = runningJobs();
    for (const j of jobs) { const c = CD.classifyJob(j); log(`is running: ${j.label} (${CD.fmtDur((j.elapsed || 0) * 1000)} so far)`, { role: c.role, room: c.room }); }
    const o = ordersSummary();
    log(`Duty roster: ${o.total} standing order${o.total === 1 ? '' : 's'}, ${o.active ? o.active + ' active' : 'all paused'}`, { room: 'ops' });
    if (S.inflight) log(`${S.inflight} chat turn${S.inflight === 1 ? '' : 's'} in flight across your panes`, { room: 'core' });
  }
  bridge.on((what, snap, prev) => {
    if (what === 'jobs') CD.diffJobs(prev, snap.jobs).forEach(emitEvent);
    if (what === 'orders') { CD.diffOrders(prev, snap.orders).forEach(emitEvent); renderDutyRoster(); }
    if (what === 'health') CD.diffHealth(prev, snap.health).forEach(emitEvent);
    if (what === 'crew' && S.mode === 'live') onCrew(prev, snap.crew);
    if (what === 'online') log('Bridge link established (read-only)', { level: 'ok' });
    if (what === 'offline') log('Bridge link lost', { level: 'error' });
    if (what !== 'agents') recomputeLive();
    if (what === 'jobs' || what === 'orders') summarizeOnce();
    updateBanner();
  });

  const fill = (s) => String(s || '').replace(/\{(\w+)\}/g, (m, role) => (crewByRole[role] ? crewByRole[role].name : m));
  function demoTick() {
    const D = S.demo;
    const t = (nowMs() - D.start) / 1000;
    const cycle = Math.floor(t / CD.DEMO_LENGTH), tt = t % CD.DEMO_LENGTH;
    if (cycle !== D.cycle) { D.cycle = cycle; D.idx = 0; }
    let changed = false;
    while (D.idx < CD.DEMO.length && CD.DEMO[D.idx].t <= tt) {
      const st = CD.DEMO[D.idx++];
      changed = true;
      if ('inflight' in st) D.inflight = st.inflight;
      if ('duties' in st) {
        D.duties = st.duties.map(([role, room, kind, label, detail]) => ({
          key: `demo:${role}:${label}`, role, room, kind, label: fill(label), detail: fill(detail),
          since: Date.now(), priority: kind === 'alert' ? 7 : kind === 'chat' ? 2 : 5 }));
      }
      if ('alert' in st) D.alert = st.alert;
      D.core = st.core || '';
      (st.say || []).forEach(([role, text], i) => setTimeout(() => { if (S.mode === 'demo') say(crewByRole[role], fill(text)); }, i * 1700));
      if (st.log) log(fill(st.log[0]), { role: st.log[2], level: st.log[1], room: D.duties.length ? null : 'lounge' });
    }
    if (changed) {
      S.inflight = D.inflight;
      S.coreLabel = D.core;
      S.coreTarget = null;
      S.cond = 'green';
      S.roomAlerts = D.alert ? { [D.alert.room]: [{ key: 'demo', text: D.alert.text, since: Date.now() }] } : {};
      applyDuties(D.duties);
    }
  }
  function setMode(m) {
    if (S.mode === m) return;
    dialogueEpoch++;
    for (const timer of conversationTimers) clearTimeout(timer);
    conversationTimers.clear();
    for (const b of [...bubbles]) killBubble(b);
    S.mode = m;
    $('#mode-live').classList.toggle('on', m === 'live');
    $('#mode-demo').classList.toggle('on', m === 'demo');
    S.firstSeen.clear(); S.roomAlerts = {}; S.coreLabel = ''; S.coreTarget = null; S.quiet = false;
    for (const c of crew) { c.duty = null; c.queue = []; if (c.mode === 'work' || c.mode === 'sleep') c.mode = 'idle'; c.idleUntil = nowMs() + rand(200, 2500); }
    if (m === 'demo') {
      S.demo = { start: nowMs(), idx: 0, cycle: 0, duties: [], inflight: 0, core: '', alert: null };
      log('Demo mission started (scripted)', { level: 'info' });
      applyDuties([]);
    } else {
      log('Returned to live data', { level: 'info' });
      recomputeLive();
    }
    updateBanner();
    renderDetail(true);
  }

  // --------------------------------------------------------------- boot
  buildRoster();
  buildSystems();
  renderDutyRoster();
  log(`Crew deck online. Linking to ${BRIDGE.replace(/^https?:\/\//, '')}`, { level: 'info' });
  bridge.start();
  window.addEventListener("pagehide", () => bridge.stop());
  if (params.get('mode') === 'demo') setMode('demo');
  refreshTop(); refreshRoster(); refreshSystems(); renderDetail(true); updateBanner();
  window.__crewDeck = { S, V, crew, rooms, doors, planPath, setMode, select, bridge, toggleLabels };
  if (window.CrewControls?.state) receiveMissionState(window.CrewControls.state);

  let last = nowMs(), uiAcc = 0;
  function loop() {
    const t = nowMs();
    const dt = Math.min(0.05, (t - last) / 1000);
    last = t;
    if (S.mode === 'demo') demoTick();
    for (const c of crew) { updateCrew(c, dt, t); idleThink(c, t); }
    updateDoors(dt, t);
    chatterTick(t);
    S.roomSt = roomState();
    spawnPackets(t);
    draw(t, dt);
    updateBubbles(t);
    uiAcc += dt;
    if (uiAcc > 0.5) { uiAcc = 0; refreshTop(); refreshRoster(); refreshSystems(); renderDetail(false); }
    requestAnimationFrame(loop);
  }
  S.roomSt = roomState();
  requestAnimationFrame(loop);
})().catch((err) => {
  console.error(err);
  const b = document.getElementById('banner');
  if (b) { b.className = 'banner show'; b.textContent = 'Crew deck failed to start: ' + (err && err.message ? err.message : err); }
});
