"""Auto model router: pure routing logic.

Picks the cheapest model the user already pays for (or runs locally) that is
strong enough for one chat message, and explains the choice in plain words.
See docs/SPEC_auto_router.md, Sections 6 and 9.2.

This module is deliberately pure. It imports nothing from the bridge: the
caller passes in the message context, the user's settings, the previous turn
of the pane, an optional triage function, and the list of candidate models.
That keeps every rule unit-testable without a CLI and lets the private build
copy the file unchanged.

Only rule ids are ever returned for logging, never the words that matched,
so no prompt text leaks into the usage ledger.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional

ROUTER_VERSION = "1.0.0"
BUILTIN_CATALOG_VERSION = "builtin-2026-11-23"

# ── Tiers ────────────────────────────────────────────────────────────────────
LIGHT, MID, TOP = "light", "mid", "top"
TIERS = (LIGHT, MID, TOP)
TIER_RANK = {LIGHT: 0, MID: 1, TOP: 2}
TIER_NAMES = {LIGHT: "Fast", MID: "Standard", TOP: "Strongest"}
# Settings use the plain names; map them to tier ids.
SETTING_TIERS = {"fast": LIGHT, "standard": MID, "strongest": TOP}


def tier_max(*tiers: Optional[str]) -> str:
    vals = [t for t in tiers if t in TIER_RANK]
    return max(vals, key=TIER_RANK.__getitem__) if vals else TOP


def tier_up(tier: str) -> str:
    return TIERS[min(TIER_RANK.get(tier, 2) + 1, 2)]


# ── Categories and the bias table (spec 6.2) ────────────────────────────────
BIASES = ("save", "balanced", "quality")

# category -> (save, balanced, quality)
CATEGORY_TIERS = {
    "chat":         (LIGHT, LIGHT, MID),
    "lookup":       (LIGHT, LIGHT, MID),
    "rewrite":      (LIGHT, LIGHT, MID),
    "writing":      (LIGHT, MID,   TOP),
    "research":     (MID,   MID,   TOP),
    "personal":     (MID,   MID,   TOP),
    "vision":       (MID,   MID,   TOP),
    "code_help":    (MID,   MID,   TOP),
    "planning":     (MID,   TOP,   TOP),
    "health_legal": (TOP,   TOP,   TOP),
    "code_change":  (TOP,   TOP,   TOP),
    "security":     (TOP,   TOP,   TOP),
    "money":        (TOP,   TOP,   TOP),
    "release":      (TOP,   TOP,   TOP),
    "action":       (TOP,   TOP,   TOP),
    "multi_part":   (TOP,   TOP,   TOP),
}
CATEGORIES = tuple(CATEGORY_TIERS)
LOCKED = frozenset({"code_change", "security", "money", "release", "action"})

# Reported-reason priority when several categories tie on the final tier.
REASON_PRIORITY = (
    "action", "security", "money", "release", "code_change", "health_legal",
    "multi_part", "planning", "vision", "code_help", "research", "personal",
    "writing", "rewrite", "lookup", "chat",
)

# Categories that never count toward "two categories means multi-part":
# they are context, not separate asks.
_MULTI_PART_IGNORE = frozenset({"chat", "lookup", "vision"})
# Pairs that naturally co-occur in one ask ("explain this error",
# "rewrite this email", "write a journal entry about how I feel").
_COMPATIBLE = (
    frozenset({"research", "code_help"}),
    frozenset({"rewrite", "writing"}),
    frozenset({"personal", "writing"}),
    frozenset({"research", "lookup"}),
)

# Rules whose hit forces the strongest tier no matter what the settings say.
# Crisis language must never be routed down, even if health topics are
# lowered in Advanced settings.
LOCKED_RULE_IDS = frozenset({"hl.crisis"})


# ── Rules (spec 6.3, lexicon as data) ───────────────────────────────────────
@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    weight: str          # "strong" or "weak"
    pattern: str


_I = r"(?:^|(?<=[\s,.;:!?(]))"          # start of an imperative clause
_ASK = r"(?:please\s+|can you\s+|could you\s+|would you\s+|go ahead and\s+|i want you to\s+|i need you to\s+|help me\s+|just\s+)?"

RULES: tuple = (
    # action: aimed at files, accounts, messages, or the system
    Rule("act.destroy", "action", "strong",
         r"\b(delete|erase|wipe|uninstall|unsubscribe|deactivate|format (?:the|my) (?:drive|disk))\b"),
    Rule("act.install", "action", "strong", r"\b(install|reinstall)\b"),
    Rule("act.rename_move", "action", "strong",
         r"\b(rename|move|copy|remove|clean up|organi[sz]e)\b.{0,40}\b(files?|folders?|director(?:y|ies)|downloads|desktop|documents|photos|drive|apps?|programs?|accounts?|emails?|inbox)\b"),
    Rule("act.send", "action", "strong", r"\bsend\b(?!\s+me\b)"),
    Rule("act.post", "action", "strong",
         r"(?:^|\b(?:please|can you|could you|go ahead and|then)\s+)(post|publish|upload|tweet|share)\b(?!s)\s+(?:it|this|that|these|them|to|on|my|the)\b"),
    Rule("act.reply", "action", "strong", r"^\s*" + _ASK + r"(reply|respond|forward) (?:to|this|that|it)\b"),
    Rule("act.schedule", "action", "strong",
         r"\b(schedule|book|cancel|reschedule)\b.{0,30}\b(meeting|appointment|call|event|reminder|flight|reservation|subscription|order|my)\b|\bset (?:a|an|my) (?:reminder|alarm|timer)\b"),
    Rule("act.run", "action", "strong",
         r"\b(run|execute|launch|start|stop|kill|restart|shut down|reboot)\b\s+(?:this|that|the|it|my)\b.{0,30}\b(command|script|program|app|process|server|service|computer|pc|code|file|job)\b|\brun this\b|\bexecute\b"),
    Rule("act.settings", "action", "strong",
         r"\b(change|update|turn (?:on|off)|enable|disable|reset)\b\s+(?:my|the)\s+(?:settings?|preferences|password|account|config\w*)"),
    Rule("act.files", "action", "strong",
         r"\b(create|make|save|write)\b\s+(?:a|an|the|this|it)?\s*(?:new\s+)?(file|folder|directory)\b|\bsave (?:this|it) (?:to|as|in)\b|\boverwrite\b"),
    Rule("act.transfer", "action", "strong", r"\b(transfer|withdraw|deposit)\b"),

    # security
    Rule("sec.password", "security", "strong",
         r"\b(passwords?|passcodes?|passphrases?|pin codes?|seed phrase|recovery phrase|private keys?|ssh keys?)\b"),
    Rule("sec.keys", "security", "strong",
         r"\b(api[ -]?keys?|secret keys?|(?:access|auth|api|bearer|session|refresh) tokens?|credentials?|\.env\b|secrets?)\b"),
    Rule("sec.auth", "security", "strong",
         r"\b(2fa|mfa|two[- ]factor|authenticat\w*|authori[sz]ation|oauth|permissions?|login (?:details|info|credentials)|(?:can'?t|cannot|unable to) (?:log|sign) ?in)\b"),
    Rule("sec.threat", "security", "strong",
         r"\b(hack(?:ed|er|ing)?|breach\w*|vulnerab\w*|exploit\w*|malware|virus|ransomware|phishing|scam\w*|suspicious (?:link|email|message|login)|encrypt\w*|decrypt\w*|firewall|cve-\d+|security)\b"),

    # money
    Rule("mon.trade", "money", "strong",
         r"\b(trad(?:e|es|ing)|buy(?:ing)?|sell(?:ing)?|crypto\w*|bitcoin|btc|eth|ethereum|solana|memecoins?|tokens? launch|stocks?(?! photo| image)|portfolio|invest\w*|forex|dividends?|options trading|futures)\b"),
    Rule("mon.bank", "money", "strong",
         r"\b(wallets?|bank\w*|payments?|pay(?:ing)?(?! attention)|paid|invoices?|refunds?|loans?|mortgage|credit cards?|debit|paypal|stripe|venmo|zelle|wire transfer|salary|budget\w*|price of|charge(?:d|s)? me)\b"),
    Rule("mon.amount", "money", "weak", r"[$€£]\s?\d"),

    # release and infrastructure
    Rule("rel.deploy", "release", "strong",
         r"\b(deploy\w*|go live|production|staging|servers?|cron\w*|dns|nameservers?|installers?|hosting|domain names?|ssl cert\w*|ci/cd|docker\w*|kubernetes|vercel|netlify|heroku|droplet|nginx)\b"),
    Rule("rel.release", "release", "strong",
         r"\b(release (?:a|the|v\d|version|build|notes)|ship (?:it|the build|a release|the release)|publish (?:the|my) (?:site|app|website|package|release|extension))\b"),

    # code change (locked)
    Rule("code.fix", "code_change", "strong",
         r"\b(fix|debug|refactor|implement|patch|optimi[sz]e|migrate|update|change|modify|add|remove|rewrite)\b.{0,30}\b(bugs?|code|functions?|methods?|class(?:es)?|scripts?|modules?|endpoints?|api|components?|tests?|features?|repo\w*|build|css|html|quer(?:y|ies)|regex|app)\b"),
    Rule("code.write", "code_change", "strong",
         r"\b(write|create|build|make|generate|code)\b.{0,30}\b(functions?|scripts?|programs?|class(?:es)?|apps?|websites?|web ?apps?|sites?|components?|endpoints?|apis?|bots?|extensions?|plugins?|test suites?|unit tests?)\b"),
    Rule("code.git", "code_change", "strong",
         r"\b(commit|pull request|merge (?:the|this|my) (?:branch|pr)|git push|push to (?:main|master|github)|rebase)\b"),

    # health and legal
    Rule("hl.crisis", "health_legal", "strong",
         r"\b(suicid\w*|self[- ]harm|kill myself|end my life|overdos\w*|want to die)\b"),
    Rule("hl.health", "health_legal", "strong",
         r"\b(symptoms?|medications?|medicines?|meds|dos(?:e|age)|prescri\w*|doctor|diagnos\w*|pregnan\w*|allerg\w*|rash|fever|injur\w*|blood pressure|diabet\w*|cancer|infection|supplements?|side effects?|chest pain|pain)\b"),
    Rule("hl.legal", "health_legal", "strong",
         r"\b(contracts?|lawsuits?|sue|suing|legal\w*|lawyers?|attorneys?|court|tax(?:es)?|irs|visas?|immigration|custody|divorce|lease|landlord|evict\w*|copyright\w*|trademark\w*|liabilit\w*|llc|nonprofit|501\(c\)|terms of service|gdpr)\b"),

    # planning
    Rule("plan.plan", "planning", "strong",
         r"\b(plan(?:s|ning)?|strateg\w*|roadmaps?|help me decide|prioriti[sz]\w*|milestones?|next steps|game plan|business model|goals?)\b"),
    Rule("plan.decide", "planning", "weak", r"\b(should i|which (?:one|option) should|decide|decision)\b"),

    # code help (no change requested)
    Rule("help.code_words", "code_help", "weak",
         r"\b(python|javascript|typescript|java|c\+\+|c#|rust|golang|sql|regex|syntax|stack trace|exception|compiler|error message|null pointer|undefined is not)\b"),

    # personal
    Rule("pers.feelings", "personal", "strong",
         r"\b(i feel|i'?m feeling|feelings?|anxious|anxiety|sad|lonely|stress(?:ed)?|overwhelmed|depress\w*|grie(?:f|ving)|journal\w*|relationship|my partner|boyfriend|girlfriend|husband|wife|break ?up|friendship|habits?|motivat\w*|meditat\w*|gratitude|self[- ]esteem|i'?m struggling|purpose in life|spiritual\w*)\b"),

    # research
    Rule("res.explain", "research", "strong",
         r"\b(explain|compare|comparison|pros and cons|difference between|versus|vs\.?|summari[sz]e|summary of|overview of|how does .{1,40} work|break down|analy[sz]e|research)\b"),
    Rule("res.why", "research", "weak", r"^\s*(why|how come)\b|\btell me about\b|\bwhat are the\b"),

    # writing
    Rule("write.draft", "writing", "strong",
         r"\b(write|draft|compose|create|make|give me|come up with|generate)\b.{0,40}\b(emails?|e-mails?|captions?|posts?|blogs?|stor(?:y|ies)|poems?|bios?|letters?|messages?|tweets?|threads?|descriptions?|newsletters?|speech(?:es)?|toasts?|scripts?|essays?|articles?|headlines?|taglines?|slogans?|titles?|cards?|announcements?|invitations?|replies|reply|responses?|outlines?|lyrics|song)\b"),

    # rewrite (needs pasted text for a strong hit; see _has_pasted_text)
    Rule("rw.verb", "rewrite", "weak",
         r"\b(fix (?:the |my )?(?:grammar|spelling|typos?|punctuation)|proofread|rephrase|reword|paraphrase|shorten|make (?:this|it) (?:shorter|longer|clearer|simpler|more \w+|less \w+|sound \w+)|simplify this|turn (?:this|it) into|translate|tidy (?:this|it) up|clean (?:this|it) up|polish)\b"),

    # lookup (strong cues; a bare short question is a weak structural hit)
    Rule("look.cue", "lookup", "strong",
         r"\b(what time|time zone|convert|conversion|define|definition of|meaning of|what does .{1,30} mean|how do you spell|synonyms? (?:of|for)|antonyms? (?:of|for)|how many|how far|how tall|how old|how long is|what year|when did|when was|when is|who is|who was|who wrote|capital of|population of|weather in|what is the date|what day is)\b"),
)

_COMPILED = tuple((r, re.compile(r.pattern, re.IGNORECASE)) for r in RULES)

# Greetings and thanks: "chat" when nothing else matched and the message is short.
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|yo|hiya|howdy|thanks|thank you|thx|ty|cheers|good (?:morning|afternoon|evening|night)|"
    r"how are you|how'?s it going|what'?s up|sup|bye|goodbye|see you|nice|cool|great|awesome|lol|haha|"
    r"you'?re (?:the best|awesome|great)|i appreciate it)\b[\s\w!.,'?]*$", re.IGNORECASE)

# Short approvals inherit the previous turn's tier and category (spec 6.3.7).
_APPROVAL = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|k|do it|go ahead|go for it|ship it|proceed|continue|sounds good|"
    r"approved|let'?s do it|let'?s go|please do|yes please|yes,? do it|all good|perfect,? (?:go|do it)|confirm(?:ed)?|"
    r"keep going|carry on|go on|resume|next|next step|do the next (?:one|step|part)|finish (?:it|up|the rest)|"
    r"continue (?:where (?:you|we) left off|from there|with (?:it|that|the rest))|keep at it|same again)"
    r"[\s!.,]*(please)?[\s!.,]*$", re.IGNORECASE)

# Follow-ups read the conversation, not just the words. When the last reply
# offered to do something, approving it means taking action, which only the
# strongest tier can do (the lower tiers run read-only).
_OFFER = re.compile(
    r"(want me to|shall i|should i (?:go ahead|start|proceed|build|run|commit|push|merge)|would you like me to|"
    r"do you want me to|i can (?:now )?(?:build|wire|add|fix|run|commit|push|merge|deploy|install|set up|ship|"
    r"start|make|write|update|create|apply|port)|ready to (?:build|ship|merge|deploy|commit|run|start|apply)|"
    r"say the word|say go|on your go|next step|next,? i(?:'ll| will)|<<ask_options>>|"
    r"(?:say|reply|answer) (?:yes|go|continue))", re.IGNORECASE)
# How much of the conversation a follow-up may read (characters).
CONTEXT_ASK_CHARS = 1500
CONTEXT_REPLY_CHARS = 1200
CONTEXT_OFFER_CHARS = 500
# A reply this short with no recognisable request is read as an answer to the
# previous turn ("the second one", "Postgres") and routed from the context.
SHORT_REPLY_CHARS = 60

# Explicit model requests inside the message (spec 6.3.5). Not stripped.
_REQ_TOP = re.compile(
    r"\b(use (?:opus|the strongest model|the best model|your best model|the smartest model)|think (?:hard|harder|carefully|deeply)|"
    r"be careful|double[- ]check)\b", re.IGNORECASE)
_REQ_MID = re.compile(r"\b(use (?:sonnet|the standard model))\b", re.IGNORECASE)
_REQ_LIGHT = re.compile(
    r"\b(use (?:haiku|the fast model|a fast model|the cheapest model)|quick answer|just briefly|briefly|tl;?dr)\b", re.IGNORECASE)

# Shape signals
_CODE_FENCE = re.compile(r"```")
_TRACE = re.compile(
    r"(Traceback \(most recent call last\)|^\s*at [\w$.<>]+\(|^\w*(?:Error|Exception):|\bline \d+, in\b|"
    r"npm ERR!|SyntaxError|TypeError|ReferenceError|NullPointerException)", re.MULTILINE)
_CODE_SHAPE = re.compile(
    r"(\bdef \w+\(|\bfunction \w*\(|=>|console\.log|\bimport \w+|\bfrom \w+ import\b|#include|\bpublic static\b|"
    r"\bSELECT\b.+\bFROM\b|</?\w+[^>]*>|\{\s*\"\w+\"\s*:|\w+\.(?:py|js|ts|tsx|jsx|java|cs|cpp|go|rs|rb|php|html|css|json|ya?ml|sh|ps1)\b)")
_EDIT_VERBS = re.compile(
    r"\b(fix|change|update|modify|refactor|rewrite|implement|add|remove|replace|patch|edit|make it|get it to|so that it)\b",
    re.IGNORECASE)
_QUESTION_START = re.compile(
    r"^\s*(what|who|when|where|which|how|is|are|does|do|did|can|could|will|would|was|were|define|convert)\b", re.IGNORECASE)
_CONNECTORS = re.compile(r"\b(and also|also,|additionally|after that|and then|then also|plus,)\b", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.MULTILINE)


def _has_pasted_text(msg: str) -> bool:
    """True when the message carries text to work on, not just an instruction."""
    if "\n" in msg.strip() and len(msg) > 60:
        return True
    if re.search(r"[\"“‘'].{20,}[\"”’']", msg, re.DOTALL):
        return True
    m = re.search(r":\s*(.+)$", msg, re.DOTALL)
    if m and len(m.group(1)) >= 30:
        return True
    return len(msg) > 400


def count_asks(msg: str) -> int:
    """Rough count of distinct requests in one message."""
    q = msg.count("?")
    list_asks = sum(1 for line in msg.splitlines()
                    if _LIST_ITEM.match(line) and line.rstrip().endswith("?"))
    asks = max(1, q) if q else 1
    asks = max(asks, list_asks)
    asks += len(_CONNECTORS.findall(msg))
    return asks


# ── Settings ────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "mode": "manual",
    "bias": "balanced",
    "category_floor": {},            # category -> "auto"|"fast"|"standard"|"strongest"
    "allowed": {},                   # provider id -> bool (absent means default)
    "model_overrides": {},           # provider id -> "fast"|"standard"|"strongest"|"off"
    "triage": True,
    "triage_backend": "auto",
    "escalation": True,
    "allow_step_down_on_limit": False,
    "sticky_minutes": 10,
    "show_model_names": False,
}


def normalize_settings(raw: Optional[dict]) -> dict:
    """Merge user settings over defaults, dropping anything malformed.
    Lenient on purpose: strict validation with error messages lives in the
    settings endpoint (Task A5); routing must never crash on a bad file."""
    s = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    if not isinstance(raw, dict):
        return s
    if raw.get("mode") in ("auto", "manual"):
        s["mode"] = raw["mode"]
    if raw.get("bias") in BIASES:
        s["bias"] = raw["bias"]
    cf = raw.get("category_floor")
    if isinstance(cf, dict):
        s["category_floor"] = {c: v for c, v in cf.items()
                               if c in CATEGORY_TIERS and v in ("auto", "fast", "standard", "strongest")}
    al = raw.get("allowed")
    if isinstance(al, dict):
        s["allowed"] = {str(k): bool(v) for k, v in al.items() if isinstance(v, bool)}
    mo = raw.get("model_overrides")
    if isinstance(mo, dict):
        s["model_overrides"] = {str(k): v for k, v in mo.items()
                                if v in ("fast", "standard", "strongest", "off")}
    for key in ("triage", "escalation", "allow_step_down_on_limit", "show_model_names"):
        if isinstance(raw.get(key), bool):
            s[key] = raw[key]
    if raw.get("triage_backend") in ("auto", "local", "claude"):
        s["triage_backend"] = raw["triage_backend"]
    sm = raw.get("sticky_minutes")
    if isinstance(sm, int) and not isinstance(sm, bool) and 0 <= sm <= 60:
        s["sticky_minutes"] = sm
    return s


AUTO_PROVIDER_ID = "auto"            # pseudo provider id the front end sends for Auto


def validate_settings(raw) -> tuple:
    """Strict check for the settings endpoint (Task A5). Returns
    (settings, errors). Unknown keys and bad values are errors, and a locked
    category can never be given a floor below Strongest."""
    errors = []
    if not isinstance(raw, dict):
        return normalize_settings(None), ["settings must be a JSON object"]
    allowed_keys = set(DEFAULT_SETTINGS)
    for k in raw:
        if k not in allowed_keys:
            errors.append(f"unknown setting '{k}'")
    if "mode" in raw and raw["mode"] not in ("auto", "manual"):
        errors.append("mode must be 'auto' or 'manual'")
    if "bias" in raw and raw["bias"] not in BIASES:
        errors.append(f"bias must be one of {', '.join(BIASES)}")
    cf = raw.get("category_floor", {})
    if not isinstance(cf, dict):
        errors.append("category_floor must be an object")
    else:
        for c, v in cf.items():
            if c not in CATEGORY_TIERS:
                errors.append(f"unknown category '{c}'")
            elif v not in ("auto", "fast", "standard", "strongest"):
                errors.append(f"category_floor.{c} must be auto, fast, standard or strongest")
            elif c in LOCKED and v not in ("auto", "strongest"):
                errors.append(f"'{c}' always uses the strongest model and cannot be lowered")
    for key in ("allowed", "model_overrides"):
        val = raw.get(key, {})
        if not isinstance(val, dict):
            errors.append(f"{key} must be an object")
            continue
        for pid, v in val.items():
            if not isinstance(pid, str) or not pid or len(pid) > 80:
                errors.append(f"{key} has a bad model id")
            elif key == "allowed" and not isinstance(v, bool):
                errors.append(f"allowed.{pid} must be true or false")
            elif key == "model_overrides" and v not in ("fast", "standard", "strongest", "off"):
                errors.append(f"model_overrides.{pid} must be fast, standard, strongest or off")
    for key in ("triage", "escalation", "allow_step_down_on_limit", "show_model_names"):
        if key in raw and not isinstance(raw[key], bool):
            errors.append(f"{key} must be true or false")
    if "triage_backend" in raw and raw["triage_backend"] not in ("auto", "local", "claude"):
        errors.append("triage_backend must be auto, local or claude")
    if "sticky_minutes" in raw:
        sm = raw["sticky_minutes"]
        if not isinstance(sm, int) or isinstance(sm, bool) or not 0 <= sm <= 60:
            errors.append("sticky_minutes must be a whole number from 0 to 60")
    return normalize_settings(raw), errors


# ── Built-in tier map (replaced by router_catalog.json in Task A2) ──────────
# Rates provider ids by tier family, so an alias that moves to a newer model
# keeps its tier. Used to describe the tier of a manual turn in the ledger and
# as the default rating until the catalog loader lands.
BUILTIN_TIERS = {
    "claude-cli": TOP, "claude-cli-opus-5": TOP, "claude-cli-opus-48": TOP,
    "claude-cli-sonnet": MID, "claude-cli-haiku": LIGHT,
    "codex": TOP, "codex-sol": MID, "codex-luna": LIGHT, "codex-mini": LIGHT,
    "ollama": LIGHT, "ollama-small": LIGHT,
}


def provider_tier(pid: str) -> Optional[str]:
    """Descriptive tier of a provider id, or None when unrated."""
    if not pid:
        return None
    if pid in BUILTIN_TIERS:
        return BUILTIN_TIERS[pid]
    if pid.startswith("ollama:"):
        return LIGHT
    return None


def provider_family(pid: str) -> str:
    pid = pid or ""
    if pid.startswith("claude"):
        return "claude"
    if pid.startswith("codex"):
        return "codex"
    if pid.startswith("ollama"):
        return "ollama"
    return pid.split(":", 1)[0] or "unknown"


# ── Copy (spec 9.2). Plain English only; the private build may override. ────
COPY = {
    "cat.chat":          ("quick chat", "This was a short, simple message, so a fast model answered to save your allowance."),
    "cat.lookup":        ("quick question", "This was a quick question with a short answer, so a fast model was enough."),
    "cat.rewrite":       ("editing text", "Editing text you already wrote is simple work, so a fast model handled it."),
    "cat.writing":       ("writing task", "Writing works well on the standard model and uses less of your allowance than the strongest one."),
    "cat.research":      ("explaining a topic", "Explaining and comparing needs some thought, so the standard model answered."),
    "cat.personal":      ("personal topic", "Personal topics deserve a thoughtful answer, so the standard model answered."),
    "cat.vision":        ("reading your file", "You attached an image or document, so a model that can read it answered."),
    "cat.code_help":     ("explaining code", "Explaining code or an error message needs some thought, so the standard model answered."),
    "cat.planning":      ("planning", "Plans and decisions benefit from careful thinking, so the strongest model answered."),
    "cat.health_legal":  ("health or legal topic", "Health and legal questions need care, so the strongest model answered."),
    "cat.multi_part":    ("several requests", "This message asked for several things at once, so the strongest model handled it."),
    "lock.security":     ("about security", "This touches passwords or security, so I always use the strongest model for it."),
    "lock.money":        ("about money", "This involves money, so I always use the strongest model for it."),
    "lock.release":      ("publishing or servers", "This involves publishing, servers or going live, so I always use the strongest model for it."),
    "lock.action":       ("changes something", "This asks me to change, send or delete something, so I used the strongest model to be safe."),
    "lock.code_change":  ("changing code", "Changing code is easy to get subtly wrong, so the strongest model handled it."),
    "lock.crisis":       ("taking care", "This sounds important, so I used the strongest model to answer with care."),
    "unclear.safe":      ("playing it safe", "I wasn't sure what this needs, so I used the strongest model rather than guess."),
    "triage.down":       ("quick check said simple", "A quick check said this is simple, so a lighter model answered."),
    "triage.up":         ("quick check said careful", "A quick check said this needs care, so a stronger model answered."),
    "follow.approval":   ("carrying on", "You approved the last step, so the same kind of model carried on."),
    "follow.context":    ("carrying on", "Your reply continues the earlier request, so the model matched what that work needs."),
    "follow.offer":      ("carrying on the work", "You said yes to work the last reply offered to do, so the strongest model carried it out."),
    "sticky":            ("same conversation", "We're in the middle of a task, so I kept the same model so it doesn't lose track."),
    "user.request":      ("you asked", "You asked for this model in your message."),
    "redo":              ("trying stronger", "You asked for a stronger model, so this answer came from one."),
    "escalated":         ("retried", "The first answer looked unsure, so I tried again with a stronger model."),
    "limit.cross":       ("{family} is busy", "{family} has reached its usage limit, so another service answered at the same level."),
    "limit.up":          ("next level up", "No model at the usual level was available, so the next stronger one answered."),
    "limit.down":        ("usage limits", "Stronger models were at their usage limits, so a lighter one answered as you allowed in Settings."),
    "fallback_default":  ("your home model", "No model at the right level was available, so your home model answered."),
}

FAMILY_LABELS = {"claude": "Claude", "codex": "ChatGPT", "ollama": "Your local model"}


def reason_copy(code: str, family: str = "") -> tuple:
    short, long = COPY.get(code, COPY["unclear.safe"])
    fam = FAMILY_LABELS.get(family, family.title() if family else "Your usual service")
    return short.replace("{family}", fam), long.replace("{family}", fam)


def badge_text(decision: "Decision") -> str:
    short, _ = reason_copy(decision.reason_code, decision.limited_family or "")
    if decision.reason_code == "fallback_default":
        return f"Auto · {short}"
    return f"Auto · {TIER_NAMES.get(decision.tier, 'Strongest')} · {short}"


# ── Context and decision ─────────────────────────────────────────────────────
@dataclass
class RouteContext:
    message: str
    project_path: str = ""
    attachments: tuple = ()          # attachment kinds: "image", "pdf", "text", "audio"
    prev: Optional[dict] = None      # pane's previous decision: tier, category, provider_id, ts
    last_ask: str = ""               # the pane's previous user message (for follow-ups)
    last_reply: str = ""             # the pane's previous assistant reply (for follow-ups)
    settings: Optional[dict] = None
    now: Optional[float] = None
    home_provider: str = ""          # the user's Default LLM (ACTIVE_PROVIDER)
    min_tier: Optional[str] = None   # Redo floor ("Try a stronger model")
    triage: Optional[Callable[[str], Optional[dict]]] = None
    rules_only: bool = False         # shadow mode: no triage, no selection


@dataclass
class Decision:
    provider_id: Optional[str] = None
    family: Optional[str] = None
    tier: str = TOP
    min_tier: str = TOP
    category: str = "unclear"
    categories: list = field(default_factory=list)
    rule_hits: list = field(default_factory=list)
    confidence: str = "none"
    reason_code: str = "unclear.safe"
    reason_text: str = ""
    triage: Optional[dict] = None
    triage_used: bool = False
    sticky: bool = False
    cache_warm: bool = False
    fallback: Optional[str] = None
    limited_family: Optional[str] = None
    skipped: list = field(default_factory=list)
    bias: str = "balanced"
    catalog_version: str = BUILTIN_CATALOG_VERSION
    router_version: str = ROUTER_VERSION
    decided_ms: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


# ── Classification (spec 6.4 steps 1 and 2) ─────────────────────────────────
def classify(message: str, project_path: str = "", attachments: Iterable[str] = ()) -> dict:
    """Run every rule. Returns {categories: {cat: "strong"|"weak"}, rule_hits,
    confidence, asks}. Pure and fast."""
    msg = message or ""
    hits: dict = {}
    rule_hits: list = []

    def _hit(cat, weight, rid):
        if hits.get(cat) != "strong":
            hits[cat] = weight
        if rid not in rule_hits:
            rule_hits.append(rid)

    for rule, rx in _COMPILED:
        if rx.search(msg):
            weight = rule.weight
            if rule.id == "rw.verb" and _has_pasted_text(msg):
                weight = "strong"
            _hit(rule.category, weight, rule.id)

    code_shape = bool(_CODE_FENCE.search(msg) or _TRACE.search(msg) or _CODE_SHAPE.search(msg))
    edit_verbs = bool(_EDIT_VERBS.search(msg))
    if code_shape:
        if project_path or edit_verbs:
            _hit("code_change", "strong", "shape.code_edit" if edit_verbs else "pane.code")
        else:
            _hit("code_help", "strong", "shape.code")
    elif project_path and "code_help" in hits:
        # Any code request in a project pane may change files.
        _hit("code_change", "strong", "pane.code")

    kinds = [str(k).lower() for k in attachments or ()]
    if any(k and k != "audio" for k in kinds):
        _hit("vision", "strong", "att.file")

    asks = count_asks(msg)
    stripped = msg.strip()

    # Chat: greeting or thanks, short, and nothing else matched. Checked
    # before the structural lookup so "how are you?" stays a chat.
    greeting = not hits and len(stripped) < 80 and bool(_GREETING.match(stripped))
    if greeting:
        _hit("chat", "strong", "chat.greeting")

    # Lookup: a short single question with no code is at least a weak lookup.
    if (not greeting and "lookup" not in hits and len(stripped) < 200 and asks == 1 and not code_shape
            and (_QUESTION_START.search(stripped) or stripped.endswith("?"))):
        _hit("lookup", "weak", "look.short_question")
    # A lookup cue in a long or multi-ask message is not a quick lookup.
    if "lookup" in hits and (len(stripped) >= 200 or asks > 1 or code_shape):
        hits.pop("lookup", None)


    # Multi-part: three or more asks, or two substantive categories that are
    # not a natural pair.
    substantive = [c for c in hits if c not in _MULTI_PART_IGNORE]
    multi = asks >= 3
    if not multi and len(substantive) >= 2:
        pairs_ok = all(frozenset({a, b}) in _COMPATIBLE
                       for i, a in enumerate(substantive) for b in substantive[i + 1:])
        multi = not pairs_ok
    if multi:
        _hit("multi_part", "strong", "shape.multi_ask" if asks >= 3 else "shape.multi_category")

    if any(w == "strong" for w in hits.values()):
        confidence = "confident"
    elif hits:
        confidence = "weak"
    else:
        confidence = "none"
    return {"categories": hits, "rule_hits": rule_hits, "confidence": confidence, "asks": asks}


# ── Triage mapping (spec 6.7) ───────────────────────────────────────────────
def triage_valid(result) -> bool:
    """Strict schema check for a triage result."""
    if not isinstance(result, dict):
        return False
    diff = result.get("difficulty")
    return (result.get("category") in CATEGORY_TIERS
            and isinstance(diff, int) and not isinstance(diff, bool) and 1 <= diff <= 5
            and result.get("risk") in ("low", "medium", "high")
            and isinstance(result.get("takes_action"), bool)
            and result.get("confidence") in ("low", "medium", "high"))


def triage_tier(result: Optional[dict]) -> str:
    """Map a triage JSON result to a tier. Anything malformed means TOP."""
    if not triage_valid(result):
        return TOP
    cat, diff = result["category"], result["difficulty"]
    risk, act, conf = result["risk"], result["takes_action"], result["confidence"]
    if risk == "high" or act or conf == "low" or cat in LOCKED:
        return TOP
    if diff <= 2:
        return LIGHT
    if diff == 3:
        return MID
    return TOP


# ── Floor (spec 6.4 steps 3 to 7) ───────────────────────────────────────────
def _pick_reason_category(cat_tiers: dict, final_tier: str) -> str:
    at_final = [c for c, t in cat_tiers.items() if t == final_tier]
    pool = at_final or list(cat_tiers)
    for c in REASON_PRIORITY:
        if c in pool:
            return c
    return pool[0] if pool else "unclear"


def _sticky_active(prev: Optional[dict], settings: dict, now: float) -> bool:
    if not isinstance(prev, dict) or prev.get("tier") not in TIER_RANK:
        return False
    minutes = settings.get("sticky_minutes", 10)
    if not minutes:
        return False
    try:
        return (now - float(prev.get("ts") or 0)) <= minutes * 60
    except (TypeError, ValueError):
        return False


def _category_tier(cat: str, bias_idx: int, settings: dict) -> str:
    """A category's tier under the bias column and the user's floor for it."""
    t = CATEGORY_TIERS[cat][bias_idx]
    user = settings["category_floor"].get(cat, "auto")
    if user != "auto":
        ut = SETTING_TIERS[user]
        t = tier_max(t, ut) if cat in LOCKED else ut
    return t


def _follow_up(ctx: RouteContext, prev: Optional[dict], settings: dict, bias_idx: int,
               approval: bool) -> Optional[Decision]:
    """Route a follow-up from the conversation before it. Returns None when
    there is nothing to go on, so the normal unclear handling applies.

    Reads the previous ask and the end of the previous reply:
      * an approval of something the reply offered to do -> Strongest (it acts);
      * otherwise the strongest categories found in that context set the tier;
      * never below the previous Auto turn's tier, nor the Redo floor.
    Weak hits and the multi-part shape are ignored: they describe how a
    message was worded, and the reply's own prose would trip them."""
    reply = (ctx.last_reply or "").strip()
    ask = (ctx.last_ask or "").strip()
    has_prev = bool(prev and prev.get("tier") in TIER_RANK)
    if not (reply or ask or has_prev):
        return None

    cat_tiers: dict = {}
    hits = ["follow.approval" if approval else "follow.answer"]
    code = "follow.approval"

    offer = bool(reply) and bool(_OFFER.search(reply[-CONTEXT_OFFER_CHARS:]))
    if offer and approval:
        cat_tiers["action"] = TOP
        hits.append("follow.offer")
        code = "follow.offer"

    context = "\n".join(x for x in (ask[-CONTEXT_ASK_CHARS:], reply[-CONTEXT_REPLY_CHARS:]) if x)
    found = False
    if context:
        cc = classify(context, ctx.project_path)
        for cat, weight in cc["categories"].items():
            if weight != "strong" or cat in ("multi_part", "chat"):
                continue
            t = _category_tier(cat, bias_idx, settings)
            cat_tiers[cat] = tier_max(cat_tiers.get(cat), t) if cat in cat_tiers else t
            found = True
        if found:
            hits.append("follow.context")
            if code != "follow.offer":
                code = "follow.context"

    if has_prev:
        pc = prev.get("category") if prev.get("category") in CATEGORY_TIERS else None
        key = pc or "unclear"
        cat_tiers[key] = tier_max(cat_tiers.get(key), prev["tier"]) if key in cat_tiers else prev["tier"]
    if not cat_tiers:
        # Context exists but says nothing recognisable: no safe basis to go
        # lower, so leave it to the normal unclear handling.
        return None

    tier = tier_max(*cat_tiers.values())
    if ctx.min_tier in TIER_RANK:
        tier = tier_max(tier, ctx.min_tier)
    d = Decision()
    known = {c: t for c, t in cat_tiers.items() if c in CATEGORY_TIERS}
    d.category = _pick_reason_category(known, tier) if known else "unclear"
    d.categories = sorted(known, key=REASON_PRIORITY.index) or ["unclear"]
    d.rule_hits = hits
    d.confidence = "confident"
    d.min_tier = d.tier = tier
    d.sticky = has_prev
    if ctx.min_tier in TIER_RANK and TIER_RANK[ctx.min_tier] > TIER_RANK[tier_max(*cat_tiers.values())]:
        code = "redo"
    d.reason_code = code if code in COPY else "follow.approval"
    d.reason_text = reason_copy(d.reason_code)[1]
    return d


def plan(ctx: RouteContext) -> Decision:
    """Steps 1 to 7: work out the minimum tier and the reason. No model choice."""
    t0 = time.perf_counter()
    settings = normalize_settings(ctx.settings)
    now = ctx.now if ctx.now is not None else time.time()
    bias = settings["bias"]
    bias_idx = BIASES.index(bias)
    d = Decision(bias=bias)
    msg = ctx.message or ""
    prev = ctx.prev if isinstance(ctx.prev, dict) else None

    c = classify(msg, ctx.project_path, ctx.attachments)

    # Follow-ups ("continue", "yes", "the second one"): the words carry no
    # signal, so decide from the conversation that came before.
    approval = bool(_APPROVAL.match(msg))
    short_answer = (not approval and c["confidence"] == "none" and "?" not in msg
                    and 0 < len(msg.strip()) <= SHORT_REPLY_CHARS
                    and not any(k and k != "audio" for k in (str(x).lower() for x in ctx.attachments or ())))
    if approval or short_answer:
        fd = _follow_up(ctx, prev, settings, bias_idx, approval)
        if fd is not None:
            fd.bias = bias
            fd.decided_ms = int((time.perf_counter() - t0) * 1000)
            return fd
    hits = c["categories"]
    d.categories = sorted(hits, key=REASON_PRIORITY.index)
    d.rule_hits = list(c["rule_hits"])
    d.confidence = c["confidence"]
    locked_hit = any(cat in LOCKED for cat in hits) or any(r in LOCKED_RULE_IDS for r in d.rule_hits)

    # Steps 3 and 4: bias column, then per-category settings floors.
    cat_tiers = {}
    for cat in hits:
        cat_tiers[cat] = _category_tier(cat, bias_idx, settings)
    if "hl.crisis" in d.rule_hits:
        cat_tiers["health_legal"] = TOP
    floor = tier_max(*cat_tiers.values()) if cat_tiers else LIGHT
    rule_floor = floor
    reason_cat = _pick_reason_category(cat_tiers, floor) if cat_tiers else "unclear"

    # Step 5: explicit request in the message.
    requested = None
    lowered = False
    if _REQ_TOP.search(msg):
        requested = TOP
    elif _REQ_MID.search(msg):
        requested = MID
    elif _REQ_LIGHT.search(msg):
        requested = LIGHT
    if requested:
        if TIER_RANK[requested] > TIER_RANK[floor]:
            floor = requested
        elif not locked_hit and TIER_RANK[requested] < TIER_RANK[floor] and d.confidence != "none":
            floor = requested
            lowered = True

    # Step 6: stickiness. Never down inside a thread, unless the user just
    # asked for a lighter model in so many words.
    sticky_raised = False
    if not lowered and _sticky_active(prev, settings, now):
        if TIER_RANK[prev["tier"]] > TIER_RANK[floor]:
            floor = prev["tier"]
            sticky_raised = True
        d.sticky = True

    # Step 7: unclear handling.
    triage_effect = None
    # An explicit request for a stronger model settles it; a request for a
    # lighter one does not, because a vague message still needs the safety net.
    if d.confidence in ("weak", "none") and not locked_hit and requested in (None, LIGHT):
        if ctx.rules_only or not settings["triage"] or ctx.triage is None:
            floor = TOP
            triage_effect = "safe"
        else:
            result = None
            try:
                result = ctx.triage(msg)
            except Exception:
                result = None
            d.triage_used = True
            if triage_valid(result):
                d.triage = {k: result[k] for k in
                            ("category", "difficulty", "risk", "takes_action", "confidence")}
                floor = tier_max(floor, triage_tier(result))
                triage_effect = "down" if floor != TOP else "up"
                reason_cat = d.triage["category"]
            else:
                floor = TOP
                triage_effect = "safe"

    # Redo floor.
    redo_raised = False
    if ctx.min_tier in TIER_RANK and TIER_RANK[ctx.min_tier] > TIER_RANK[floor]:
        floor = ctx.min_tier
        redo_raised = True

    d.min_tier = floor
    d.tier = floor
    d.category = reason_cat
    if redo_raised:
        code = "redo"
    elif requested and (TIER_RANK[requested] > TIER_RANK[rule_floor] or lowered):
        code = "user.request"
    elif "hl.crisis" in d.rule_hits:
        code = "lock.crisis"
    elif locked_hit and reason_cat in LOCKED:
        code = f"lock.{reason_cat}"
    elif sticky_raised:
        code = "sticky"
    elif triage_effect == "safe":
        code = "unclear.safe"
        d.category = "unclear"
    elif triage_effect == "down":
        code = "triage.down"
    elif triage_effect == "up":
        code = "triage.up"
    else:
        code = f"cat.{reason_cat}"
    if code not in COPY:
        code = "unclear.safe"
    d.reason_code = code
    d.reason_text = reason_copy(code)[1]
    d.decided_ms = int((time.perf_counter() - t0) * 1000)
    return d


# ── Model selection (spec 6.6 and 6.10) ─────────────────────────────────────
def _candidate_tier(c: dict, settings: dict) -> Optional[str]:
    ov = settings["model_overrides"].get(c.get("pid", ""))
    if ov == "off":
        return None
    if ov in SETTING_TIERS:
        return SETTING_TIERS[ov]
    t = c.get("tier")
    return t if t in TIER_RANK else None


def _usable(c: dict, d: Decision, settings: dict) -> tuple:
    """(ok, why_not) for one candidate against this decision's constraints."""
    pid = c.get("pid", "")
    if not c.get("eligible", False):
        return False, c.get("why_not") or "not available"
    if settings["allowed"].get(pid) is False:
        return False, "turned off in Auto settings"
    if _candidate_tier(c, settings) is None:
        return False, "not rated"
    cats = set(d.categories) | {d.category}
    if "vision" in cats and not c.get("vision", False):
        return False, "cannot read attachments"
    if c.get("local") and (cats & (LOCKED | {"vision", "code_change", "health_legal"})):
        return False, "local models do not take this kind of task"
    if c.get("read_only_ok") is False and not cats <= {"chat", "lookup", "rewrite", "writing"}:
        return False, "cannot run read-only"
    return True, ""


def select(d: Decision, candidates: Iterable[dict], ctx: RouteContext) -> Decision:
    """Pick a model at the floor tier (or the next one up) from candidates."""
    settings = normalize_settings(ctx.settings)
    now = ctx.now if ctx.now is not None else time.time()
    prev = ctx.prev if isinstance(ctx.prev, dict) else None
    warm_pid = prev.get("provider_id") if (prev and _sticky_active(prev, settings, now)) else None
    home_family = provider_family(ctx.home_provider) if ctx.home_provider else ""

    usable, skipped = [], []
    for c in candidates or ():
        ok, why = _usable(c, d, settings)
        if ok:
            usable.append(c)
        else:
            skipped.append({"pid": c.get("pid"), "why_not": why})
    d.skipped = skipped

    def order(c):
        return (
            0 if c.get("pid") == warm_pid else 1,
            0 if home_family and c.get("family") == home_family else 1,
            -float(c.get("headroom_pct") if c.get("headroom_pct") is not None else 0),
            int(c.get("cost_rank") or 0),
            str(c.get("pid")),
        )

    floor_rank = TIER_RANK[d.min_tier]
    chosen = None
    fallback = None
    for rank in range(floor_rank, 3):
        at = sorted((c for c in usable if TIER_RANK[_candidate_tier(c, settings)] == rank), key=order)
        if at:
            chosen = at[0]
            if rank > floor_rank:
                fallback = "limit.up"
            break
    may_step_down = (settings["allow_step_down_on_limit"]
                     and not (set(d.categories) & LOCKED)
                     and "hl.crisis" not in d.rule_hits
                     and d.reason_code not in ("unclear.safe", "redo", "user.request"))
    if chosen is None and may_step_down:
        for rank in range(floor_rank - 1, -1, -1):
            at = sorted((c for c in usable if TIER_RANK[_candidate_tier(c, settings)] == rank), key=order)
            if at:
                chosen, fallback = at[0], "limit.down"
                break
    if chosen is None:
        d.provider_id = ctx.home_provider or None
        d.family = provider_family(ctx.home_provider) if ctx.home_provider else None
        d.tier = provider_tier(ctx.home_provider) or TOP
        d.fallback = "fallback_default"
        d.reason_code = "fallback_default"
        d.reason_text = reason_copy("fallback_default")[1]
        return d

    d.provider_id = chosen.get("pid")
    d.family = chosen.get("family") or provider_family(d.provider_id)
    d.tier = _candidate_tier(chosen, settings)
    d.cache_warm = bool(warm_pid and chosen.get("pid") == warm_pid)

    # Same tier but a different family because the home family is limited.
    if fallback is None and home_family and d.family != home_family:
        home_blocked = [c for c in candidates or ()
                        if c.get("family") == home_family
                        and _candidate_tier(c, settings) == d.tier
                        and not c.get("eligible", False)
                        and "limit" in str(c.get("why_not") or "").lower()]
        if home_blocked:
            fallback = "limit.cross"
            d.limited_family = home_family
    d.fallback = fallback
    if fallback and not d.reason_code.startswith("lock.") and d.reason_code not in ("user.request", "redo", "lock.crisis"):
        d.reason_code = fallback
        d.reason_text = reason_copy(fallback, d.limited_family or "")[1]
    return d


def decide(ctx: RouteContext, candidates: Optional[Iterable[dict]] = None) -> Decision:
    """Full decision. With rules_only or no candidates, returns the floor
    without choosing a model (shadow mode)."""
    t0 = time.perf_counter()
    d = plan(ctx)
    if not ctx.rules_only and candidates is not None:
        d = select(d, candidates, ctx)
    d.decided_ms = int((time.perf_counter() - t0) * 1000)
    return d


def shadow(message: str, project_path: str = "", attachments: Iterable[str] = (),
           prev: Optional[dict] = None, settings: Optional[dict] = None,
           now: Optional[float] = None) -> Decision:
    """Rules-only decision for a manual turn (spec 10.3): no triage, no model
    call, no selection. Unclear prompts count as Strongest."""
    return decide(RouteContext(message=message, project_path=project_path,
                               attachments=tuple(attachments or ()), prev=prev,
                               settings=settings, now=now, rules_only=True))
