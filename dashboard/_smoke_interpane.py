"""Live smoke test for inter-pane messaging (send_to_pane).

Drives the REAL seam: _marker_filter_sse (the token-stream marker parser used by
every CLI provider) -> _handle_send_to_pane_marker -> _push_ui_event -> the
/ui_events drain the frontend polls. No second server, fully deterministic.

Run:  python _smoke_interpane.py
Exit 0 = all pass.
"""
import sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bridge_server as B

PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail and not cond else ""))

def feed(chunks, sender_path, sender_pane, client_id):
    """Bind the sender pane, run `chunks` through a fresh marker filter as if
    streamed from the model, and return (visible_text, drained_events)."""
    B._bind_history(sender_path, sender_pane)
    visible = []
    filt = B._marker_filter_sse(lambda et, tx: visible.append(tx) if et == 'token' else None)
    # register the drain client BEFORE the event fires (fanout + 12s backlog)
    B._drain_ui_events(client_id)
    for c in chunks:
        filt('token', c)
    filt.finalize()
    evts = B._drain_ui_events(client_id)
    return "".join(visible), evts

def pane_msgs(evts):
    return [e for e in evts if e.get("type") == "pane_message"]

print("── Inter-pane messaging smoke test ─────────────────────────")

# 1. Whole marker in a single chunk
TOK = "SMOKE-A-7X"
marker = '<<send_to_pane>>{"to":"window 2","message":"%s"}<</send_to_pane>>' % TOK
vis, evts = feed(["Sure, relaying now. ", marker, " done."],
                 r"C:\Users\mixma\Documents\DATA", "smoke-A", "cid-1")
pm = pane_msgs(evts)
check("single-chunk: exactly one pane_message emitted", len(pm) == 1, f"got {len(pm)}")
if pm:
    p = pm[0]["payload"]
    check("single-chunk: to routed", p.get("to") == "window 2", repr(p.get("to")))
    check("single-chunk: message intact", p.get("message") == TOK, repr(p.get("message")))
    check("single-chunk: from_key = sender path::pane",
          p.get("from_key") == r"c:\users\mixma\documents\data::smoke-a", repr(p.get("from_key")))
check("single-chunk: marker stripped from visible output", "send_to_pane" not in vis, repr(vis))
check("single-chunk: surrounding prose preserved", "Sure, relaying now." in vis and "done." in vis, repr(vis))

# 2. Marker split across many stream chunks (realistic token streaming)
TOK2 = "SMOKE-B-42"
full = '<<send_to_pane>>{"to":"SelkirkSprinklers","message":"%s"}<</send_to_pane>>' % TOK2
chunks = [full[i:i+7] for i in range(0, len(full), 7)]  # 7-char shards
vis2, evts2 = feed(chunks, r"C:\Users\mixma\Documents\DATA", "smoke-B", "cid-2")
pm2 = pane_msgs(evts2)
check("split-stream: exactly one pane_message (no dup, no drop)", len(pm2) == 1, f"got {len(pm2)}")
if pm2:
    check("split-stream: message reassembled correctly", pm2[0]["payload"].get("message") == TOK2)
    check("split-stream: to reassembled correctly", pm2[0]["payload"].get("to") == "SelkirkSprinklers")
check("split-stream: nothing leaked to visible output", vis2.strip() == "", repr(vis2))

# 3. Distinct sender key tags correctly (routing failures go back to right pane)
vis3, evts3 = feed([marker], r"C:\Users\mixma\Documents\DATA", "OTHER-Pane", "cid-3")
pm3 = pane_msgs(evts3)
check("sender-tag: from_key follows the binding pane",
      bool(pm3) and pm3[0]["payload"].get("from_key") == r"c:\users\mixma\documents\data::other-pane",
      repr(pm3[0]["payload"].get("from_key")) if pm3 else "no event")

# 4. Malformed markers must NOT emit an event
bad_missing_msg = '<<send_to_pane>>{"to":"window 2"}<</send_to_pane>>'
_, e4a = feed([bad_missing_msg], r"C:\Users\mixma\Documents\DATA", "smoke-C", "cid-4a")
check("malformed: missing 'message' emits nothing", len(pane_msgs(e4a)) == 0)
bad_missing_to = '<<send_to_pane>>{"message":"orphan"}<</send_to_pane>>'
_, e4b = feed([bad_missing_to], r"C:\Users\mixma\Documents\DATA", "smoke-C", "cid-4b")
check("malformed: missing 'to' emits nothing", len(pane_msgs(e4b)) == 0)
bad_json = '<<send_to_pane>>{not valid json}<</send_to_pane>>'
_, e4c = feed([bad_json], r"C:\Users\mixma\Documents\DATA", "smoke-C", "cid-4c")
check("malformed: invalid JSON emits nothing (and does not crash)", len(pane_msgs(e4c)) == 0)

# 5. Alias fields ('pane'/'target', 'text') accepted per handler contract
alias = '<<send_to_pane>>{"target":"main","text":"via-aliases"}<</send_to_pane>>'
_, e5 = feed([alias], r"C:\Users\mixma\Documents\DATA", "smoke-D", "cid-5")
pm5 = pane_msgs(e5)
check("aliases: 'target'+'text' accepted", len(pm5) == 1 and pm5[0]["payload"].get("to") == "main"
      and pm5[0]["payload"].get("message") == "via-aliases")

print("────────────────────────────────────────────────────────────")
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("ALL GREEN — inter-pane relay verified end to end (backend).")
sys.exit(0)
