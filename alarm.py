#!/usr/bin/env python3
"""
Wake-up alarms for ARC.

"Bella, wake me at seven on weekdays" → the server holds the alarm, and at
07:00 every Monday to Friday it rings in the browser AND pushes to the phone at
urgent priority, until somebody dismisses or snoozes it.

Why this is not the [[alarm]] marker the page already has:
  The marker alarm lives in one browser tab's localStorage, fires only while
  that tab is open, and says one sentence. That is a nudge for someone already
  awake. Waking a sleeping person needs three things the marker cannot give:
  it must survive the tab being closed (so the state lives here, on the
  server); it must repeat on a schedule (so it is still set on Tuesday); and it
  must keep making noise until acknowledged, because one chirp at 7am is slept
  straight through.

How it fits together:
  · Alarms live in alarms.json (gitignored — it's when you get up).
  · The server's 30-second monitor loop calls evaluate(), which starts any
    alarm whose moment has come and works out when it should next go off.
  · pending_push()   feeds the phone-push loop. Priority 'urgent' so it breaks
    through the phone's silent mode — that is the whole point of an alarm.
  · ringing()        feeds the /api/alarms/due poll. Unlike reminders and
    price alerts, reading this does NOT consume it: a ringing alarm keeps
    being reported until it is dismissed, so reloading the page (or opening it
    on a second device) does not silence one. Delivery-once is right for a
    message; wrong for a bell.
  · snooze_alarm() / dismiss_alarm() stop the noise, from a button or by voice.

Like reminders and price alerts, this is a single owner-side list — it has to
be server-side to fire with no browser open, and pushes only ever reach the
owner's ntfy topic, so a signed-in guest can never be woken by it.
"""

import os
import re
import json
import time
import threading
import itertools
import datetime as dt
from pathlib import Path

import push     # only to ASK whether the phone is reachable, never to send

ROOT = Path(__file__).parent.resolve()
# Per-instance data dir (see run.py); a second Bella keeps its own alarms.
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
ALARMS_FILE = DATA_DIR / "alarms.json"

_ids = itertools.count(1)

# The monitor loop (evaluate/pending_push, on worker threads) and the browser
# poll and buttons (ringing/snooze/dismiss, on the event-loop thread) both
# read-modify-write this file. Same discipline as alerts.py: guard every
# load-mutate-save sequence. Nothing here touches the network, so unlike
# alerts.py the lock can simply wrap each operation.
_LOCK = threading.RLock()

# If an alarm's moment passed while ARC was not running, do NOT ring on
# startup — being woken at 09:40 by the 07:00 alarm is worse than not being
# woken at all, and it teaches you to distrust the thing. Past this window the
# occurrence is written off and the alarm is rescheduled.
STALE_AFTER = 10 * 60

# An alarm nobody dismisses stops by itself eventually. Without this, a phone
# left subscribed while its owner is out gets pushed for ever, and a browser
# left open rings into an empty room all day.
RING_FOR = 15 * 60

# How often a ringing alarm is pushed to the phone again. One notification is
# not an alarm — it is a notification, and it is slept through. Re-pushing also
# quietly repairs a push that failed: send() swallows network errors and returns
# False, and with a one-shot flag that alarm would simply never have reached the
# phone at all. At this interval a 15-minute ring is about seven attempts.
REPUSH_EVERY = 120

# What "snooze" means with no number attached.
DEFAULT_SNOOZE_MIN = 9

MAX_LABEL = 60


def connected() -> bool:
    # Always available. Ringing in the browser needs nothing set up; phone
    # delivery lights up on its own once ntfy is configured.
    return True


# --- storage ---------------------------------------------------------------

def _load():
    try:
        items = json.loads(ALARMS_FILE.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _save(items):
    # Atomic replace so a concurrent reader never sees a half-written file.
    tmp = ALARMS_FILE.with_name(ALARMS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ALARMS_FILE)


def _next_id() -> str:
    existing = {a.get("id") for a in _load()}
    while True:
        cid = f"am{next(_ids)}"
        if cid not in existing:
            return cid


# --- when: clock time ------------------------------------------------------

_CLOCK = re.compile(r"^(\d{1,2})(?::|\.|h)?(\d{2})?(am|pm|a\.m\.|p\.m\.)?$")

_WORDS = {"midnight": (0, 0), "noon": (12, 0), "midday": (12, 0)}


def _parse_clock(s: str):
    """'7', '7am', '07:00', '7.30pm', '19:00', 'midnight' -> (hour, minute).

    Also accepts a full ISO datetime, because the model is told elsewhere to
    resolve times to ISO and will sometimes do it here too; only the clock part
    is kept, since an alarm is a time of day, not a date."""
    t = (s or "").strip().lower()
    if not t:
        return None
    if t in _WORDS:
        return _WORDS[t]
    iso = re.match(r"^\d{4}-\d{2}-\d{2}[t ](\d{1,2}):(\d{2})", t)
    if iso:
        h, m = int(iso.group(1)), int(iso.group(2))
        return (h, m) if h < 24 and m < 60 else None
    m = _CLOCK.match(t.replace(" ", ""))
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ap = (m.group(3) or "").replace(".", "")
    if ap == "pm" and hour < 12:
        hour += 12
    if ap == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return (hour, minute)


def _clock_words(hour: int, minute: int) -> str:
    """Spoken back, so no leading zeros and no 24-hour clock."""
    h12 = hour % 12 or 12
    ampm = "am" if hour < 12 else "pm"
    return f"{h12}:{minute:02d}{ampm}" if minute else f"{h12}{ampm}"


# --- when: which days ------------------------------------------------------

_DAY_NUM = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_SPOKEN = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
           "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}

_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
_WEEKENDS = ["sat", "sun"]
_ALL_DAYS = _WEEKDAYS + _WEEKENDS


def _day_key(word: str):
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    if not w:
        return None
    # 'tues', 'thurs', 'thur', 'tuesday' all reduce to the three-letter key.
    for key in _DAY_NUM:
        if w.startswith(key):
            return key
    return None


def _parse_repeat(repeat: str):
    """-> a list of day keys, or None for a one-off.

    Returns False when the text was given but meant nothing recognisable, so
    the caller can say so rather than quietly setting a one-off alarm for a
    user who asked for every weekday."""
    r = (repeat or "").strip().lower()
    if not r or r in ("once", "one off", "one-off", "today", "tomorrow",
                      "no", "none", "just once"):
        return None
    if "weekday" in r or "week day" in r or "workday" in r or "work day" in r:
        return list(_WEEKDAYS)
    if "weekend" in r:
        return list(_WEEKENDS)
    if "every day" in r or "everyday" in r or "daily" in r or r == "all":
        return list(_ALL_DAYS)
    days, seen = [], set()
    for word in re.split(r"[,/&]| and | ", r):
        k = _day_key(word)
        if k and k not in seen:
            seen.add(k)
            days.append(k)
    if not days:
        return False
    return sorted(days, key=lambda k: _DAY_NUM[k])


def _repeat_words(days) -> str:
    if not days:
        return "once"
    s = sorted(days, key=lambda k: _DAY_NUM[k])
    if s == _ALL_DAYS:
        return "every day"
    if s == _WEEKDAYS:
        return "on weekdays"
    if s == _WEEKENDS:
        return "at weekends"
    names = [_SPOKEN[d] for d in s]
    if len(names) == 1:
        return f"every {names[0]}"
    return "every " + ", ".join(names[:-1]) + " and " + names[-1]


def _next_at(hour: int, minute: int, days, after: float) -> float:
    """The first moment strictly after `after` when the clock reads hour:minute
    on an allowed day. Built from local naive datetimes on purpose: an alarm is
    a promise about the wall clock, so when the clocks change, 7am stays 7am."""
    start = dt.datetime.fromtimestamp(after)
    allowed = None if not days else {_DAY_NUM[d] for d in days}
    for i in range(0, 9):
        cand = (start + dt.timedelta(days=i)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        ts = cand.timestamp()
        if ts <= after:
            continue
        if allowed is None or cand.weekday() in allowed:
            return ts
    return after + 86400   # unreachable with a non-empty day set


def _human_until(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return "in under a minute"
    m = int(round(s / 60))
    if m < 60:
        return f"in {m} minutes"
    h, r = divmod(m, 60)
    if h < 24:
        if r == 0:
            return f"in {h} hour{'s' if h != 1 else ''}"
        return f"in {h}h {r}m"
    d = round(h / 24)
    return f"in about {d} day{'s' if d != 1 else ''}"


def _describe(a: dict) -> str:
    when = _clock_words(a["hour"], a["minute"])
    rep = _repeat_words(a.get("days"))
    line = f"{when} ({rep})" if rep != "once" else when
    label = (a.get("label") or "").strip()
    if label:
        line += f" — {label}"
    if not a.get("enabled"):
        line += " [off]"
    return line


# --- tools -----------------------------------------------------------------

def set_alarm(time_of_day: str = "", repeat: str = "", label: str = "") -> str:
    """Set a wake-up alarm at a clock time, optionally repeating."""
    hm = _parse_clock(time_of_day)
    if not hm:
        return ("What time should the alarm go off? Give me a clock time like "
                "7am or 06:30.")
    hour, minute = hm
    days = _parse_repeat(repeat)
    if days is False:
        return (f"I didn't understand '{repeat}' as days of the week. Try 'daily', "
                f"'weekdays', 'weekends', or days like 'mon, wed, fri'.")

    now = time.time()
    fire = _next_at(hour, minute, days, now)
    text = (label or "").strip()[:MAX_LABEL]

    with _LOCK:
        items = _load()
        # Setting the alarm you already have should not leave you with two of
        # them going off a second apart. Same time and same days = the same
        # alarm; update it in place.
        for a in items:
            if (a.get("hour") == hour and a.get("minute") == minute
                    and (a.get("days") or None) == days):
                a.update({"enabled": True, "label": text or a.get("label", ""),
                          "next_at": fire, "ringing": False, "snooze_at": None})
                _save(items)
                return (f"That alarm was already set for {_clock_words(hour, minute)} "
                        f"{_repeat_words(days)} — it's still on, next going off "
                        f"{_human_until(fire - now)}.")
        items.append({"id": _next_id(), "hour": hour, "minute": minute,
                      "days": days, "label": text, "enabled": True,
                      "created": now, "next_at": fire,
                      "ringing": False, "rang_at": None, "stop_at": None,
                      "pushed_at": None, "snooze_at": None})
        _save(items)

    rep = _repeat_words(days)
    tail = "" if rep == "once" else f", {rep}"
    for_what = f" for {text}" if text else ""
    # Say how it will actually reach them, not how it reaches them in theory.
    # An alarm whose only route is a browser tab is a fragile alarm, and the
    # moment to learn that is when it is set — not the morning it fails.
    if push.configured():
        how = "It'll ring here and on your phone."
    else:
        how = ("It'll ring in this tab. Phone alerts aren't set up, so it can "
               "only wake you if this stays open — worth setting those up.")
    return (f"Alarm set for {_clock_words(hour, minute)}{tail}{for_what} — "
            f"that's {_human_until(fire - now)}. {how}")


def list_alarms() -> str:
    """Read back the alarms that are set."""
    now = time.time()
    with _LOCK:
        items = [a for a in _load() if a.get("enabled") or a.get("ringing")]
    if not items:
        return "No alarms set."
    items.sort(key=lambda a: a.get("next_at") or 0)
    lines = []
    for a in items:
        state = "RINGING NOW" if a.get("ringing") else \
            _human_until((a.get("next_at") or now) - now)
        lines.append(f"  · {_describe(a)} — {state}")
    return f"{len(items)} alarm(s):\n" + "\n".join(lines)


def cancel_alarm(which: str = "") -> str:
    """Remove alarms by clock time, by label, by id, or 'all'."""
    w = (which or "").strip().lower()
    with _LOCK:
        items = _load()
        live = [a for a in items if a.get("enabled") or a.get("ringing")]
        if not live:
            return "There are no alarms to cancel."
        if w in ("all", "everything", "*", ""):
            _save([a for a in items if not (a.get("enabled") or a.get("ringing"))])
            return f"Cleared all {len(live)} alarm(s)."
        hm = _parse_clock(w)
        removed, kept = [], []
        for a in items:
            live_now = a.get("enabled") or a.get("ringing")
            match = (a.get("id", "").lower() == w
                     or (hm is not None and (a["hour"], a["minute"]) == hm)
                     or (len(w) > 2 and w in (a.get("label") or "").lower()))
            (removed if (match and live_now) else kept).append(a)
        if not removed:
            return f"I couldn't find an alarm matching '{which}'."
        _save(kept)
    return "Cancelled: " + "; ".join(_describe(a) for a in removed) + "."


def _stop(snooze_minutes=None) -> int:
    """Shared by the voice tools and the HUD's buttons. Returns how many alarms
    were actually stopped, so both can answer honestly when nothing was ringing.

    Snoozing deliberately does not touch next_at: a snoozed 7am weekday alarm is
    still a 7am weekday alarm, it just also goes off at 7:09 today."""
    now = time.time()
    with _LOCK:
        items = _load()
        n = 0
        for a in items:
            if not a.get("ringing"):
                continue
            a["ringing"] = False
            a["stop_at"] = None
            a["snooze_at"] = (now + snooze_minutes * 60) if snooze_minutes else None
            n += 1
        if n:
            _save(items)
    return n


def stop(snooze_minutes=None) -> int:
    """Stop whatever is ringing. The public form of _stop, for run.py's Snooze
    and Stop buttons — the HTTP routes and the voice tools must take exactly
    the same path, or the two ways of turning an alarm off drift apart."""
    return _stop(snooze_minutes=snooze_minutes)


def snooze_alarm(minutes=None) -> str:
    """Silence whatever is ringing and have it come back shortly."""
    try:
        mins = int(float(minutes)) if minutes is not None else DEFAULT_SNOOZE_MIN
    except (TypeError, ValueError):
        mins = DEFAULT_SNOOZE_MIN
    mins = max(1, min(120, mins))
    if not _stop(snooze_minutes=mins):
        return "Nothing is ringing right now."
    return f"Snoozed. I'll wake you again in {mins} minutes."


def dismiss_alarm() -> str:
    """Stop whatever is ringing, for good."""
    if not _stop():
        return "Nothing is ringing right now."
    return "Alarm off. Good morning, sir."


# --- background evaluation (called by run.py's monitor loop) ---------------

def _ring(a: dict, now: float) -> None:
    a["ringing"] = True
    a["rang_at"] = now
    a["stop_at"] = now + RING_FOR
    a["pushed_at"] = None   # a snoozed alarm pushes again when it comes back


def evaluate():
    """Start any alarm whose moment has come, and reschedule repeating ones.

    Cheap and offline — no network, no quotes to fetch — so unlike
    alerts.evaluate() this can simply hold the lock throughout."""
    now = time.time()
    with _LOCK:
        items = _load()
        changed = False
        for a in items:
            # Already ringing: only ever check whether it has rung long enough.
            if a.get("ringing"):
                if a.get("stop_at") and now >= a["stop_at"]:
                    a["ringing"] = False
                    a["stop_at"] = None
                    changed = True
                continue

            snooze = a.get("snooze_at")
            if snooze and now >= snooze:
                a["snooze_at"] = None
                _ring(a, now)
                changed = True
                continue

            if not a.get("enabled"):
                continue
            due = a.get("next_at") or 0
            if now < due:
                continue

            if now - due > STALE_AFTER:
                # Missed entirely — ARC was off. Roll forward silently.
                a["next_at"] = _next_at(a["hour"], a["minute"], a.get("days"), now)
                if not a.get("days"):
                    a["enabled"] = False       # a one-off that never fired is spent
                changed = True
                continue

            _ring(a, now)
            if a.get("days"):
                # Schedule from just past this occurrence, never from `now`, or a
                # daily alarm evaluated a few seconds late lands on today again.
                a["next_at"] = _next_at(a["hour"], a["minute"], a["days"], due + 60)
            else:
                a["enabled"] = False
            changed = True

        if changed:
            # Drop one-offs that are spent, keeping anything still live.
            items = [a for a in items
                     if a.get("enabled") or a.get("ringing") or a.get("snooze_at")]
            _save(items)


def _message(a: dict) -> str:
    label = (a.get("label") or "").strip()
    when = _clock_words(a["hour"], a["minute"])
    if label:
        return f"It's {when} — {label}."
    return f"It's {when}. Time to get up, sir."


def pending_push():
    """Ringing alarms due a push to the phone. Marks when each was sent.

    Returns an alarm REPEATEDLY, every REPUSH_EVERY seconds for as long as it
    is ringing — deliberately unlike reminders and price alerts, which are
    handed over exactly once because they are messages. A phone that buzzes
    once at 7am has told you something; a phone that keeps buzzing wakes you
    up, and that is the entire job."""
    now = time.time()
    with _LOCK:
        items = _load()
        out, changed = [], False
        for a in items:
            if not a.get("ringing"):
                continue
            last = a.get("pushed_at")
            if last and now - last < REPUSH_EVERY:
                continue
            a["pushed_at"] = now
            changed = True
            out.append(_message(a))
        if changed:
            _save(items)
    return out


def ringing():
    """Every alarm currently ringing, for the browser poll.

    Read-only on purpose. Reminders and price alerts are consumed on read
    because they are messages and must arrive exactly once; an alarm is a bell
    and must keep ringing until someone stops it. Consuming here would mean a
    page refresh silences your alarm — and the one morning that matters, it
    would be the refresh that let you sleep in."""
    now = time.time()
    with _LOCK:
        items = _load()
    return [{"id": a["id"], "message": _message(a),
             "label": (a.get("label") or "").strip(),
             "time": _clock_words(a["hour"], a["minute"]),
             "ringing_for": int(now - (a.get("rang_at") or now))}
            for a in items if a.get("ringing")]


def next_up():
    """The soonest alarm, as a couple of short strings for the HUD readout.

    Until this existed an alarm was invisible until it went off: the page's
    TIMERS row only knows about the page's own timers, so "is my alarm set?"
    could only be answered by asking out loud. For an alarm clock that is the
    one question you want answered by glancing at it before you fall asleep."""
    now = time.time()
    with _LOCK:
        items = [a for a in _load() if a.get("enabled")]
    if not items:
        return None
    a = min(items, key=lambda x: x.get("next_at") or 0)
    left = max(0, (a.get("next_at") or now) - now)
    hrs = left / 3600
    if hrs >= 1:
        short = f"{int(hrs)}h"
    else:
        short = f"{max(1, int(round(left / 60)))}m"
    return {"time": _clock_words(a["hour"], a["minute"]),
            "in": short,
            "repeat": _repeat_words(a.get("days")),
            "label": (a.get("label") or "").strip(),
            "count": len(items)}


def armed() -> bool:
    """Is there an alarm the owner is relying on to go off?

    run.py asks this to decide whether a browser that is only polling counts as
    "in use" (see ARC_ALARM_KEEPS_SESSION). Setting an alarm is an explicit
    instruction that this page must still be able to make a noise hours from
    now, so while one is set the idle clock is held off."""
    with _LOCK:
        return any(a.get("enabled") or a.get("ringing") or a.get("snooze_at")
                   for a in _load())


def summary_line() -> str:
    """One line for the model's turn context, so it can answer "is my alarm set"
    without spending a tool call."""
    now = time.time()
    with _LOCK:
        items = [a for a in _load() if a.get("enabled") or a.get("ringing")]
    if not items:
        return ""
    items.sort(key=lambda a: a.get("next_at") or 0)
    parts = []
    for a in items[:6]:
        if a.get("ringing"):
            parts.append(f"{_clock_words(a['hour'], a['minute'])} RINGING NOW")
        else:
            parts.append(f"{_clock_words(a['hour'], a['minute'])} "
                         f"{_repeat_words(a.get('days'))} "
                         f"({_human_until((a.get('next_at') or now) - now)})")
    return "ALARMS SET: " + "; ".join(parts)


# --- wire format -----------------------------------------------------------

TOOLS = [
    {"name": "set_alarm",
     "description": (
         "Set a wake-up alarm at a clock time, which can repeat on chosen days. Use for "
         "'wake me at 7', 'set an alarm for 6:30am', 'wake me at 7 every weekday', "
         "'alarm at 8 on Saturdays'. This is the RIGHT tool for waking someone up: it is "
         "kept on the server, so it survives the page being closed and a restart, it "
         "repeats, and when it goes off it rings continuously here AND pushes to their "
         "phone at urgent priority until they dismiss or snooze it. "
         "'time_of_day' is a clock time like '7am', '06:30', '19:00'. "
         "'repeat' is optional: omit it for a one-off alarm at the next such time, or give "
         "'daily', 'weekdays', 'weekends', or days like 'mon,wed,fri'. "
         "'label' is an optional short reason ('gym', 'flight')."),
     "input_schema": {"type": "object", "properties": {
         "time_of_day": {"type": "string",
                         "description": "Clock time, e.g. '7am' or '06:30'"},
         "repeat": {"type": "string",
                    "description": "'daily' | 'weekdays' | 'weekends' | 'mon,wed,fri' (optional)"},
         "label": {"type": "string"}},
         "required": ["time_of_day"]}},
    {"name": "list_alarms",
     "description": "List the alarms the user has set and when each next goes off.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_alarm",
     "description": ("Turn off an alarm. 'which' can be a clock time ('7am'), words from its "
                     "label ('gym'), or 'all' to clear every alarm."),
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}},
                      "required": ["which"]}},
    {"name": "snooze_alarm",
     "description": ("Silence a ringing alarm and have it come back shortly. Use when the user "
                     "says 'snooze', 'five more minutes', 'not yet'. 'minutes' is optional "
                     f"(default {DEFAULT_SNOOZE_MIN})."),
     "input_schema": {"type": "object", "properties": {"minutes": {"type": "number"}}}},
    {"name": "dismiss_alarm",
     "description": ("Stop a ringing alarm for good. Use when the user says 'stop', "
                     "'turn it off', \"I'm up\", 'alarm off' while one is going."),
     "input_schema": {"type": "object", "properties": {}}},
]

_DISPATCH = {"set_alarm": set_alarm, "list_alarms": list_alarms,
             "cancel_alarm": cancel_alarm, "snooze_alarm": snooze_alarm,
             "dismiss_alarm": dismiss_alarm}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
