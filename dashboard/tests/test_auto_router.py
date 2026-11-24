"""Tests for auto_router (Task A1 of docs/SPEC_auto_router.md).

Run from dashboard/:  python -m unittest tests.test_auto_router -v
Pure logic only: no CLI, no network, no bridge import.
"""
import re
import time
import unittest

import auto_router as ar
from auto_router import LIGHT, MID, TOP, RouteContext

NOW = 1_800_000_000.0


def floor(msg, **kw):
    """Rules-only floor tier for a message (triage off unless given)."""
    settings = kw.pop("settings", None)
    triage = kw.pop("triage", None)
    if triage is None:
        settings = {**(settings or {}), "triage": False}
    ctx = RouteContext(message=msg, settings=settings, now=NOW, triage=triage, **kw)
    return ar.plan(ctx)


# High-risk prompts: every one must land on the strongest tier, in every bias.
HIGH_RISK = [
    ("delete my old invoices", "lock.action"),
    ("uninstall the photo app", "lock.action"),
    ("send this to my boss", "lock.action"),
    ("rename all the files in my downloads folder", "lock.action"),
    ("set a reminder for 3pm", "lock.action"),
    ("my password got leaked, what do I do", "lock.security"),
    ("where should I store my API key", "lock.security"),
    ("I think I was hacked", "lock.security"),
    ("is this a phishing email", "lock.security"),
    ("should I buy bitcoin now", "lock.money"),
    ("how do I get a refund from my bank", "lock.money"),
    ("check my wallet balance", "lock.money"),
    ("deploy the site to production", "lock.release"),
    ("set up DNS for my domain names", "lock.release"),
    ("fix the bug in app.js", "lock.code_change"),
    ("write a python script that cleans up csv data", "lock.code_change"),
    ("refactor the login function", "lock.code_change"),
    ("I want to end my life", "lock.crisis"),
    ("quick question about my password", "lock.security"),
    ("use haiku: delete my old emails", "lock.action"),
]

# (message, expected tier on Balanced, expected reason code)
BALANCED = [
    ("hi", LIGHT, "cat.chat"),
    ("thanks!", LIGHT, "cat.chat"),
    ("how are you?", LIGHT, "cat.chat"),
    ("good morning", LIGHT, "cat.chat"),
    ("what time is it in Vegas", LIGHT, "cat.lookup"),
    ("convert 5 miles to km", LIGHT, "cat.lookup"),
    ("define entropy", LIGHT, "cat.lookup"),
    ("who wrote the Bhagavad Gita?", LIGHT, "cat.lookup"),
    ("write a caption for my sunset photo", MID, "cat.writing"),
    ("draft a thank you letter to my aunt", MID, "cat.writing"),
    ("explain how vaccines work", MID, "cat.research"),
    ("compare yoga and pilates", MID, "cat.research"),
    ("I feel lonely lately", MID, "cat.personal"),
    ("help me make a plan for my week", TOP, "cat.planning"),
    ("I have chest pain when I run", TOP, "cat.health_legal"),
    ("can you help with my thing", TOP, "unclear.safe"),
    ("hmm", TOP, "unclear.safe"),
    ("fix the grammar: their going to the store tomorrow and they is happy about it", LIGHT, "cat.rewrite"),
]


class TestLockedCategories(unittest.TestCase):
    def test_high_risk_always_top_in_every_bias(self):
        for bias in ar.BIASES:
            for msg, code in HIGH_RISK:
                with self.subTest(bias=bias, msg=msg):
                    d = floor(msg, settings={"bias": bias})
                    self.assertEqual(d.tier, TOP)
                    self.assertEqual(d.reason_code, code)

    def test_settings_cannot_lower_locked(self):
        s = {"category_floor": {c: "fast" for c in ar.CATEGORIES}}
        for msg, _ in HIGH_RISK:
            with self.subTest(msg=msg):
                self.assertEqual(floor(msg, settings=s).tier, TOP)

    def test_triage_never_consulted_for_locked(self):
        calls = []
        def triage(m):
            calls.append(m)
            return {"category": "chat", "difficulty": 1, "risk": "low", "takes_action": False, "confidence": "high"}
        d = floor("delete my old invoices", triage=triage)
        self.assertEqual(d.tier, TOP)
        self.assertEqual(calls, [])

    def test_crisis_stays_top_even_if_health_lowered(self):
        d = floor("I want to end my life", settings={"category_floor": {"health_legal": "fast"}})
        self.assertEqual(d.tier, TOP)
        self.assertEqual(d.reason_code, "lock.crisis")


class TestBalancedTable(unittest.TestCase):
    def test_balanced_routes(self):
        for msg, tier, code in BALANCED:
            with self.subTest(msg=msg):
                d = floor(msg)
                self.assertEqual(d.tier, tier, (d.categories, d.rule_hits))
                self.assertEqual(d.reason_code, code)


class TestBias(unittest.TestCase):
    def test_writing_by_bias(self):
        msg = "write a caption for my sunset photo"
        self.assertEqual(floor(msg, settings={"bias": "save"}).tier, LIGHT)
        self.assertEqual(floor(msg, settings={"bias": "balanced"}).tier, MID)
        self.assertEqual(floor(msg, settings={"bias": "quality"}).tier, TOP)

    def test_chat_by_bias(self):
        self.assertEqual(floor("hi", settings={"bias": "save"}).tier, LIGHT)
        self.assertEqual(floor("hi", settings={"bias": "quality"}).tier, MID)

    def test_planning_by_bias(self):
        msg = "help me make a plan for my week"
        self.assertEqual(floor(msg, settings={"bias": "save"}).tier, MID)
        self.assertEqual(floor(msg, settings={"bias": "quality"}).tier, TOP)

    def test_bad_bias_falls_back_to_balanced(self):
        self.assertEqual(floor("write a caption for my photo", settings={"bias": "cheap"}).tier, MID)


class TestSettingsFloors(unittest.TestCase):
    def test_floor_can_raise(self):
        d = floor("write a caption for my photo", settings={"category_floor": {"writing": "strongest"}})
        self.assertEqual(d.tier, TOP)

    def test_floor_can_lower_non_locked(self):
        d = floor("help me make a plan for my week", settings={"category_floor": {"planning": "fast"}})
        self.assertEqual(d.tier, LIGHT)

    def test_invalid_floor_ignored(self):
        d = floor("write a caption for my photo", settings={"category_floor": {"writing": "tiny"}})
        self.assertEqual(d.tier, MID)


class TestExplicitRequests(unittest.TestCase):
    def test_raise_to_top(self):
        for msg in ("hi, use opus", "explain photosynthesis and think hard", "write a poem, be careful"):
            with self.subTest(msg=msg):
                d = floor(msg)
                self.assertEqual(d.tier, TOP)
                self.assertEqual(d.reason_code, "user.request")

    def test_use_sonnet_sets_mid(self):
        self.assertEqual(floor("hi there, use sonnet").tier, MID)

    def test_lower_non_locked(self):
        d = floor("explain how vaccines work, quick answer")
        self.assertEqual(d.tier, LIGHT)
        self.assertEqual(d.reason_code, "user.request")

    def test_lower_ignored_for_locked(self):
        self.assertEqual(floor("quick answer: should I sell my stocks").tier, TOP)

    def test_lower_ignored_when_unclear(self):
        self.assertEqual(floor("hmm, quick answer").tier, TOP)


class TestStickiness(unittest.TestCase):
    def prev(self, tier, age_s, pid="claude-cli"):
        return {"tier": tier, "category": "planning", "provider_id": pid, "ts": NOW - age_s}

    def test_never_down_inside_window(self):
        d = floor("thanks!", prev=self.prev(TOP, 60))
        self.assertEqual(d.tier, TOP)
        self.assertEqual(d.reason_code, "sticky")
        self.assertTrue(d.sticky)

    def test_window_expires(self):
        self.assertEqual(floor("thanks!", prev=self.prev(TOP, 11 * 60)).tier, LIGHT)

    def test_can_go_up(self):
        self.assertEqual(floor("deploy it to production", prev=self.prev(LIGHT, 30)).tier, TOP)

    def test_sticky_zero_disables(self):
        d = floor("thanks!", prev=self.prev(TOP, 30), settings={"sticky_minutes": 0})
        self.assertEqual(d.tier, LIGHT)

    def test_explicit_lower_skips_stickiness(self):
        d = floor("explain how tides work, quick answer", prev=self.prev(TOP, 30))
        self.assertEqual(d.tier, LIGHT)


class TestApproval(unittest.TestCase):
    def test_inherits_previous(self):
        prev = {"tier": TOP, "category": "action", "provider_id": "claude-cli", "ts": NOW - 3600}
        for msg in ("yes", "do it", "go ahead!", "ship it", "ok"):
            with self.subTest(msg=msg):
                d = floor(msg, prev=prev)
                self.assertEqual(d.tier, TOP)
                self.assertEqual(d.category, "action")
                self.assertEqual(d.reason_code, "follow.approval")

    def test_inherits_light(self):
        prev = {"tier": LIGHT, "category": "lookup", "provider_id": "claude-cli-haiku", "ts": NOW - 20}
        self.assertEqual(floor("yes please", prev=prev).tier, LIGHT)

    def test_without_previous_is_unclear(self):
        self.assertEqual(floor("yes").tier, TOP)


class TestTriage(unittest.TestCase):
    def good(self, **kw):
        base = {"category": "lookup", "difficulty": 1, "risk": "low", "takes_action": False, "confidence": "high"}
        base.update(kw)
        return lambda m: base

    def test_triage_down(self):
        d = floor("can you help with my thing", triage=self.good())
        self.assertEqual(d.tier, LIGHT)
        self.assertEqual(d.reason_code, "triage.down")
        self.assertTrue(d.triage_used)

    def test_triage_mid(self):
        self.assertEqual(floor("can you help with my thing", triage=self.good(difficulty=3)).tier, MID)

    def test_triage_high_risk_or_action_or_low_conf_is_top(self):
        for kw in ({"risk": "high"}, {"takes_action": True}, {"confidence": "low"}, {"difficulty": 5},
                   {"category": "money"}):
            with self.subTest(kw=kw):
                d = floor("can you help with my thing", triage=self.good(**kw))
                self.assertEqual(d.tier, TOP)
                self.assertEqual(d.reason_code, "triage.up")

    def test_malformed_is_top(self):
        for bad in (None, "nope", {}, {"category": "lookup"},
                    {"category": "zzz", "difficulty": 1, "risk": "low", "takes_action": False, "confidence": "high"},
                    {"category": "lookup", "difficulty": True, "risk": "low", "takes_action": False, "confidence": "high"},
                    {"category": "lookup", "difficulty": 9, "risk": "low", "takes_action": False, "confidence": "high"}):
            with self.subTest(bad=bad):
                d = floor("can you help with my thing", triage=lambda m, b=bad: b)
                self.assertEqual(d.tier, TOP)
                self.assertEqual(d.reason_code, "unclear.safe")

    def test_exception_is_top(self):
        def boom(m):
            raise TimeoutError
        self.assertEqual(floor("can you help with my thing", triage=boom).tier, TOP)

    def test_triage_off_is_top(self):
        d = floor("can you help with my thing", triage=self.good(), settings={"triage": False})
        self.assertEqual(d.tier, TOP)
        self.assertFalse(d.triage_used)

    def test_rules_only_never_calls_triage(self):
        calls = []
        ctx = RouteContext(message="can you help with my thing", now=NOW, rules_only=True,
                           triage=lambda m: calls.append(m))
        self.assertEqual(ar.plan(ctx).tier, TOP)
        self.assertEqual(calls, [])

    def test_triage_cannot_lower_below_sticky(self):
        prev = {"tier": MID, "category": "writing", "provider_id": "claude-cli-sonnet", "ts": NOW - 30}
        d = floor("can you help with my thing", triage=self.good(), prev=prev)
        self.assertEqual(d.tier, MID)

    def test_triage_only_stores_schema_fields(self):
        d = floor("can you help with my thing",
                  triage=lambda m: {"category": "lookup", "difficulty": 1, "risk": "low",
                                    "takes_action": False, "confidence": "high", "echo": m})
        self.assertNotIn("echo", d.triage)


class TestShapeSignals(unittest.TestCase):
    def test_code_fence_without_edit_is_code_help(self):
        d = floor("what does this mean?\n```\nx = [i for i in range(3)]\n```")
        self.assertEqual(d.tier, MID)
        self.assertIn("code_help", d.categories)

    def test_traceback_with_fix_is_code_change(self):
        d = floor("Traceback (most recent call last):\n  File \"a.py\", line 3, in <module>\nKeyError: 'x'\nfix this")
        self.assertEqual(d.reason_code, "lock.code_change")

    def test_code_in_project_pane_is_code_change(self):
        d = floor("what does this do?\n```\ndef f(x): return x\n```", project_path="C:/proj")
        self.assertEqual(d.tier, TOP)
        self.assertIn("code_change", d.categories)

    def test_three_asks_is_multi_part(self):
        d = floor("what is a sonnet? who wrote the most? and also which is the best one?")
        self.assertIn("multi_part", d.categories)
        self.assertEqual(d.tier, TOP)

    def test_natural_pair_not_multi_part(self):
        d = floor("explain this error message from python")
        self.assertNotIn("multi_part", d.categories)

    def test_image_attachment_is_vision(self):
        d = floor("what is in this picture", attachments=("image",))
        self.assertIn("vision", d.categories)
        self.assertEqual(d.tier, MID)

    def test_audio_attachment_does_not_route(self):
        d = floor("hi", attachments=("audio",))
        self.assertNotIn("vision", d.categories)

    def test_redo_floor(self):
        d = floor("hi", min_tier=MID)
        self.assertEqual(d.tier, MID)
        self.assertEqual(d.reason_code, "redo")

    def test_rule_hits_are_ids_not_text(self):
        d = floor("my password is hunter2 and my bank pin is 1234")
        for rid in d.rule_hits:
            self.assertRegex(rid, r"^[a-z_]+\.[a-z_]+$")
            self.assertNotIn("hunter2", rid)


def cand(pid, tier, family=None, eligible=True, **kw):
    c = {"pid": pid, "tier": tier, "family": family or ar.provider_family(pid), "eligible": eligible,
         "cost_rank": {LIGHT: 1, MID: 2, TOP: 3}[tier], "vision": pid.startswith("claude"),
         "local": pid.startswith("ollama"), "headroom_pct": 50}
    c.update(kw)
    return c


POOL = [
    cand("claude-cli", TOP), cand("claude-cli-sonnet", MID), cand("claude-cli-haiku", LIGHT),
    cand("codex", TOP), cand("codex-sol", MID), cand("codex-luna", LIGHT),
]


def pick(msg, candidates=POOL, home="claude-cli", **kw):
    settings = kw.pop("settings", None)
    settings = {**(settings or {}), "triage": False}
    ctx = RouteContext(message=msg, settings=settings, now=NOW, home_provider=home, **kw)
    return ar.decide(ctx, candidates)


class TestSelection(unittest.TestCase):
    def test_cheapest_strong_enough(self):
        self.assertEqual(pick("hi").provider_id, "claude-cli-haiku")
        self.assertEqual(pick("write a caption for my photo").provider_id, "claude-cli-sonnet")
        self.assertEqual(pick("delete my old invoices").provider_id, "claude-cli")

    def test_home_family_preferred(self):
        self.assertEqual(pick("hi", home="codex").provider_id, "codex-luna")

    def test_warm_cache_preferred(self):
        prev = {"tier": LIGHT, "category": "chat", "provider_id": "codex-luna", "ts": NOW - 30}
        d = pick("thanks!", prev=prev)
        self.assertEqual(d.provider_id, "codex-luna")
        self.assertTrue(d.cache_warm)

    def test_headroom_breaks_tie(self):
        pool = [cand("claude-cli-haiku", LIGHT, headroom_pct=10), cand("codex-luna", LIGHT, headroom_pct=90)]
        self.assertEqual(pick("hi", candidates=pool, home="gemini").provider_id, "codex-luna")

    def test_ineligible_never_chosen(self):
        pool = [cand("codex-luna", LIGHT, eligible=False, why_not="signed in with an API key"),
                cand("claude-cli", TOP)]
        d = pick("hi", candidates=pool, home="codex")
        self.assertEqual(d.provider_id, "claude-cli")
        self.assertIn({"pid": "codex-luna", "why_not": "signed in with an API key"}, d.skipped)

    def test_metered_like_candidates_never_chosen_randomized(self):
        import random
        rng = random.Random(7)
        msgs = [m for m, _, _ in BALANCED] + [m for m, _ in HIGH_RISK]
        bad = {"codex-api", "paid-slot"}
        for _ in range(500):
            pool = [cand("codex-api", rng.choice(ar.TIERS), family="codex", eligible=False, why_not="metered"),
                    cand("paid-slot", rng.choice(ar.TIERS), family="paid", eligible=False, why_not="metered"),
                    *rng.sample(POOL, rng.randint(0, len(POOL)))]
            d = pick(rng.choice(msgs), candidates=pool, home="claude-cli")
            self.assertNotIn(d.provider_id, bad)

    def test_allowed_false_skipped(self):
        d = pick("hi", settings={"allowed": {"claude-cli-haiku": False}})
        self.assertEqual(d.provider_id, "codex-luna")

    def test_override_off_and_retier(self):
        self.assertEqual(pick("hi", settings={"model_overrides": {"claude-cli-haiku": "off"}}).provider_id,
                         "codex-luna")
        pool = [cand("claude-cli-sonnet", MID), cand("claude-cli", TOP)]
        d = pick("hi", candidates=pool, settings={"model_overrides": {"claude-cli-sonnet": "fast"}})
        self.assertEqual(d.provider_id, "claude-cli-sonnet")
        self.assertEqual(d.tier, LIGHT)

    def test_vision_restricts_to_vision_models(self):
        d = pick("what is in this picture", attachments=("image",), home="codex")
        self.assertEqual(d.provider_id, "claude-cli-sonnet")

    def test_local_never_gets_locked_or_vision(self):
        pool = [cand("ollama:qwen2.5:3b", LIGHT), cand("claude-cli", TOP)]
        self.assertEqual(pick("delete my files", candidates=pool).provider_id, "claude-cli")
        self.assertEqual(pick("hi", candidates=pool, home="ollama").provider_id, "ollama:qwen2.5:3b")

    def test_read_only_incapable_limited_to_text(self):
        pool = [cand("codex-sol", MID, read_only_ok=False), cand("claude-cli", TOP)]
        self.assertEqual(pick("explain how tides work", candidates=pool).provider_id, "claude-cli")
        self.assertEqual(pick("write a caption for my photo", candidates=pool).provider_id, "codex-sol")

    def test_limit_cross_family(self):
        pool = [cand("claude-cli-sonnet", MID, eligible=False, why_not="usage limit until 2:40"),
                cand("codex-sol", MID), cand("claude-cli", TOP, eligible=False, why_not="usage limit until 2:40")]
        d = pick("write a caption for my photo", candidates=pool)
        self.assertEqual(d.provider_id, "codex-sol")
        self.assertEqual(d.fallback, "limit.cross")
        self.assertEqual(d.reason_code, "limit.cross")
        self.assertIn("Claude is busy", ar.badge_text(d))

    def test_limit_up(self):
        pool = [cand("claude-cli-haiku", LIGHT, eligible=False, why_not="usage limit"), cand("claude-cli-sonnet", MID)]
        d = pick("hi", candidates=pool)
        self.assertEqual((d.provider_id, d.fallback), ("claude-cli-sonnet", "limit.up"))

    def test_never_steps_down_by_default(self):
        pool = [cand("claude-cli-haiku", LIGHT)]
        d = pick("write a caption for my photo", candidates=pool)
        self.assertEqual(d.fallback, "fallback_default")
        self.assertEqual(d.provider_id, "claude-cli")

    def test_step_down_when_allowed(self):
        pool = [cand("claude-cli-haiku", LIGHT)]
        d = pick("write a caption for my photo", candidates=pool,
                 settings={"allow_step_down_on_limit": True})
        self.assertEqual((d.provider_id, d.fallback), ("claude-cli-haiku", "limit.down"))

    def test_step_down_never_for_locked(self):
        pool = [cand("claude-cli-haiku", LIGHT)]
        d = pick("delete my old files", candidates=pool, settings={"allow_step_down_on_limit": True})
        self.assertEqual(d.fallback, "fallback_default")

    def test_locked_reason_survives_limit_cross(self):
        pool = [cand("claude-cli", TOP, eligible=False, why_not="usage limit"), cand("codex", TOP)]
        d = pick("delete my old invoices", candidates=pool)
        self.assertEqual(d.provider_id, "codex")
        self.assertEqual(d.reason_code, "lock.action")
        self.assertEqual(d.fallback, "limit.cross")

    def test_nothing_eligible_uses_home(self):
        d = pick("hi", candidates=[])
        self.assertEqual((d.provider_id, d.reason_code), ("claude-cli", "fallback_default"))
        self.assertEqual(ar.badge_text(d), "Auto \u00b7 your home model")

    def test_rules_only_selects_nothing(self):
        d = ar.shadow("hi", now=NOW)
        self.assertIsNone(d.provider_id)
        self.assertEqual(d.tier, LIGHT)


class TestHelpers(unittest.TestCase):
    def test_provider_tier(self):
        self.assertEqual(ar.provider_tier("claude-cli"), TOP)
        self.assertEqual(ar.provider_tier("claude-cli-sonnet"), MID)
        self.assertEqual(ar.provider_tier("codex-luna"), LIGHT)
        self.assertEqual(ar.provider_tier("ollama:qwen2.5:3b"), LIGHT)
        self.assertIsNone(ar.provider_tier("gemini"))
        self.assertIsNone(ar.provider_tier("claude-cli-fable"))

    def test_provider_family(self):
        self.assertEqual(ar.provider_family("claude-cli-haiku"), "claude")
        self.assertEqual(ar.provider_family("codex-sol"), "codex")
        self.assertEqual(ar.provider_family("ollama:llama3"), "ollama")
        self.assertEqual(ar.provider_family("grok"), "grok")

    def test_normalize_settings_rejects_junk(self):
        s = ar.normalize_settings({"bias": 5, "sticky_minutes": 999, "triage": "yes",
                                   "allowed": {"x": "true"}, "model_overrides": {"y": "huge"}})
        self.assertEqual(s["bias"], "balanced")
        self.assertEqual(s["sticky_minutes"], 10)
        self.assertTrue(s["triage"])
        self.assertEqual(s["allowed"], {})
        self.assertEqual(s["model_overrides"], {})
        self.assertEqual(ar.normalize_settings(None)["mode"], "manual")

    def test_every_rule_category_known(self):
        for r in ar.RULES:
            self.assertIn(r.category, ar.CATEGORY_TIERS, r.id)
            self.assertIn(r.weight, ("strong", "weak"), r.id)
        self.assertEqual(len({r.id for r in ar.RULES}), len(ar.RULES))

    def test_rules_only_speed(self):
        msg = "please explain how the stock market works and compare it with crypto " * 20
        t0 = time.perf_counter()
        for _ in range(20):
            ar.shadow(msg, now=NOW)
        self.assertLess((time.perf_counter() - t0) / 20, 0.05)


class TestCopy(unittest.TestCase):
    BANNED = re.compile(
        r"\b(captain|starfleet|enterprise|stardate|lcars|engage|make it so|picard|riker|worf|geordi|"
        r"o'brien|troi|crusher|guinan|wesley|data daemon|neural matrix|positronic)\b", re.IGNORECASE)

    def test_no_dashes_or_franchise_words(self):
        for code, (short, long) in ar.COPY.items():
            for text in (short, long):
                with self.subTest(code=code):
                    self.assertNotIn("\u2014", text)
                    self.assertNotIn("\u2013", text)
                    self.assertIsNone(self.BANNED.search(text))
                    self.assertLessEqual(len(short), 60)

    def test_every_reason_code_has_copy(self):
        for c in ar.CATEGORIES:
            self.assertTrue(f"cat.{c}" in ar.COPY or c in ar.LOCKED, c)
        for c in ar.LOCKED:
            self.assertIn(f"lock.{c}", ar.COPY)

    def test_badge_format(self):
        d = floor("write a caption for my photo")
        self.assertEqual(ar.badge_text(d), "Auto \u00b7 Standard \u00b7 writing task")


if __name__ == "__main__":
    unittest.main()
