#!/usr/bin/env python3
"""What ARC actually did, day by day — the record behind Arc Watch.

There was no record. The spend meter was a dictionary in memory that reset
every time the process restarted and only ever held today, so the honest answer
to "what did I spend on the nineteenth?" was that nobody knew, including ARC.
For something that costs real money per sentence, that is a strange gap.

So: one row per day, written to disk, kept for a bit over a year.

Two decisions worth stating, because both could reasonably have gone the other
way.

WHAT IS NOT KEPT. No prompts, no replies, no transcripts, nothing anybody said.
The counts and the costs, the names of the tools used, and how often things went
wrong. A usage log that quietly became a conversation log would be a much more
sensitive file than this one, sitting in the same folder with the same
protection, and nobody would have decided to make it.

WHY ROLLUPS AND NOT EVENTS. A row per turn would be more flexible and would grow
without bound on a product that polls every thirty seconds. Days are the unit
people actually ask about — "yesterday", "last week", "this month" — and a
year's worth of them is under a hundred kilobytes.
"""

import io
import json
import os
import threading
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STATS = DATA_DIR / "usage.json"

# A bit over a year, so "this time last year" is always answerable and the file
# still cannot grow without limit.
KEEP_DAYS = 400

_lock = threading.RLock()
_days: dict = {}
_loaded = False
_dirty = False
_last_write = 0.0
WRITE_EVERY = 20.0          # seconds; a busy minute is one write, not forty


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _blank() -> dict:
    # "spend" is money per model, alongside "models" which is turns per model.
    # Both are needed and neither implies the other: Opus is five times Haiku's
    # rate and Sonnet three times it, so a day can be nine tenths Haiku turns
    # and nine tenths Opus money. Counting turns alone says the opposite.
    #
    # "saved" is what caching kept, worked out at record time from the rate of
    # the model that actually answered. It used to be reconstructed afterwards
    # at a flat $3 — which quietly credited every Haiku turn with three times
    # the saving it earned.
    return {"turns": 0, "tok_in": 0, "tok_out": 0,
            "tok_cache_read": 0, "tok_cache_write": 0,
            "cost": 0.0, "saved": 0.0, "tools": {}, "models": {}, "spend": {},
            "errors": 0, "refusals": 0, "searches": 0,
            "alarms": 0, "alerts": 0, "voice_chars": 0}


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(STATS.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _days.update({k: v for k, v in data.items() if isinstance(v, dict)})
    except FileNotFoundError:
        pass
    except Exception:
        # A damaged file must not take the server down over a usage counter.
        # selfheal will notice it and put back the last good copy.
        _days.clear()


def _write(force: bool = False):
    global _dirty, _last_write
    now = time.time()
    if not force and now - _last_write < WRITE_EVERY:
        return
    _last_write = now
    _dirty = False
    try:
        for old in sorted(_days)[:-KEEP_DAYS]:
            _days.pop(old, None)
        STATS.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_days, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATS)      # atomic; never a half-written file
    except Exception:
        pass


def flush():
    """Write now. Called at shutdown, so the last few turns are not lost."""
    with _lock:
        _load()
        if _dirty:
            _write(force=True)


def day(stamp: str = "") -> dict:
    """One day's figures, always with every field present.

    A day recorded before a counter existed has no key for it, and the reader
    should not have to know which release each field arrived in. Filling the
    gaps from a blank record means an old day reads as a zero rather than as a
    missing key — which is what it actually was.
    """
    with _lock:
        _load()
        return {**_blank(), **(_days.get(stamp or _today()) or {})}


def record(*, tok_in=0, tok_out=0, cache_read=0, cache_write=0, cost=0.0,
           saved=0.0, tools=(), model="", error=False, refusal=False,
           searched=False, turn=True) -> None:
    """One completed turn. Never raises — a counter must not break a reply."""
    global _dirty
    try:
        with _lock:
            _load()
            d = _days.setdefault(_today(), _blank())
            for k, v in (("turns", 1 if turn else 0), ("tok_in", tok_in),
                         ("tok_out", tok_out), ("tok_cache_read", cache_read),
                         ("tok_cache_write", cache_write),
                         ("errors", 1 if error else 0),
                         ("refusals", 1 if refusal else 0),
                         ("searches", 1 if searched else 0)):
                d[k] = d.get(k, 0) + v
            d["cost"] = round(d.get("cost", 0.0) + (cost or 0.0), 6)
            d["saved"] = round(d.get("saved", 0.0) + (saved or 0.0), 6)
            for t in tools or ():
                d["tools"][t] = d["tools"].get(t, 0) + 1
            if model:
                d["models"][model] = d["models"].get(model, 0) + 1
                # setdefault, because a day recorded before this existed has
                # no "spend" key at all and reading it back must not throw.
                spend = d.setdefault("spend", {})
                spend[model] = round(spend.get(model, 0.0) + (cost or 0.0), 6)
            _dirty = True
            _write()
    except Exception:
        pass


def bump(field: str, n: int = 1) -> None:
    """Count something that is not a turn — an alarm going off, a voice line."""
    global _dirty
    try:
        with _lock:
            _load()
            d = _days.setdefault(_today(), _blank())
            d[field] = d.get(field, 0) + n
            _dirty = True
            _write()
    except Exception:
        pass


# --- reading it back --------------------------------------------------------

def _range(days: int) -> list:
    end = date.today()
    return [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def series(days: int = 30) -> list:
    """One entry per day, oldest first, with the empty days present.

    Gaps matter: a chart that silently skips the days ARC was switched off
    makes a fortnight look like a week and a quiet spell look like nothing
    happened.
    """
    with _lock:
        _load()
        # _blank() first for the same reason as day(): a row from an older
        # release is missing whatever was added since, and a chart reading it
        # should see a zero, not an absent key.
        return [{"date": s, **_blank(), **(_days.get(s) or {})}
                for s in _range(days)]


def totals(days: int = 30) -> dict:
    rows = series(days)
    out = _blank()
    tools: Counter = Counter()
    models: Counter = Counter()
    spend: Counter = Counter()
    for r in rows:
        for k in out:
            if k in ("tools", "models", "spend"):
                continue
            out[k] = (round(out[k] + r.get(k, 0), 6) if k in ("cost", "saved")
                      else out[k] + r.get(k, 0))
        tools.update(r.get("tools") or {})
        models.update(r.get("models") or {})
        spend.update(r.get("spend") or {})
    out["tools"] = dict(tools.most_common())
    out["models"] = dict(models.most_common())
    out["spend"] = {m: round(v, 6) for m, v in spend.most_common()}
    out["days"] = days
    out["active_days"] = sum(1 for r in rows if r.get("turns"))
    return out


def summary(days: int = 7) -> str:
    """The same figures as a sentence, for when it is asked out loud."""
    t = totals(days)
    if not t["turns"]:
        return ("Nothing recorded in the last %d days — either I was switched "
                "off or nobody said anything." % days)
    span = "today" if days == 1 else "over the last %d days" % days
    saved = ""
    if t.get("saved"):
        # Worked out per turn at the answering model's own rate, not
        # reconstructed here at one flat price. The point of caching, in money.
        saved = " Caching saved about $%.2f of that." % t["saved"]
    top = list(t["tools"].items())[:3]
    tools = ("  Most used: "
             + ", ".join("%s (%d)" % (n, c) for n, c in top)) if top else ""
    return ("%s: %d turn%s, $%.2f, across %d day%s.%s%s"
            % (span.capitalize(), t["turns"], "" if t["turns"] == 1 else "s",
               t["cost"], t["active_days"], "" if t["active_days"] == 1 else "s",
               saved, tools))


# --- tool surface -----------------------------------------------------------

def connected() -> bool:
    return True


_WORDS = {"today": 1, "yesterday": 2, "week": 7, "this week": 7, "last week": 7,
          "fortnight": 14, "two weeks": 14, "month": 30, "this month": 30,
          "last month": 30, "quarter": 90, "3 months": 90, "year": 365,
          "all": 400, "everything": 400, "ever": 400}


def usage_report(period: str = "week") -> str:
    p = (period or "week").strip().lower()
    days = _WORDS.get(p)
    if days is None:
        digits = "".join(c for c in p if c.isdigit())
        days = max(1, min(400, int(digits))) if digits else 7
    if p == "yesterday":
        d = day((date.today() - timedelta(days=1)).isoformat())
        if not d["turns"]:
            return "Nothing recorded for yesterday."
        return ("Yesterday: %d turns, $%.2f."
                % (d["turns"], d["cost"]))
    return summary(days)


TOOLS = [
    {"name": "usage_report",
     "description": (
         "What ARC has cost and how much it has been used, over a period. Use for "
         "'what did I spend this week', 'how much have you cost me', 'how many "
         "questions did I ask', 'what are my usage stats', 'what did I spend "
         "yesterday'. Figures only — no conversations are stored."),
     "input_schema": {"type": "object", "properties": {
         "period": {"type": "string", "description":
                    "today, yesterday, week, month, quarter, year, or a number of days"}}}},
]

_DISPATCH = {"usage_report": usage_report}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return "No such tool: %s" % name, True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return "Wrong arguments for %s: %s" % (name, e), True
    except Exception as e:
        return "Could not read the usage record: %s" % e, True
