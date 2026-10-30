'use strict';
/* Crew Deck: data layer.
   - Roles, the product roster, sprite slots.
   - Rules deciding which officer and which room own a job or a standing order.
   - A strictly read-only bridge adapter. GET only. It never touches /ui_events
     (that endpoint drains a shared queue the main dashboard depends on).
   - Exact tracking: when the bridge serves /crew_activity, officers follow what
     the bridge reports (summoned subagents, officers holding a channel, standing
     orders while they actually run). Older bridges answer 404 and the deck falls
     back to the keyword rules, rechecking quietly until the bridge restarts.
   - The scripted DEMO mission. */
(function () {
  const CD = (window.CD = {});

  // ---------------------------------------------------------------- roles
  CD.ROLES = {
    command:     { sprite: 's0', home: 'bridge',      title: 'Mission Architect · Strategy' },
    review:      { sprite: 's6', home: 'tactical',    title: 'First Officer · Code Review' },
    security:    { sprite: 's1', home: 'shields',     title: 'Security Chief' },
    engineering: { sprite: 's7', home: 'engineering', title: 'Chief Engineer · Test & Debug' },
    ops:         { sprite: 's3', home: 'engines',     title: 'Operations Chief · DevOps' },
    counsel:     { sprite: 's4', home: 'sanctuary',   title: 'Counselor · Reflection' },
    medical:     { sprite: 's2', home: 'medbay',      title: 'Medical Officer · Health' },
    advisor:     { sprite: 's5', home: 'lounge',      title: 'Advisor · Second Opinion' },
    content:     { sprite: 's8', home: 'comms',       title: 'Utility Officer · Content' },
  };
  CD.ROLE_ORDER = ['command', 'review', 'security', 'engineering', 'ops', 'counsel', 'medical', 'advisor', 'content'];

  CD.ROSTERS = {
  "retail": {
    "label": "DATA",
    "ship": "DAEMON",
    "computer": "DATA",
    "ids": {
      "command": "atlas",
      "review": "vector",
      "security": "sentinel",
      "engineering": "probe",
      "ops": "relay",
      "counsel": "echo",
      "medical": "pulse",
      "advisor": "sage",
      "content": "scout"
    },
    "names": {
      "command": "Atlas",
      "review": "Vector",
      "security": "Sentinel",
      "engineering": "Probe",
      "ops": "Relay",
      "counsel": "Echo",
      "medical": "Pulse",
      "advisor": "Sage",
      "content": "Scout"
    }
  }
};

  // ------------------------------------------------- ownership rules
  // First match wins. Jobs are classified by label + command.
  const JOB_RULES = [
    [/\b(security|audit|vuln\w*|threat\w*|secrets?)\b/i, 'security', 'shields'],
    [/\b(reels?|video|render\w*|transcod\w*|captions?|thumbnails?|carousel|blog|posts?|content|ffmpeg|whisper|tts)\b/i, 'content', 'comms'],
    [/\b(tests?|pytest|lint\w*|smoke|debug\w*|benchmark\w*|profil\w*)\b/i, 'engineering', 'engineering'],
    [/\b(scrape\w*|crawl\w*|research|leads?|scan\w*)\b/i, 'review', 'sensors'],
  ];
  CD.classifyJob = (job) => {
    const s = `${job.label || ''} ${job.command || ''}`;
    for (const [re, role, room] of JOB_RULES) if (re.test(s)) return { role, room };
    return { role: 'ops', room: 'engines' };
  };

  // Standing orders are classified by name + action (the prompt is too noisy).
  const ORDER_RULES = [
    [/\b(security|audit|threat\w*|vuln\w*)\b/i, 'security', 'shields'],
    [/\b(leads?|booking|calendly|flip|scan\w*|watch\w*|jobs?)\b/i, 'review', 'sensors'],
    [/\b(spam|clean ?up|moderat\w*)\b/i, 'security', 'shields'],
    [/\b(reels?|carousel|blog|posts?|content|instagram|ig|youtube|tiktok|threads|captions?|video|debrief)\b/i, 'content', 'comms'],
    [/(operator'?s log|journal|memoir|digest|spiritual|reflect\w*|meditat\w*)/i, 'counsel', 'sanctuary'],
    [/\b(health|fitness|sleep|workout|nutrition)\b/i, 'medical', 'medbay'],
    [/\b(tests?|debug\w*|benchmark\w*|perf\w*)\b/i, 'engineering', 'engineering'],
    [/\breview\w*\b/i, 'review', 'tactical'],
    [/\b(brief\w*|news|growth|strateg\w*|plan\w*|business|market\w*)\b/i, 'command', 'bridge'],
    [/\b(update\w*|deploy\w*|sync|droplet|backup\w*|archive\w*|queue|server|install\w*)\b/i, 'ops', 'engines'],
  ];
  CD.classifyOrder = (o) => {
    const s = `${o.name || ''} ${o.action || ''}`;
    for (const [re, role, room] of ORDER_RULES) if (re.test(s)) return { role, room };
    return { role: 'command', room: 'ops' };
  };

  // --------------------------------------------------------- formatting
  const pad = (n) => String(n).padStart(2, '0');
  CD.fmtClock = (ms) => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  CD.fmtClockS = (ms) => { const d = new Date(ms); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  CD.fmtDur = (ms) => {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${pad(s % 60)}s`;
    const h = Math.floor(m / 60);
    if (h < 48) return `${h}h ${pad(m % 60)}m`;
    return `${Math.floor(h / 24)}d ${h % 24}h`;
  };
  CD.fmtAgo = (ms) => (ms ? `${CD.fmtDur(Date.now() - ms)} ago` : 'never');
  CD.fmtBps = (bps) => {
    if (!(bps >= 0)) return '-';
    if (bps < 1024) return `${Math.round(bps)} B/s`;
    if (bps < 1048576) return `${(bps / 1024).toFixed(1)} KB/s`;
    return `${(bps / 1048576).toFixed(1)} MB/s`;
  };
  CD.short = (s, n = 42) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  CD.pick = (a) => a[Math.floor(Math.random() * a.length)];

  // Exact tracking helpers.
  // The kind of work a tool call is doing, mapped to the room it lights up.
  CD.ZONE_ROOM = { web: 'sensors', mail: 'comms', mcp: 'comms', calendar: 'bridge', screen: 'bridge',
                   memory: 'memory', shell: 'reactor', plan: 'ops' };
  // Generic subagents are not officers; they show on the core as helpers.
  CD.helperLabel = (type) => ({ explore: 'Explorer', 'general-purpose': 'Helper', claude: 'Helper', 'codex-helper': 'Codex helper' }[String(type || '').toLowerCase()] || String(type || 'Helper'));
  CD.isOfficerRole = (role) => !!(role && role !== 'core' && CD.ROLES[role]);

  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  CD.humanCron = (c) => {
    const p = String(c || '').trim().split(/\s+/);
    if (p.length !== 5) return c || '';
    const [mi, hr, dom, mon, dow] = p;
    const anyDay = dom === '*' && mon === '*';
    if (mi.startsWith('*/') && hr === '*' && anyDay && dow === '*') return `every ${mi.slice(2)} min`;
    if (/^\d+$/.test(mi) && hr === '*' && anyDay && dow === '*') return `hourly at :${pad(mi)}`;
    if (/^\d+$/.test(mi) && hr.startsWith('*/') && anyDay && dow === '*') return `every ${hr.slice(2)}h at :${pad(mi)}`;
    if (/^\d+$/.test(mi) && /^[\d,]+$/.test(hr) && anyDay) {
      const times = hr.split(',').map((h) => `${pad(h)}:${pad(mi)}`).join(', ');
      if (dow === '*') return `daily ${times}`;
      if (dow === '1-5') return `weekdays ${times}`;
      if (/^[\d,]+$/.test(dow)) return `${dow.split(',').map((d) => DOW[+d % 7]).join('/')} ${times}`;
    }
    return c;
  };

  // ------------------------------------------------------ bridge adapter
  CD.Bridge = class {
    constructor(base, key) {
      this.base = String(base || '').replace(/\/+$/, '');
      this.key = key || '';
      this.snap = { online: null, health: null, status: null, jobs: null, orders: null, msd: null, agents: null,
                    crew: null, crewState: 'pending', errors: {}, lastOk: 0 };
      this.listeners = [];
      this.timers = [];
      this.crewTimer = null;
    }
    url(path) { return this.base + path; }
    async get(path, ms = 6000) {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), ms);
      try {
        const r = await window.CrewAccess.fetch(this.url(path), { cache: 'no-store', signal: ctl.signal });
        if (r.status === 401) throw new Error('Authentication required. Reopen Crew Deck from the dashboard.');
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
      } finally {
        clearTimeout(t);
      }
    }
    poll(name, path, every, transform) {
      let busy = false;
      const run = async () => {
        if (busy) return;
        busy = true;
        try {
          const data = await this.get(path);
          const prev = this.snap[name];
          this.snap[name] = transform ? transform(data) : data;
          this.snap.lastOk = Date.now();
          delete this.snap.errors[name];
          if (this.snap.online !== true) { this.snap.online = true; this.emit('online', null); }
          this.emit(name, prev);
        } catch (e) {
          this.snap.errors[name] = String((e && e.message) || e);
          if (name === 'health' && this.snap.online !== false) { this.snap.online = false; this.emit('offline', null); }
        } finally {
          busy = false;
        }
      };
      run();
      this.timers.push(setInterval(run, every));
    }
    // Exact officer tracking. Polled fast while the bridge serves it. A bridge
    // without the endpoint answers 404: back off to a slow recheck so the deck
    // switches to exact mode on its own once the bridge is restarted.
    pollCrew() {
      let busy = false;
      const run = async () => {
        if (busy) return;
        busy = true;
        let next = 1500;
        const prev = this.snap.crew;
        const prevState = this.snap.crewState;
        try {
          const d = await this.get('/crew_activity', 5000);
          const ok = !!(d && d.available);
          this.snap.crew = ok ? d : null;
          this.snap.crewState = ok ? 'live' : 'unavailable';
          delete this.snap.errors.crew;
          if (!ok) next = 15000;
        } catch (e) {
          const msg = String((e && e.message) || e);
          this.snap.errors.crew = msg;
          this.snap.crew = null;
          this.snap.crewState = /HTTP 404/.test(msg) ? 'missing' : 'error';
          next = /HTTP 404/.test(msg) ? 20000 : 6000;
        } finally {
          busy = false;
          if (this.snap.crew || prev || this.snap.crewState !== prevState) this.emit('crew', prev);
          this.crewTimer = setTimeout(run, next);
        }
      };
      run();
    }
    start() {
      this.poll('health', '/ships_health', 2000);
      this.poll('status', '/status', 2500);
      this.poll('jobs', '/jobs', 4000, (d) => d.jobs || []);
      this.poll('orders', '/standing_orders', 12000, (d) => d.orders || []);
      this.poll('msd', '/msd', 15000);
      this.pollCrew();
      // One-shot roster detection.
      this.get('/agents', 12000)
        .then((d) => { this.snap.agents = (d.agents || []).map((a) => a.id); this.emit('agents', null); })
        .catch(() => {});
    }
    stop() { this.timers.forEach(clearInterval); clearTimeout(this.crewTimer); }
    on(fn) { this.listeners.push(fn); }
    emit(what, prev) { for (const fn of this.listeners) fn(what, this.snap, prev); }
  };

  // ------------------------------------------------- live duty derivation
  // Exact duties identify the actual runner. Other duties are visual assignments.
  CD.deriveLive = (snap, now, acks) => {
    const duties = [];
    const roomAlerts = {};
    const h = snap.health || {};

    if (h.alert === 'red' || h.red_alert_manual) {
      duties.push({ key: 'cond:red', role: 'security', room: 'shields', kind: 'alert', label: 'Red alert',
        detail: (h.red_alert_reasons || []).join(' · ') || 'Raised manually', priority: 9 });
    } else if (h.alert === 'yellow') {
      duties.push({ key: 'cond:yellow', role: 'security', room: 'shields', kind: 'alert', label: 'Yellow alert',
        detail: (h.red_alert_reasons || []).join(' · ') || 'Systems under strain', priority: 8 });
    }

    for (const j of snap.jobs || []) {
      const c = CD.classifyJob(j);
      if (j.status === 'running') {
        duties.push({ key: 'job:' + j.id, role: c.role, room: c.room, kind: 'job', label: j.label || 'Background job',
          detail: `Running ${CD.fmtDur((j.elapsed || 0) * 1000)}`, since: (j.started_at || 0) * 1000, priority: 5,
          source: { type: 'job', id: j.id, project: j.project_path || '', command: j.command || '' } });
      } else if ((j.status === 'failed' || j.status === 'timeout') && j.finished_at &&
                 now - j.finished_at * 1000 < 20 * 60e3 && !acks[j.id]) {
        (roomAlerts[c.room] = roomAlerts[c.room] || []).push({ key: j.id, text: `Job failed: ${j.label}`, since: j.finished_at * 1000 });
        duties.push({ key: 'fail:' + j.id, role: 'engineering', room: c.room, kind: 'alert', label: 'Tracing a failed job',
          detail: `${j.label} (exit ${j.exit_code ?? '?'})`, since: j.finished_at * 1000, priority: 7,
          source: { type: 'job', id: j.id, project: j.project_path || '' } });
      }
    }

    // Exact feed from the bridge, or null on a bridge without /crew_activity.
    const crew = snap.crew && snap.crew.available ? snap.crew : null;

    // Standing orders. With the feed, an order is "running" exactly while the
    // bridge is executing it; without it, a recent last_run stands in.
    const runningOrders = new Map();
    if (crew) {
      for (const o of crew.orders || []) runningOrders.set(String(o.id), { since: (o.started_at || 0) * 1000, doing: '' });
      for (const t of crew.turns || []) {
        if (t.source !== 'order' || !t.order_id) continue;
        const r = runningOrders.get(String(t.order_id)) || { since: (t.started_at || 0) * 1000, doing: '' };
        r.doing = t.doing || '';
        runningOrders.set(String(t.order_id), r);
      }
    }
    for (const o of snap.orders || []) {
      const run = runningOrders.get(String(o.id));
      // LLM orders are drawn from their actual runner below, including the core.
      if (crew && (crew.turns || []).some((t) => t.source === 'order' && String(t.order_id) === String(o.id))) continue;
      if (!o.enabled && !run) continue;
      const c = CD.classifyOrder(o);
      const last = (o.last_run || 0) * 1000;
      const next = (o.next_run || 0) * 1000;
      if (run) {
        duties.push({ key: 'order:' + o.id, role: c.role, room: c.room, kind: 'order', label: o.name,
          detail: run.doing ? `Running: ${run.doing}` : 'Standing order running', since: run.since, priority: 5,
          source: { type: 'order', id: o.id }, exact: false });
      } else if (!crew && last && now - last < 4 * 60e3) {
        duties.push({ key: 'order:' + o.id, role: c.role, room: c.room, kind: 'order', label: o.name,
          detail: 'Standing order executing', since: last, priority: 4, source: { type: 'order', id: o.id } });
      } else if (o.enabled && next && next - now < 90e3 && next > now - 30e3) {
        duties.push({ key: 'prep:' + o.id, role: c.role, room: c.room, kind: 'standby', label: 'Standing by: ' + o.name,
          detail: 'Due at ' + CD.fmtClock(next), since: now, priority: 3, source: { type: 'order', id: o.id } });
      }
    }

    // What the main computer itself is doing (exact mode only).
    const core = { turns: 0, doing: '', zone: null, project: '', last: 0, helpers: [] };
    let inflight;
    if (crew) {
      for (const a of crew.agents || []) {
        if (CD.isOfficerRole(a.role)) {
          duties.push({ key: 'agent:' + a.id, role: a.role, room: CD.ROLES[a.role].home, kind: 'agent',
            label: a.description || 'Assigned task', detail: (a.doing || 'Working') + (a.project ? ` · ${a.project}` : ''),
            since: (a.started_at || 0) * 1000, priority: 6, exact: true,
            source: { type: 'agent', id: a.id, agentType: a.type, project: a.project, pane: a.pane,
                      background: a.background, tools: a.tools, summary: a.summary } });
        } else if (!a.role || a.role === 'core') {
          core.helpers.push(a);
        }
      }
      for (const t of crew.turns || []) {
        if (CD.isOfficerRole(t.role)) {
          const voice = t.source === 'voice';
          const order = t.source === 'order';
          duties.push({ key: 'turn:' + t.id, role: t.role, room: CD.ROLES[t.role].home, kind: order ? 'order' : voice ? 'voice' : 'chat',
            label: order ? t.label || 'Standing order' : voice ? 'On a voice call with you' : `Talking with you${t.project ? ' in ' + t.project : ''}`,
            detail: t.doing || 'Listening', since: (t.started_at || 0) * 1000, priority: 6, exact: true,
            source: { type: 'turn', id: t.id, project: t.project, pane: t.pane, provider: t.provider } });
        } else {
          core.turns += 1;
          if ((t.last_event_at || 0) >= core.last) {
            core.last = t.last_event_at || 0;
            core.doing = t.doing || '';
            core.zone = t.action ? t.action.zone : null;
            core.project = t.project || '';
          }
        }
      }
      inflight = (crew.turns || []).length;
    } else {
      inflight = h.llm_inflight ?? (snap.msd && snap.msd.llm && snap.msd.llm.inflight) ?? 0;
      if (inflight > 0) {
        duties.push({ key: 'chat', role: 'command', room: 'bridge', kind: 'chat',
          label: `${inflight} channel${inflight === 1 ? '' : 's'} open with you`,
          detail: (snap.msd && snap.msd.llm && snap.msd.llm.provider_label) || 'Main computer online', since: now, priority: 2 });
      }
    }
    return { duties, roomAlerts, inflight, exact: !!crew, inferred: duties.some((d) => !d.exact), core };
  };

  // ---------------------------------------------------- change events
  CD.diffJobs = (prev, next) => {
    const ev = [];
    if (!prev) return ev;
    const before = new Map(prev.map((j) => [j.id, j]));
    for (const j of next) {
      const c = CD.classifyJob(j);
      const old = before.get(j.id);
      if (!old && j.status === 'running') {
        ev.push({ role: c.role, room: c.room, level: 'info', log: `started: ${j.label}`,
          say: CD.pick([`On it: ${CD.short(j.label, 34)}.`, `Spinning up ${CD.short(j.label, 34)}.`]) });
      } else if (old && old.status === 'running' && j.status !== 'running') {
        if (j.status === 'ok') {
          ev.push({ role: c.role, room: c.room, level: 'ok', log: `finished: ${j.label}`, say: `${CD.short(j.label, 34)}: done, clean.` });
        } else if (j.status === 'failed' || j.status === 'timeout') {
          ev.push({ role: c.role, room: c.room, level: 'error', log: `reports a failure: ${j.label} (exit ${j.exit_code ?? '?'})`,
            say: `${CD.short(j.label, 30)} failed. Flagging it.` });
        } else {
          ev.push({ role: c.role, room: c.room, level: 'warn', log: `reports ${j.status}: ${j.label}`, say: `${CD.short(j.label, 34)}: ${j.status}.` });
        }
      }
    }
    return ev;
  };

  CD.diffOrders = (prev, next) => {
    const ev = [];
    if (!prev) return ev;
    const before = new Map(prev.map((o) => [o.id, o]));
    for (const o of next) {
      const old = before.get(o.id);
      if (!old) continue;
      const c = CD.classifyOrder(o);
      if ((o.last_run || 0) > (old.last_run || 0)) {
        ev.push({ role: c.role, room: c.room, level: 'info', log: `ran standing order: ${o.name}`, say: `Standing order: ${CD.short(o.name, 34)}.` });
      }
      if (!!o.enabled !== !!old.enabled) {
        ev.push({ role: null, room: 'ops', level: 'info', log: `Standing order ${o.enabled ? 'activated' : 'paused'}: ${o.name}` });
      }
    }
    return ev;
  };

  // Bridge crew events since the last poll, turned into log lines and speech.
  // First sight of a feed (or a restarted bridge) replays nothing.
  CD.diffCrew = (prev, next) => {
    const ev = [];
    if (!prev || !next || !next.available || prev.since !== next.since || prev.scope !== next.scope) return ev;
    const lastSeq = (prev.events || []).reduce((m, e) => Math.max(m, e.seq || 0), 0);
    const fresh = (next.events || []).filter((e) => (e.seq || 0) > lastSeq).sort((a, b) => a.seq - b.seq);
    const agents = new Map([...(next.agents || []), ...(next.recent_agents || [])].map((a) => [a.id, a]));
    const turns = new Map([...(next.turns || []), ...(next.recent_turns || [])].map((t) => [t.id, t]));
    const home = (role) => CD.ROLES[role].home;
    for (const e of fresh) {
      const a = e.agent ? agents.get(e.agent) : null;
      const t = e.turn ? turns.get(e.turn) : null;
      const officer = CD.isOfficerRole(e.role) ? e.role : null;
      if (e.kind === 'agent_start') {
        const what = (a && a.description) || String(e.text || '').replace(/^started: /, '');
        if (officer) {
          ev.push({ role: officer, room: home(officer), level: 'info', log: `summoned: ${what}`,
            say: CD.pick([`On it: ${CD.short(what, 34)}.`, 'Acknowledged. On my way.', `${CD.short(what, 34)}. Starting now.`]) });
        } else {
          ev.push({ role: null, room: 'core', level: 'info', log: `${CD.helperLabel(e.type)} launched: ${what}` });
        }
      } else if (e.kind === 'agent_end') {
        const what = (a && a.description) || e.type || 'task';
        const st = (a && a.status) || 'completed';
        const level = st === 'failed' ? 'error' : st === 'completed' ? 'ok' : 'warn';
        const verb = { completed: 'finished', failed: 'failed', stopped: 'was stopped', ended: 'stood down' }[st] || st;
        const took = a && a.ended_at && a.started_at ? ` (${CD.fmtDur((a.ended_at - a.started_at) * 1000)})` : '';
        if (officer) {
          ev.push({ role: officer, room: home(officer), level, log: `${verb}: ${what}${took}`,
            say: st === 'completed' ? CD.pick([`${CD.short(what, 30)}: done.`, 'Done. Reporting back.'])
              : st === 'failed' ? `${CD.short(what, 30)} failed.` : null });
        } else {
          ev.push({ role: null, room: 'core', level, log: `${CD.helperLabel(e.type)} ${verb}: ${what}${took}` });
        }
      } else if (e.kind === 'turn_start' || e.kind === 'turn_end') {
        const start = e.kind === 'turn_start';
        const bad = !start && t && t.ok === false;
        const took = !start && t && t.ended_at ? ` after ${CD.fmtDur((t.ended_at - t.started_at) * 1000)}` : '';
        const where = t && t.project ? ` in ${t.project}` : '';
        if (t && t.source === 'order') {
          const c = officer ? { role: officer, room: home(officer) } : { role: null, room: 'core' };
          ev.push(start
            ? { role: c.role, room: c.room, level: 'info', log: `started standing order: ${t.label}`, say: `Standing order: ${CD.short(t.label, 30)}.` }
            : { role: c.role, room: c.room, level: bad ? 'error' : 'ok', log: `finished standing order: ${t.label}${took}` });
        } else if (officer) {
          const voice = t && t.source === 'voice';
          ev.push(start
            ? { role: officer, room: home(officer), level: 'info', log: voice ? 'answered a voice call' : `opened a channel with you${where}`, say: voice ? 'Listening.' : 'Channel open.' }
            : { role: officer, room: home(officer), level: bad ? 'error' : 'ok', log: `closed the channel${where}${took}` });
        } else {
          const via = start && t && t.provider ? ` (${t.provider})` : '';
          ev.push({ role: null, room: 'core', level: bad ? 'error' : 'info',
            log: `${start ? 'Channel opened' : 'Channel closed'}${where}${via}${took}${bad ? ', with an error' : ''}` });
        }
      }
    }
    return ev;
  };

  CD.diffHealth = (prev, next) => {
    const ev = [];
    if (!prev || !next) return ev;
    if (prev.alert !== next.alert) {
      const reasons = next.red_alert_reasons || [];
      ev.push({ role: 'security', room: 'shields', level: next.alert === 'green' ? 'ok' : next.alert === 'red' ? 'error' : 'warn',
        log: `reports condition ${String(next.alert).toUpperCase()}${reasons.length ? ': ' + reasons.join(', ') : ''}`,
        say: next.alert === 'green' ? 'Condition green. Standing down.' : `Condition ${next.alert}. ${reasons[0] || ''}` });
    }
    const pi = prev.llm_inflight || 0, ni = next.llm_inflight || 0;
    if (pi === 0 && ni > 0) ev.push({ role: null, room: 'bridge', level: 'info', log: 'A channel opened with you' });
    if (pi > 0 && ni === 0) ev.push({ role: null, room: 'bridge', level: 'info', log: 'All channels quiet' });
    if (!!prev.tunnel !== !!next.tunnel) {
      ev.push({ role: null, room: 'comms', level: next.tunnel ? 'ok' : 'warn', log: `Remote link ${next.tunnel ? 'restored' : 'lost'}` });
    }
    return ev;
  };

  // -------------------------------------------------------------- demo
  // Placeholders like {review} become the officer names of the active roster.
  CD.DEMO_LENGTH = 86;
  CD.DEMO = [
    { t: 0, inflight: 1, duties: [['command', 'bridge', 'chat', 'Channel open with you', 'Demo mission: ship a new feature']],
      say: [['command', 'Channel open. What are we building?']], log: ['opened a channel with you', 'info', 'command'] },
    { t: 6, inflight: 1, duties: [['command', 'ops', 'plan', 'Planning the feature', 'Spec and task order'],
                                  ['review', 'ops', 'plan', 'Planning session', 'Scoping the spec with {command}']],
      say: [['command', 'Planning session. {review}, with me.']], log: ['called a planning session in Operations', 'info', 'command'] },
    { t: 13, say: [['review', 'Scope is tight. Three steps.']] },
    { t: 17, inflight: 3, core: 'BUILDING', duties: [['command', 'bridge', 'chat', 'Overseeing the build', 'The computer is writing the code'],
                                                     ['review', 'tactical', 'standby', 'Waiting on the build', 'Review queued']],
      say: [['command', 'Computer, build it.']], log: ['handed the build to the computer', 'info', 'command'] },
    { t: 24, inflight: 3, core: 'BUILDING', duties: [['command', 'bridge', 'chat', 'Overseeing the build', 'The computer is writing the code'],
                                                     ['review', 'tactical', 'review', 'Reviewing the diff', '5 files changed']],
      say: [['review', 'Diff is in. Reviewing now.']], log: ['started the code review', 'info', 'review'] },
    { t: 31, inflight: 2, duties: [['command', 'bridge', 'chat', 'Overseeing the build', ''],
                                   ['review', 'tactical', 'review', 'Reviewing the diff', 'Two nits filed'],
                                   ['security', 'shields', 'scan', 'Security pass', 'Scanning the change for threats']],
      say: [['review', 'Two nits. Otherwise clean.'], ['security', 'Scanning for vulnerabilities.']], log: ['started the security pass', 'info', 'security'] },
    { t: 38, inflight: 2, duties: [['command', 'bridge', 'chat', 'Overseeing the build', ''],
                                   ['security', 'shields', 'scan', 'Security pass', 'No findings'],
                                   ['engineering', 'engineering', 'test', 'Running the test suite', '42 tests']],
      say: [['security', 'No threats found. Clear to proceed.'], ['engineering', 'Running the test suite.']], log: ['reports the security pass is clean', 'ok', 'security'] },
    { t: 45, inflight: 2, alert: { room: 'engineering', text: 'Test failure: 1 of 42' },
      duties: [['command', 'bridge', 'chat', 'Overseeing the build', ''],
               ['engineering', 'engineering', 'alert', 'Tracing a test failure', '1 of 42 failed']],
      say: [['engineering', 'One failure. Tracing the root cause.']], log: ['found a test failure in Engineering', 'error', 'engineering'] },
    { t: 52, inflight: 2, alert: null, duties: [['command', 'bridge', 'chat', 'Overseeing the build', ''],
                                                ['engineering', 'engineering', 'test', 'Re-running the suite', '42 of 42 passing'],
                                                ['ops', 'engines', 'job', 'Deploying the build', 'Pushing to the server']],
      say: [['engineering', 'Fixed. 42 for 42.'], ['ops', 'Deploying now.']], log: ['started the deploy after tests went green', 'ok', 'ops'] },
    { t: 59, inflight: 1, duties: [['command', 'bridge', 'chat', 'Channel open with you', ''],
                                   ['ops', 'engines', 'job', 'Verifying the deploy', 'Health checks'],
                                   ['content', 'comms', 'draft', 'Drafting release notes', 'Changelog and socials']],
      say: [['ops', 'Deployed. Server is healthy.'], ['content', 'Drafting the release notes.']], log: ['verified the deploy', 'ok', 'ops'] },
    { t: 66, inflight: 1, duties: [['command', 'bridge', 'chat', 'Channel open with you', ''],
                                   ['content', 'comms', 'draft', 'Drafting release notes', 'Changelog and socials'],
                                   ['counsel', 'sanctuary', 'order', 'Weekly digest', 'Standing order fired on schedule']],
      say: [['counsel', "Compiling this week's reflections."]], log: ['ran standing order: Weekly digest', 'info', 'counsel'] },
    { t: 73, inflight: 0, duties: [], say: [['advisor', 'Good work today. Sit a while.']], log: ['Mission complete. Crew standing down', 'ok', null] },
    { t: 79, say: [['command', 'Well done, everyone.']] },
  ];
})();
