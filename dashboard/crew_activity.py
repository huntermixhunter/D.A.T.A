"""Crew activity: a live, read-only picture of which officer is doing what.

Feeds GET /crew_activity, which the Crew Deck polls so each officer walks to
the work they are actually doing instead of work guessed from keywords.

What the bridge reports in. Every call is optional and fail-safe.

  begin_turn / end_turn   One chat turn on any entry path (pane chat stream,
                          sync chat, standing order, voice) and the officer
                          the user is talking to on that pane.
  claude_event            Raw Claude Code stream-json events. A subagent is
                          opened by its Agent tool call and refined by the
                          CLI's own task_started / task_progress /
                          task_notification system events. Nested tool calls
                          carry parent_tool_use_id, which credits them to the
                          subagent instead of the main turn.
  codex_event             Raw `codex exec --json` events: item.started and
                          item.completed for commands, file edits, MCP tools
                          and web searches.
  tap(sse)                Wraps a runner's send_sse callback. Sees the
                          structured `tool` events the other runners emit
                          and notices when reply text starts to stream.
  order_start / order_end Bracket a standing order, LLM or not.

Privacy: nothing here stores prompts, messages, tool inputs, command lines,
search queries, or tool output. It keeps tool names, file basenames, a URL's
host, the short description a model writes for a subagent or a Bash call,
and timing. Every string is length-capped.

Robustness: every public method swallows its own exceptions (counted in
`errors`). A bug in this module can leave the deck stale. It can never break
a chat turn.
"""
import collections
import itertools
import re
import shlex
import threading
import time
import urllib.parse

VERSION = 1

# Role slots the Crew Deck draws. "core" is the main computer (no avatar).
ROLES = ("command", "review", "security", "engineering", "ops",
         "counsel", "medical", "advisor", "content", "core")

# Every name an officer answers to, mapped to a role slot: the product ids, and plugin or built-in agent types with an obvious
# counterpart. Generic helpers (Explore, general-purpose, claude) are left
# out on purpose. They are not officers, so they stay unassigned rather than
# being pinned on someone who is not doing the work.
_ROLE_OF = {
    "atlas": "command", "plan": "command",
    "vercel:ai-architect": "command",
    "vector": "review", "agent-skills:code-reviewer": "review",
    "sentinel": "security",
    "agent-skills:security-auditor": "security",
    "probe": "engineering",
    "agent-skills:test-engineer": "engineering",
    "vercel:performance-optimizer": "engineering",
    "relay": "ops", "vercel:deployment-expert": "ops",
    "echo": "counsel",
    "pulse": "medical",
    "sage": "advisor",
    "scout": "content",
    "data": "core", "forge": "core", "computer": "core",
}

_AGENT_TASK_TYPES = {"local_agent", "remote_agent", "in_process_teammate"}
_TERMINAL = {"completed": "completed", "complete": "completed", "success": "completed",
             "failed": "failed", "error": "failed",
             "killed": "stopped", "stopped": "stopped",
             "cancelled": "stopped", "canceled": "stopped"}


def role_for(name):
    """Role slot for an officer id or agent type. None for generic helpers."""
    if not name:
        return None
    return _ROLE_OF.get(str(name).strip().lower())


# ------------------------------------------------------------ text helpers
def _cap(s, n=80):
    s = " ".join(str(s if s is not None else "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


def _base(path):
    p = str(path or "").strip().strip("\"'")
    if not p:
        return ""
    p = p.replace("\\", "/").rstrip("/")
    return _cap(p.rsplit("/", 1)[-1], 60)


def _host(url):
    try:
        return _cap(urllib.parse.urlparse(str(url or "")).hostname or "", 60)
    except Exception:
        return ""


def _exe(tok):
    t = str(tok or "").strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if t.endswith(ext):
            t = t[: -len(ext)]
    return _cap(t, 24)


_SHELLS = {"bash", "sh", "zsh", "pwsh", "powershell", "cmd"}
_SKIP_TOKENS = {"-c", "-lc", "-l", "/c", "/s", "/d", "/k", "-command", "-noprofile", "-noninteractive",
                "-nologo", "-executionpolicy", "bypass", "-windowstyle", "hidden", "&", "{", "(", "&&",
                "call", "cd", "start"}


def _split(s):
    try:
        if "\\" in s:
            # Windows paths: POSIX rules would eat every backslash.
            return [t.strip("\"'") for t in shlex.split(s, posix=False)]
        return shlex.split(s, posix=True)
    except ValueError:
        return s.split()


def _program(command):
    """Name of the program a shell command runs, with shell wrappers
    (`bash -lc '...'`, `powershell -Command ...`) peeled off. Never the
    arguments: those can carry tokens and paths."""
    try:
        toks = [str(t) for t in command] if isinstance(command, (list, tuple)) else _split(str(command or ""))
        for _ in range(4):
            toks = [t for t in toks if t.strip() and t.lower() not in _SKIP_TOKENS]
            if not toks:
                return ""
            head = _exe(toks[0])
            if head in _SHELLS and len(toks) > 1:
                toks = _split(toks[1]) if len(toks) == 2 else toks[1:]
                continue
            # Assignments and inline scripts are not executable names and may
            # include credentials. Emit no target when parsing is uncertain.
            return head if re.fullmatch(r"[a-z_][a-z0-9_.-]{0,50}", head) else ""
        return ""
    except Exception:
        return ""


def describe_tool(name, inp=None):
    """A privacy-safe description of one tool call.

    Returns {"verb", "target", "zone"}. `zone` is a coarse kind of work the
    deck can light a room for: code, shell, web, mail, calendar, screen,
    memory, agent, plan, mcp, skill, other."""
    n = str(name or "")
    i = inp if isinstance(inp, dict) else {}
    low = n.lower()
    path = i.get("file_path") or i.get("path") or i.get("notebook_path") or i.get("filename") or ""

    def out(verb, target="", zone="other"):
        return {"verb": _cap(verb, 60), "target": _cap(target, 60), "zone": zone}

    if n in ("Read", "read_file", "view", "View"):
        return out("Reading", _base(path), "code")
    if n in ("Write", "write_file", "create_file"):
        return out("Writing", _base(path), "code")
    if n in ("Edit", "MultiEdit", "edit_file", "str_replace", "apply_patch", "NotebookEdit"):
        return out("Editing", _base(path), "code")
    if n in ("Grep", "grep", "search_files", "Search"):
        return out("Searching code", "", "code")
    if n in ("Glob", "glob", "LS", "list_directory"):
        return out("Looking through files", _base(path) if n in ("LS", "list_directory") else "", "code")
    if n in ("Bash", "PowerShell", "terminal", "shell", "run_command", "local_shell"):
        desc = i.get("description")
        if desc:
            return out(desc, "", "shell")
        prog = _program(i.get("command"))
        return out("Running", prog, "shell") if prog else out("Running a command", "", "shell")
    if n in ("execute_python",):
        return out("Running Python", "", "shell")
    if n in ("WebSearch", "web_search"):
        return out("Searching the web", "", "web")
    if n in ("WebFetch", "web_extract", "fetch", "web_fetch"):
        host = _host(i.get("url"))
        return out("Reading", host, "web") if host else out("Reading a web page", "", "web")
    if n in ("Agent", "Task"):
        return out("Briefing", i.get("subagent_type") or "a helper", "agent")
    if n in ("Skill", "load_skill"):
        return out("Loading skill", i.get("skill") or i.get("name") or "", "skill")
    if n in ("TodoWrite", "update_plan"):
        return out("Updating the plan", "", "plan")
    if n in ("ToolSearch",):
        return out("Looking up tools", "", "other")
    if n.startswith("mcp__"):
        parts = n.split("__")
        server = (parts[1] if len(parts) > 1 else "mcp").replace("_", " ")
        tool = (parts[2] if len(parts) > 2 else "").replace("_", " ")
        return out("Using " + server, tool, "mcp")
    if low.startswith("mail_"):
        return out("Working the mail", "", "mail")
    if low.startswith("calendar_"):
        return out("Checking the calendar", "", "calendar")
    if low.startswith("desktop_") or n in ("computer", "take_screenshot"):
        return out("Driving the screen", "", "screen")
    if n in ("remember", "recall_memory", "search_history"):
        return out("Consulting memory", "", "memory")
    return out(n.replace("_", " ").strip() or "Working", "", "other")


def _codex_action(item):
    """describe_tool equivalent for one `codex exec --json` item."""
    it = str(item.get("type") or item.get("item_type") or "")
    if it == "command_execution":
        return describe_tool("Bash", {"command": item.get("command")})
    if it == "file_change":
        changes = item.get("changes") or []
        paths = [c.get("path") for c in changes if isinstance(c, dict) and c.get("path")]
        target = _base(paths[0]) if paths else ""
        if len(paths) > 1:
            target = f"{target} +{len(paths) - 1}"
        return {"verb": "Editing", "target": target, "zone": "code"}
    if it == "mcp_tool_call":
        server = _cap(str(item.get("server") or "mcp").replace("_", " "), 40)
        return {"verb": "Using " + server, "target": _cap(str(item.get("tool") or "").replace("_", " "), 40), "zone": "mcp"}
    if it == "web_search":
        return {"verb": "Searching the web", "target": "", "zone": "web"}
    if it == "collab_tool_call":
        return {"verb": "Coordinating helper agents", "target": "", "zone": "agent"}
    if it == "todo_list":
        return {"verb": "Updating the plan", "target": "", "zone": "plan"}
    if it in ("reasoning", "agent_reasoning"):
        return {"verb": "Thinking", "target": "", "zone": "think"}
    return None


def _doing(action):
    if not action:
        return ""
    v, t = action.get("verb") or "", action.get("target") or ""
    return f"{v} {t}".strip() if t else v


def _dur(secs):
    s = max(0, int(round(secs)))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def _looks_async(content):
    try:
        if isinstance(content, list):
            content = " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        return "async agent launched" in str(content or "").lower()
    except Exception:
        return False


def _split_pane(pane_key, project_path):
    key = str(pane_key or "")
    pane = key.split("::", 1)[1] if "::" in key else ""
    proj = str(project_path or "") or (key.split("::", 1)[0] if "::" in key else "")
    return pane, (_base(proj) if proj else "")


# ----------------------------------------------------------------- tracker
class CrewActivity:
    TURN_KEEP = 30           # finished turns kept for the deck's history
    AGENT_KEEP = 60          # finished subagents kept
    EVENT_KEEP = 120         # event ring size
    RETAIN_SECS = 30 * 60    # finished items stay visible this long
    STALE_SECS = 3 * 3600    # an active turn silent this long is flagged stale
    REPLY_WINDOW = 4.0       # seconds after the last token that count as "replying"

    def __init__(self, clock=time.time, log=None):
        self._clock = clock
        self._log = log
        self._lock = threading.RLock()
        self._ids = itertools.count(1)
        self._ev_seq = 0
        self._turns = {}                     # turn id -> turn (active)
        self._agents = {}                    # tool_use_id (or synthetic key) -> agent (active)
        self._task_index = {}                # CLI task_id -> agent key
        self._orders = {}                    # order id -> running standing order
        self._done_turns = collections.deque(maxlen=self.TURN_KEEP)
        self._done_agents = collections.deque(maxlen=self.AGENT_KEEP)
        self._events = collections.deque(maxlen=self.EVENT_KEEP)
        self._local = threading.local()
        self.started_at = clock()
        self.errors = 0

    # -------------------------------------------------------- internals
    def _fail(self, where, exc):
        self.errors += 1
        if self._log is not None and self.errors <= 20:
            try:
                self._log.warning(f"[crew] {where} ignored an error: {exc!r}")
            except Exception:
                pass

    def _event(self, kind, text, turn=None, agent=None, role=None, type_=None, owner=""):
        self._ev_seq += 1
        self._events.append({
            "seq": self._ev_seq, "at": self._clock(), "kind": kind, "text": _cap(text, 140),
            "turn": turn, "agent": agent, "role": role, "type": type_, "owner": owner,
        })

    def _prune(self, now):
        """Safety valve. Every hook ends its turn in a finally block, so this
        only fires if a runner thread hung for half a day or a future caller
        forgets end_turn. Keeps memory bounded either way."""
        dead = [t for t in self._turns.values() if now - t["last_event_at"] > 4 * self.STALE_SECS]
        if len(self._turns) - len(dead) > 200:
            dead += sorted((t for t in self._turns.values() if t not in dead),
                           key=lambda t: t["last_event_at"])[: len(self._turns) - len(dead) - 200]
        for t in dead:
            self._turns.pop(t["id"], None)
            t["ended_at"], t["ok"] = now, False
            t["inflight"].clear()
            for a in [a for a in self._agents.values() if a["turn"] == t["id"]]:
                self._agent_close(a, "ended", now)
            self._done_turns.append(t)
        if len(self._agents) > 300:
            for a in sorted(self._agents.values(), key=lambda a: a["last_event_at"])[: len(self._agents) - 300]:
                self._agent_close(a, "ended", now)

    def _stack(self):
        st = getattr(self._local, "stack", None)
        if st is None:
            st = self._local.stack = []
        return st

    def current(self):
        """Turn bound to the calling thread, or None."""
        try:
            st = self._stack()
            return st[-1] if st else None
        except Exception:
            return None

    # ------------------------------------------------------------ turns
    def begin_turn(self, source="chat", pane="", project="", provider="", officer="", label="", owner=""):
        """Open a turn and bind it to the calling thread. Returns its id."""
        try:
            now = self._clock()
            order = getattr(self._local, "order", None)
            if order and source in ("sync", "chat"):
                source = "order"
                owner = order.get("owner", owner)
            off = str(officer or "").strip().lower()
            if off == "computer":
                off = "data"
            pane_id, proj = _split_pane(pane, project)
            tid = f"t{next(self._ids)}"
            turn = {
                "id": tid, "owner": str(owner), "source": str(source or "chat"), "pane": _cap(pane_id, 60), "project": proj,
                "provider": _cap(provider, 40), "officer": _cap(off, 40), "role": role_for(off) if off else None,
                "label": _cap(label or (order or {}).get("name") or "", 80),
                "order_id": (order or {}).get("id", ""),
                "model": "", "started_at": now, "last_event_at": now, "ended_at": None, "ok": None,
                "inflight": {}, "last_action": None, "tools": 0, "agents": [], "replying_at": 0.0,
                "raw_feed": False,
            }
            with self._lock:
                self._prune(now)
                self._turns[tid] = turn
                where = f" in {proj}" if proj else ""
                via = f" ({turn['provider']})" if turn["provider"] else ""
                if turn["source"] == "order":
                    text = f"started standing order: {turn['label']}"
                elif turn["source"] == "voice":
                    text = f"answered a voice call{via}"
                else:
                    text = f"opened a channel{where}{via}"
                self._event("turn_start", text, turn=tid, role=turn["role"] or "core", owner=turn["owner"])
            self._stack().append(tid)
            return tid
        except Exception as e:
            self._fail("begin_turn", e)
            return None

    def end_turn(self, turn_id, ok=True):
        try:
            st = self._stack()
            if turn_id in st:
                st.remove(turn_id)
            if not turn_id:
                return
            now = self._clock()
            with self._lock:
                t = self._turns.pop(turn_id, None)
                if t is None:
                    return
                t["ended_at"] = now
                t["ok"] = bool(ok) and not t.get("failed", False)
                t["inflight"].clear()
                for key, a in list(self._agents.items()):
                    if a["turn"] == turn_id:
                        # The CLI process is gone, so anything it ran is gone too.
                        self._agent_close(a, "ended", now)
                self._done_turns.append(t)
                took = _dur(now - t["started_at"])
                if t["source"] == "order":
                    text = f"finished standing order: {t['label']} ({took})"
                else:
                    where = f" in {t['project']}" if t["project"] else ""
                    text = f"closed the channel{where} after {took}" + ("" if t["ok"] else ", with an error")
                self._event("turn_end", text, turn=turn_id, role=t["role"] or "core", owner=t["owner"])
        except Exception as e:
            self._fail("end_turn", e)

    # ------------------------------------------------------ order bracket
    def order_start(self, order, owner=""):
        try:
            oid = str((order or {}).get("id") or "")
            info = {"id": oid, "owner": str(owner), "name": _cap((order or {}).get("name"), 80),
                    "action": _cap((order or {}).get("action"), 40), "started_at": self._clock()}
            self._local.order = info
            with self._lock:
                self._orders[(str(owner), oid)] = info
        except Exception as e:
            self._fail("order_start", e)

    def order_end(self, order):
        try:
            oid = str((order or {}).get("id") or "")
            cur = getattr(self._local, "order", None)
            if cur and cur.get("id") == oid:
                self._local.order = None
            with self._lock:
                self._orders.pop(((cur or {}).get("owner", ""), oid), None)
        except Exception as e:
            self._fail("order_end", e)

    # ---------------------------------------------------------- agents
    def _agent_open(self, t, key, agent_type, description, background=None, parent=None, now=None):
        now = now if now is not None else self._clock()
        key = str(key or f"anon:{next(self._ids)}")
        a = self._agents.get(key)
        if a is not None:
            return a
        atype = _cap(agent_type or "general-purpose", 60)
        a = {
            "key": key, "id": f"a{next(self._ids)}", "turn": t["id"], "owner": t["owner"], "type": atype, "role": role_for(atype),
            "description": _cap(description, 90), "background": (bool(background) if background is not None else None),
            "status": "running", "started_at": now, "last_event_at": now, "ended_at": None,
            "inflight": {}, "last_action": None, "tools": 0, "summary": "", "depth": 0,
            "task_id": "", "parent": parent["id"] if parent else None,
            "pane": t["pane"], "project": t["project"],
        }
        if parent:
            a["depth"] = int(parent.get("depth") or 0) + 1
        self._agents[key] = a
        t["agents"].append(a["id"])
        self._event("agent_start", f"started: {a['description'] or atype}", turn=t["id"], agent=a["id"],
                    role=a["role"], type_=atype, owner=a["owner"])
        return a

    def _agent_close(self, a, status, now):
        if a.get("ended_at"):
            return
        a["status"] = status
        a["ended_at"] = now
        a["inflight"].clear()
        self._agents.pop(a["key"], None)
        if a.get("task_id"):
            self._task_index.pop(a["task_id"], None)
        self._done_agents.append(a)
        verb = {"completed": "finished", "failed": "failed", "stopped": "was stopped",
                "ended": "stood down"}.get(status, status)
        self._event("agent_end", f"{verb}: {a['description'] or a['type']} ({_dur(now - a['started_at'])})",
                    turn=a["turn"], agent=a["id"], role=a["role"], type_=a["type"], owner=a["owner"])

    def _find_agent(self, ev):
        tuid = ev.get("tool_use_id")
        if tuid and tuid in self._agents:
            return self._agents[tuid]
        tid = ev.get("task_id")
        if tid and str(tid) in self._task_index:
            return self._agents.get(self._task_index[str(tid)])
        return None

    def _owner(self, t, parent):
        """Who a (possibly nested) event belongs to: a subagent or the turn."""
        if parent:
            a = self._agents.get(parent)
            if a is not None:
                return a
        return t

    # Models often run several tools at once, so each turn and agent keeps a
    # small map of in-flight calls (tool id -> action). What it is "doing" is
    # the newest in-flight call that is real work: a call that summons a
    # subagent only waits on that agent, which is tracked on its own.
    INFLIGHT_MAX = 16

    def _set_action(self, who, name, inp, tool_id, now, count=True):
        act = describe_tool(name, inp)
        act["tool"] = _cap(name, 60)
        act["id"] = tool_id or None
        act["since"] = now
        inflight = who["inflight"]
        key = tool_id or "_"
        inflight.pop(key, None)
        inflight[key] = act
        while len(inflight) > self.INFLIGHT_MAX:
            inflight.pop(next(iter(inflight)))
        if act["zone"] != "agent":
            who["last_action"] = act
        if count:
            who["tools"] = int(who.get("tools") or 0) + 1
        who["last_event_at"] = now
        return act

    def _clear_action(self, who, tool_id, now):
        if tool_id:
            who["inflight"].pop(tool_id, None)
            who["inflight"].pop("_", None)      # coarse progress hints expire with any result
        else:
            who["inflight"].clear()
        who["last_event_at"] = now

    @staticmethod
    def _current(who):
        """Newest in-flight call that is real work, or None."""
        for act in reversed(list(who["inflight"].values())):
            if act.get("zone") != "agent":
                return act
        return None

    # ------------------------------------------------------ raw feeds
    def claude_event(self, ev, turn_id=None):
        """One parsed line of `claude --output-format stream-json --verbose`."""
        try:
            if not isinstance(ev, dict):
                return
            tid = turn_id or self.current()
            if not tid:
                return
            now = self._clock()
            with self._lock:
                t = self._turns.get(tid)
                if t is None:
                    return
                t["raw_feed"] = True
                t["last_event_at"] = now
                et = ev.get("type")
                parent = ev.get("parent_tool_use_id")
                if et == "assistant":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_use":
                            who = self._owner(t, parent)
                            name, inp = b.get("name"), b.get("input")
                            self._set_action(who, name, inp, b.get("id"), now)
                            if name in ("Agent", "Task") and isinstance(inp, dict):
                                self._agent_open(t, b.get("id"), inp.get("subagent_type"), inp.get("description"),
                                                 background=inp.get("run_in_background"),
                                                 parent=who if who is not t else None, now=now)
                        elif b.get("type") == "text" and not parent:
                            t["replying_at"] = now
                elif et == "user":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        tuid = b.get("tool_use_id")
                        self._clear_action(self._owner(t, parent), tuid, now)
                        a = self._agents.get(tuid)
                        if a is not None and a["status"] == "running":
                            if a.get("background") is True or _looks_async(b.get("content")):
                                a["background"] = True      # keeps working until task_notification
                            else:
                                self._agent_close(a, "failed" if b.get("is_error") else "completed", now)
                elif et == "tool_progress":
                    # Heartbeat for a call already in flight; only fills in a
                    # step if the call itself was never seen.
                    who = self._owner(t, parent)
                    who["last_event_at"] = now
                    tuid = ev.get("tool_use_id")
                    if ev.get("tool_name") and tuid not in who["inflight"] and not self._current(who):
                        self._set_action(who, ev.get("tool_name"), None, tuid, now, count=False)
                elif et == "system":
                    st = ev.get("subtype")
                    if st == "init":
                        t["model"] = _cap(ev.get("model"), 40)
                    elif st == "task_started":
                        self._task_started(t, ev, now)
                    elif st == "task_progress":
                        self._task_progress(ev, now)
                    elif st == "task_notification":
                        self._task_notification(ev, now)
                elif et == "result" and ev.get("is_error"):
                    t["failed"] = True
        except Exception as e:
            self._fail("claude_event", e)

    def _task_started(self, t, ev, now):
        stype = ev.get("subagent_type")
        ttype = str(ev.get("task_type") or "")
        if not stype and ttype not in _AGENT_TASK_TYPES:
            return                          # a background shell, not an officer
        a = self._find_agent(ev)
        if a is None:
            parent = self._agents.get(ev.get("parent_tool_use_id") or "")
            a = self._agent_open(t, ev.get("tool_use_id") or f"task:{ev.get('task_id')}", stype,
                                 ev.get("description"), background=ev.get("is_backgrounded"),
                                 parent=parent, now=now)
        else:
            if stype:
                a["type"] = _cap(stype, 60)
                a["role"] = role_for(stype)
            if ev.get("description"):
                a["description"] = _cap(ev.get("description"), 90)
            if ev.get("is_backgrounded") is not None:
                a["background"] = bool(ev.get("is_backgrounded"))
        if ev.get("task_id"):
            a["task_id"] = str(ev.get("task_id"))
            self._task_index[a["task_id"]] = a["key"]
        if isinstance(ev.get("spawn_depth"), int):
            a["depth"] = ev["spawn_depth"]
        a["last_event_at"] = now

    def _task_progress(self, ev, now):
        a = self._find_agent(ev)
        if a is None:
            return
        a["last_event_at"] = now
        last = ev.get("last_tool_name")
        cur = self._current(a)
        if last and not (cur and cur.get("tool") == last):
            # Coarser than a nested tool call (no file name), so it only wins
            # when the agent has moved on to a different tool.
            self._set_action(a, last, None, None, now, count=False)
        usage = ev.get("usage") or {}
        if isinstance(usage.get("tool_uses"), int):
            a["tools"] = max(int(a.get("tools") or 0), usage["tool_uses"])
        if ev.get("summary"):
            a["summary"] = _cap(ev.get("summary"), 120)
        if ev.get("description"):
            a["description"] = _cap(ev.get("description"), 90)

    def _task_notification(self, ev, now):
        a = self._find_agent(ev)
        if a is None:
            return
        if ev.get("summary"):
            a["summary"] = _cap(ev.get("summary"), 120)
        status = _TERMINAL.get(str(ev.get("status") or "").lower())
        if status:
            self._agent_close(a, status, now)
        else:
            a["last_event_at"] = now

    def codex_event(self, ev, turn_id=None):
        """One parsed line of `codex exec --json`."""
        try:
            if not isinstance(ev, dict):
                return
            tid = turn_id or self.current()
            if not tid:
                return
            now = self._clock()
            with self._lock:
                t = self._turns.get(tid)
                if t is None:
                    return
                t["raw_feed"] = True
                t["last_event_at"] = now
                et = ev.get("type")
                item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
                if et in ("turn.failed", "error"):
                    t["failed"] = True
                itype = str(item.get("type") or item.get("item_type") or "")
                iid = item.get("id") or "_"
                if itype == "collab_tool_call" and et in ("item.updated", "item.completed"):
                    self._codex_helpers(t, item, now)
                if et in ("item.started", "item.updated"):
                    act = _codex_action(item)
                    if act and iid not in t["inflight"]:
                        act.update({"tool": itype, "id": item.get("id"), "since": now})
                        t["inflight"][iid] = act
                        while len(t["inflight"]) > self.INFLIGHT_MAX:
                            t["inflight"].pop(next(iter(t["inflight"])))
                        if act["zone"] != "think":
                            t["last_action"] = act
                        if itype not in ("reasoning", "agent_reasoning", "todo_list"):
                            t["tools"] += 1
                elif et == "item.completed":
                    if iid in t["inflight"]:
                        t["inflight"].pop(iid, None)
                    elif itype in ("command_execution", "file_change", "mcp_tool_call", "web_search",
                                   "function_call", "tool_call", "shell_command"):
                        # Completed without a started event (fast item): still count it.
                        done = _codex_action(item) or describe_tool(item.get("name") or itype)
                        done.update({"tool": itype, "id": item.get("id"), "since": now})
                        t["last_action"] = done
                        t["tools"] += 1
                    if itype == "agent_message":
                        t["replying_at"] = now
        except Exception as e:
            self._fail("codex_event", e)

    def _codex_helpers(self, t, item, now):
        """Codex exposes helper lifecycle but no officer identity or nested tools.

        Keep them generic. Never derive an identity from private prompt/message
        fields. Keys include the owning turn because thread ids can be resumed.
        """
        states = item.get("agents_states") or {}
        receivers = item.get("receiver_thread_ids") or []
        if not isinstance(states, dict) or not isinstance(receivers, list):
            return
        for thread_id in set(receivers) | set(states):
            key = f"codex:{t['id']}:{thread_id}"
            state = states.get(thread_id) or {}
            status = state.get("status") if isinstance(state, dict) else None
            agent = self._agents.get(key)
            if status in ("pending_init", "running") or (item.get("tool") == "spawn_agent" and not status and item.get("status") == "completed"):
                agent = agent or self._agent_open(t, key, "codex-helper", "Codex helper", background=True, now=now)
                agent["last_event_at"] = now
            elif agent and status in ("completed", "errored", "shutdown", "not_found", "interrupted"):
                self._agent_close(agent, "completed" if status == "completed" else "failed" if status == "errored" else "stopped", now)

    def tap(self, sse, turn_id=None):
        """Wrap a send_sse callback so this tracker sees what the runner emits.
        Calls pass straight through; attribute lookups go to the original."""
        tracker = self
        tid = turn_id or self.current()

        class _Tap:
            def __call__(self_, event_type, text):
                try:
                    tracker._observe_sse(tid, event_type, text)
                except Exception as e:
                    tracker._fail("tap", e)
                return sse(event_type, text)

            def __getattr__(self_, name):
                return getattr(sse, name)

        return _Tap()

    def _observe_sse(self, tid, event_type, text):
        if not tid or event_type not in ("token", "tool", "error"):
            return
        now = self._clock()
        with self._lock:
            t = self._turns.get(tid)
            if t is None:
                return
            if event_type == "error":
                t["failed"] = True
                return
            if event_type == "token":
                t["replying_at"] = now
                t["last_event_at"] = now
                return
            if t.get("raw_feed") or not isinstance(text, dict):
                return                  # Claude and Codex turns are read from their raw stream
            if text.get("phase") == "call":
                self._set_action(t, text.get("name"), {"path": text.get("path") or ""}, text.get("id"), now)
            elif text.get("phase") == "result":
                self._clear_action(t, text.get("id"), now)

    # ---------------------------------------------------------- snapshot
    def _turn_view(self, t, now, running_agents):
        active = t["ended_at"] is None
        if active:
            status = "stale" if now - t["last_event_at"] > self.STALE_SECS else "running"
        else:
            status = "done" if t["ok"] else "failed"
        cur = self._current(t)
        if cur:
            doing = _doing(cur)
        elif active and now - (t["replying_at"] or 0) < self.REPLY_WINDOW:
            doing = "Writing the reply"
        elif active and running_agents:
            doing = f"Waiting on {running_agents} helper{'s' if running_agents != 1 else ''}"
        elif active:
            doing = "Thinking"
        else:
            doing = ""
        v = {k: t[k] for k in ("id", "source", "pane", "project", "provider", "officer", "role", "label",
                               "order_id", "model", "started_at", "last_event_at", "ended_at", "ok", "tools")}
        v.update({"status": status, "doing": doing, "agents": list(t["agents"]),
                  "action": dict(cur) if cur else None, "parallel": len(t["inflight"]),
                  "last_action": dict(t["last_action"]) if t["last_action"] else None,
                  "exact": bool(t["raw_feed"]) or bool(t["tools"])})
        return v

    def _agent_view(self, a, now):
        v = {k: a[k] for k in ("id", "turn", "type", "role", "description", "background", "status",
                               "started_at", "last_event_at", "ended_at", "tools", "summary", "depth",
                               "parent", "pane", "project")}
        cur = self._current(a)
        if cur:
            doing = _doing(cur)
        elif a["ended_at"] is None:
            children = sum(child["parent"] == a["id"] for child in self._agents.values())
            doing = f"Waiting on {children} helper{'s' if children != 1 else ''}" if children else "Working"
        else:
            doing = ""
        v.update({"doing": doing,
                  "action": dict(cur) if cur else None,
                  "last_action": dict(a["last_action"]) if a["last_action"] else None})
        return v

    @staticmethod
    def _rollup(turns, agents):
        """role -> what that officer is doing right now (most recent wins)."""
        out = {}

        def put(role, entry):
            cur = out.get(role)
            if cur is None:
                entry["count"] = 1
                out[role] = entry
                return
            n = cur["count"] + 1
            if entry["last_event_at"] >= cur["last_event_at"]:
                out[role] = entry
            out[role]["count"] = n

        for a in agents:
            if a["role"] and a["role"] != "core":
                put(a["role"], {"kind": "agent", "agent": a["id"], "turn": a["turn"], "type": a["type"],
                                "what": a["description"] or a["type"], "doing": a["doing"],
                                "since": a["started_at"], "last_event_at": a["last_event_at"],
                                "project": a["project"], "pane": a["pane"]})
        for t in turns:
            if t["role"] and t["role"] != "core":
                where = f" in {t['project']}" if t["project"] else ""
                what = "On a voice call with you" if t["source"] == "voice" else f"Talking with you{where}"
                put(t["role"], {"kind": t["source"], "turn": t["id"], "what": what, "doing": t["doing"],
                                "since": t["started_at"], "last_event_at": t["last_event_at"],
                                "project": t["project"], "pane": t["pane"]})
        core_turns = [t for t in turns if t["role"] in (None, "core")]
        core_agents = [a for a in agents if a["role"] == "core"]
        helpers = [a for a in agents if not a["role"]]
        if core_turns or core_agents or helpers:
            latest = max(core_turns + core_agents, key=lambda x: x["last_event_at"], default=None)
            out["core"] = {"kind": "core", "turns": len(core_turns), "agents": len(core_agents),
                           "helpers": len(helpers), "doing": latest["doing"] if latest else "",
                           "since": min((x["started_at"] for x in core_turns + core_agents + helpers), default=0),
                           "last_event_at": latest["last_event_at"] if latest else 0,
                           "count": len(core_turns) + len(core_agents)}
        return out

    def snapshot(self, owner=None):
        """JSON-ready picture of the crew. Never raises."""
        try:
            now = self._clock()
            with self._lock:
                visible = lambda x: owner is None or x.get("owner", "") == str(owner)
                running_by_turn = collections.Counter(a["turn"] for a in self._agents.values())
                turns = [self._turn_view(t, now, running_by_turn.get(t["id"], 0)) for t in self._turns.values() if visible(t)]
                agents = [self._agent_view(a, now) for a in self._agents.values() if visible(a)]
                recent_turns = [self._turn_view(t, now, 0) for t in reversed(self._done_turns)
                                if visible(t) and now - t["ended_at"] <= self.RETAIN_SECS][:12]
                recent_agents = [self._agent_view(a, now) for a in reversed(self._done_agents)
                                 if visible(a) and now - a["ended_at"] <= self.RETAIN_SECS][:24]
                events = [{k: v for k, v in e.items() if k != "owner"} for e in reversed(self._events) if visible(e)][:80]
                orders = [{k: v for k, v in o.items() if k != "owner"} for o in self._orders.values() if visible(o)]
                errors = self.errors
            turns.sort(key=lambda x: x["started_at"], reverse=True)
            agents.sort(key=lambda x: x["started_at"], reverse=True)
            return {
                "version": VERSION, "available": True, "now": now, "since": self.started_at, "scope": owner or "",
                "turns": turns, "agents": agents, "orders": orders,
                "recent_turns": recent_turns, "recent_agents": recent_agents,
                "events": events, "officers": self._rollup(turns, agents), "errors": errors,
            }
        except Exception as e:
            self._fail("snapshot", e)
            return {"version": VERSION, "available": False, "now": time.time(), "error": "snapshot failed"}


TRACKER = CrewActivity()


def selftest():
    """Replay a short synthetic Claude Code stream through a fresh tracker and
    check the result. Used as a pre-flight before the bridge is restarted.
    Returns True or raises AssertionError."""
    clock = [1000.0]
    tr = CrewActivity(clock=lambda: clock[0])
    tid = tr.begin_turn("chat", pane="c:\\x\\data::pane-ws1", project="C:\\x\\DATA",
                        provider="claude-cli", officer="data")
    tr.claude_event({"type": "system", "subtype": "init", "model": "opus"})
    tr.claude_event({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "tu1", "name": "Agent",
         "input": {"subagent_type": "vector", "description": "Review the diff", "prompt": "SECRET-PROMPT"}}]}})
    tr.claude_event({"type": "system", "subtype": "task_started", "task_id": "k1", "tool_use_id": "tu1",
                     "subagent_type": "vector", "description": "Review the diff", "is_backgrounded": False,
                     "prompt": "SECRET-PROMPT", "task_type": "local_agent"})
    clock[0] += 5
    tr.claude_event({"type": "assistant", "parent_tool_use_id": "tu1", "message": {"content": [
        {"type": "tool_use", "id": "tu2", "name": "Read", "input": {"file_path": "C:\\x\\DATA\\app.js"}}]}})
    snap = tr.snapshot()
    assert snap["officers"]["review"]["doing"] == "Reading app.js", snap["officers"]
    assert "SECRET-PROMPT" not in repr(snap)
    tr.claude_event({"type": "user", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu1", "content": "done"}]}})
    tr.end_turn(tid, ok=True)
    snap = tr.snapshot()
    assert not snap["turns"] and not snap["agents"], snap
    assert snap["recent_agents"][0]["status"] == "completed", snap["recent_agents"]
    assert tr.errors == 0, tr.errors
    return True
