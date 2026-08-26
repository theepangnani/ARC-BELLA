#!/usr/bin/env python3
"""If this, then that — standing rules ARC checks in the background.

"Tell me if Tesla drops below 200." "Shout at me if I've spent more than five
dollars today." "Let me know if Nvidia moves more than three percent." Those are
all the same shape: a condition worth watching, and something to do when it
becomes true.

TWO THINGS THIS DELIBERATELY CANNOT DO, and both were asked for.

  1. IT CANNOT BUY OR SELL ANYTHING. There is no broker here, no account, no
     order. "If Tesla hits 200, sell" is not a rule ARC can carry out, and a
     feature that half-carried it out would be far worse than one that says so:
     you would believe a sale had happened. What ARC can do is tell you the
     instant it hits, on your phone, at whatever hour it happens — which is the
     part a person cannot do for themselves. The selling stays yours. Connecting
     a brokerage is also a regulated activity, not merely a technical one.

  2. IT CANNOT TOP UP AN ANTHROPIC BALANCE. There is no API for buying credit;
     the only thing that does it is Auto-reload in the Anthropic console, which
     is a switch the account owner turns on themselves and which then works
     without ARC's involvement. What ARC can do is watch the spend and warn
     before the money runs out — see the `spend` condition.

Both refusals are the same principle the market tools already run on: a thing
that reports honestly is worth more than a thing that acts convincingly.

WHAT IT CAN DO is notify — the phone, the browser, or both — and set a
reminder. Actions are an allow-list, and stay one: the same default-deny idiom
as PASSIVE_TOOLS and GUEST_TOOLS, so a new action does nothing until someone
decides it should.
"""

import io
import json
import os
import threading
import time
from pathlib import Path

import extras   # yahoo_quote — the one quote source the whole app uses

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
RULES = DATA_DIR / "triggers.json"

_lock = threading.RLock()

# How long after firing before a rule may fire again. Without it, "Tesla below
# 200" fires every thirty seconds all afternoon, and the phone becomes
# something you switch off — which costs you the one alert that mattered.
COOLDOWN = 3600.0

MAX_RULES = 40


def connected() -> bool:
    return True


# --- storage ----------------------------------------------------------------

def _load() -> list:
    try:
        data = json.loads(RULES.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items) -> None:
    try:
        RULES.parent.mkdir(parents=True, exist_ok=True)
        tmp = RULES.with_name(RULES.name + ".tmp")
        tmp.write_text(json.dumps(items[:MAX_RULES], ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, RULES)      # atomic, like alerts.py and alarm.py
    except Exception:
        pass


def _next_id(items) -> str:
    used = {r.get("id") for r in items}
    n = 1
    while "r%d" % n in used:
        n += 1
    return "r%d" % n


# --- conditions -------------------------------------------------------------
#
# Each returns (fired, description). The description is what gets said, so it
# carries the actual numbers — "Tesla is 197.40, below your 200" tells you
# something; "your rule fired" does not.

def _price(rule):
    sym = (rule.get("symbol") or "").upper()
    want = float(rule.get("value") or 0)
    op = rule.get("op") or "below"
    q = extras.yahoo_quote(sym)
    if not q or q.get("price") is None:
        return False, ""
    now = float(q["price"])
    name = q.get("name") or sym
    if op == "above" and now >= want:
        return True, "%s is %.2f, above your %.2f." % (name, now, want)
    if op == "below" and now <= want:
        return True, "%s is %.2f, below your %.2f." % (name, now, want)
    return False, ""


def _move(rule):
    """A percentage move on the day, either direction unless one is named."""
    sym = (rule.get("symbol") or "").upper()
    want = abs(float(rule.get("value") or 0))
    q = extras.yahoo_quote(sym)
    if not q or q.get("pct") is None:
        return False, ""
    pct = float(q["pct"])
    op = rule.get("op") or "either"
    name = q.get("name") or sym
    hit = (pct <= -want if op == "below"
           else pct >= want if op == "above"
           else abs(pct) >= want)
    if hit:
        return True, "%s has moved %+.2f%% today." % (name, pct)
    return False, ""


def _spend(rule):
    """Today's Anthropic spend against a ceiling.

    This is the honest half of "top my credit up automatically". ARC cannot buy
    credit — no API exists for it — but it can tell you before you run out,
    which is the failure this is really about: finding out mid-sentence.
    """
    want = float(rule.get("value") or 0)
    try:
        import stats
        spent = stats.day().get("cost", 0.0)
    except Exception:
        return False, ""
    if (rule.get("op") or "above") == "above" and spent >= want:
        return True, ("Today's spend is $%.2f, past your $%.2f mark. I cannot "
                      "top the account up — only Auto-reload in the Anthropic "
                      "console can — but you know now rather than later."
                      % (spent, want))
    return False, ""


CONDITIONS = {"price": _price, "move": _move, "spend": _spend}


# --- actions ----------------------------------------------------------------
# An allow-list, and it stays one. Buying, selling, spending and running
# commands are absent on purpose, not by oversight.

ACTIONS = ("notify", "push", "say", "remind")


def _do(rule, message: str) -> None:
    what = rule.get("action") or "notify"
    note = (rule.get("note") or "").strip()
    text = message + (" " + note if note else "")

    if what in ("notify", "push"):
        try:
            import push
            if push.configured():
                push.send(text, title="ARC trigger", tags="bell")
        except Exception:
            pass
    if what == "remind":
        try:
            import extras as _e
            _e.set_reminder(note or message, 60)
        except Exception:
            pass
    # "say" and "notify" both leave it for the browser to speak, via due().


# --- the loop ---------------------------------------------------------------

_pending: list = []

# What is waiting for the browser to come and collect it. Bounded, because
# nothing guarantees a browser ever does: ARC can run for weeks with nobody's
# tab open, and forty rules firing once an hour would otherwise grow this list
# for ever. The oldest go — by the time there are fifty unread, the first one
# is days stale and the recent ones are what matter.
MAX_PENDING = 50


def evaluate() -> None:
    """Called from the 30-second monitor loop. Never raises.

    Quotes are fetched OUTSIDE the lock — the same rule alerts.py follows, and
    for the same reason: holding a lock across a network call freezes every
    other reader of the file for as long as Yahoo takes to answer.
    """
    rules = _load()
    if not rules:
        return
    now = time.time()
    results = []
    for r in rules:
        if not r.get("on", True):
            continue
        if now - float(r.get("fired_at") or 0) < COOLDOWN:
            continue
        fn = CONDITIONS.get(r.get("kind"))
        if not fn:
            continue
        try:
            hit, msg = fn(r)
        except Exception:
            continue
        if hit:
            results.append((r.get("id"), msg))

    if not results:
        return
    with _lock:
        rules = _load()
        by_id = {r.get("id"): r for r in rules}
        for rid, msg in results:
            r = by_id.get(rid)
            if not r:
                continue
            r["fired_at"] = now
            r["fires"] = int(r.get("fires") or 0) + 1
            if r.get("once"):
                r["on"] = False
            _pending.append(msg)
            del _pending[:-MAX_PENDING]
            _do(r, msg)
        _save(rules)


def due() -> list:
    """Anything fired since the browser last asked. Delivered once."""
    with _lock:
        out, _pending[:] = list(_pending), []
        return out


# --- tools ------------------------------------------------------------------

def _describe(r) -> str:
    kind = r.get("kind")
    op = r.get("op") or ""
    if kind == "price":
        head = "%s %s %s" % (r.get("symbol"), op, r.get("value"))
    elif kind == "move":
        head = "%s moves %s%s%%" % (r.get("symbol"),
                                    "" if op == "either" else op + " ",
                                    r.get("value"))
    else:
        head = "daily spend above $%s" % r.get("value")
    tail = " — %s" % r["note"] if r.get("note") else ""
    state = "" if r.get("on", True) else " (off)"
    return "%s: if %s, %s%s%s" % (r.get("id"), head, r.get("action", "notify"),
                                  tail, state)


def add_trigger(kind: str = "price", symbol: str = "", op: str = "below",
                value: float = 0, action: str = "notify", note: str = "",
                once: bool = False) -> str:
    """Set a standing rule."""
    kind = (kind or "price").strip().lower()
    if kind not in CONDITIONS:
        return "I can watch a price, a percentage move, or the daily spend."
    action = (action or "notify").strip().lower()
    if action not in ACTIONS:
        # Naming what IS possible matters more than refusing: the request that
        # gets here is usually "sell it", and the useful answer is what ARC can
        # do instead.
        return ("I can tell you, push it to your phone, or set a reminder. I "
                "cannot buy or sell anything — there is no brokerage connected "
                "to me, and there deliberately isn't one.")
    if kind in ("price", "move") and not (symbol or "").strip():
        return "Which stock or ticker?"
    try:
        value = float(value)
    except Exception:
        return "What number should I watch for?"
    if value <= 0:
        return "That needs a number above zero."

    with _lock:
        rules = _load()
        if len(rules) >= MAX_RULES:
            return "You already have %d rules — clear one first." % MAX_RULES
        r = {"id": _next_id(rules), "kind": kind, "symbol": (symbol or "").upper(),
             "op": (op or "below").strip().lower(), "value": value,
             "action": action, "note": (note or "").strip(),
             "once": bool(once), "on": True, "fired_at": 0, "fires": 0,
             "made": time.time()}
        rules.append(r)
        _save(rules)
    return "Set. " + _describe(r)


def list_triggers() -> str:
    rules = _load()
    if not rules:
        return "No standing rules."
    return "%d rule%s. " % (len(rules), "" if len(rules) == 1 else "s") + \
           "  ".join(_describe(r) for r in rules)


def clear_trigger(which: str = "") -> str:
    w = (which or "").strip().lower()
    with _lock:
        rules = _load()
        if not rules:
            return "Nothing to clear."
        if w in ("all", "everything"):
            _save([])
            return "Cleared all %d." % len(rules)
        keep = [r for r in rules
                if r.get("id") != w
                and w not in (r.get("symbol") or "").lower()
                and w not in (r.get("note") or "").lower()]
        if len(keep) == len(rules):
            return "Nothing matched '%s'." % which
        _save(keep)
        return "Cleared %d." % (len(rules) - len(keep))


TOOLS = [
    {"name": "add_trigger",
     "description": (
         "Set a standing rule that ARC checks in the background: 'tell me if Tesla "
         "drops below 200', 'let me know if Nvidia moves more than 3 percent', "
         "'warn me if I've spent more than 5 dollars today'. It NOTIFIES — it "
         "cannot buy, sell, trade or top up any account, and you must say so "
         "plainly rather than implying an order was placed."),
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "price, move, or spend"},
         "symbol": {"type": "string", "description": "Ticker or company, for price/move"},
         "op": {"type": "string", "description": "above, below, or either (move only)"},
         "value": {"type": "number", "description": "The price, the percentage, or the dollars"},
         "action": {"type": "string", "description": "notify, push, say or remind"},
         "note": {"type": "string", "description": "What to say when it fires"},
         "once": {"type": "boolean", "description": "Switch itself off after firing once"}},
         "required": ["kind", "value"]}},

    {"name": "list_triggers",
     "description": "Read back the standing rules and whether they have fired.",
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "clear_trigger",
     "description": "Remove a standing rule by id, ticker, or 'all'.",
     "input_schema": {"type": "object", "properties": {
         "which": {"type": "string", "description": "id, ticker, or 'all'"}},
         "required": ["which"]}},
]

_DISPATCH = {"add_trigger": add_trigger, "list_triggers": list_triggers,
             "clear_trigger": clear_trigger}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return "No such tool: %s" % name, True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return "Wrong arguments for %s: %s" % (name, e), True
    except Exception as e:
        return "Could not set that rule: %s" % e, True
