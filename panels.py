#!/usr/bin/env python3
"""Panels the user invents, built by ARC.

"Make me a panel for the Tokyo trip." "Put a countdown to my exam on screen."
"Give me a card with my flight number and the hotel." The HUD's own cards —
weather, markets, the plan — were each written by hand; this lets the user
describe one and have it appear.

WHY THIS IS A FORM AND NOT CODE, which is the whole design:

ARC could have been asked to write HTML for a panel, and it would have worked
on the first try and been a hole in the app for ever after. Everything ARC
reads — an email, a Telegram message, a web page, a calendar invite written by
somebody else — can reach the model, and a model that can write markup into
this page can be talked into writing a script tag by anything it reads. The
page holds a live session to the owner's mail, calendar and machine.

So a panel is DATA: a title, and up to eight rows of label and value. The page
renders it with createElement and textContent, exactly as it renders the plan,
and nothing a panel contains can ever be markup. The imagination is in what
the user asks for and how ARC fills it in — which is where it belongs — not in
what tags reach the DOM.

The one concession to liveness is `live`, an ALLOW-LIST of things the page
already knows how to fetch for itself: a ticker price, a countdown to a date,
the clock. A row asking for anything else keeps its text and loses the binding,
because an unknown binding must not become a request the page makes on behalf
of a sentence somebody emailed the owner.

Panels are per account, like notes and the plan (see whose.py): a guest who
asks for a panel gets their own, and cannot see or overwrite the owner's.
"""

import json
import os
import re
import time
from pathlib import Path

import whose

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PANELS = DATA_DIR / "panels.json"

# Small on purpose. The HUD is a screen with a face in the middle of it, not a
# dashboard; six cards of eight rows is already more than fits beside the
# weather and the markets without covering ARC up.
MAX_PANELS = 6
MAX_ITEMS = 8
MAX_TITLE = 40
MAX_LABEL = 24
MAX_VALUE = 80

# What a row may be bound to. Anything else is dropped to plain text.
#   ticker:NVDA        the live price, from the quote proxy the panel already has
#   countdown:2026-11-03   how long until that date (or how long since)
#   clock              the time, ticking
LIVE = re.compile(r"^(?:ticker:[A-Za-z0-9.\-^=]{1,12}|countdown:\d{4}-\d{2}-\d{2}|clock)$")


def connected() -> bool:
    return True


def _clean(s, limit: int) -> str:
    """Plain text, trimmed. Control characters go — including the newlines that
    would otherwise let one row draw itself as several — and so do the angle
    brackets, which the renderer cannot execute but which look like a mistake
    on screen and invite somebody to "fix" the renderer to honour them."""
    s = str(s if s is not None else "")
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s).replace("<", "(").replace(">", ")")
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _raw() -> object:
    try:
        return json.loads(PANELS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load() -> list:
    return whose.mine(_raw())


def _save(items) -> None:
    try:
        blob = whose.replace(_raw(), items[-MAX_PANELS:])
        tmp = PANELS.with_name(PANELS.name + ".tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, PANELS)
    except Exception:
        pass


def _tidy(items) -> list:
    """Whatever the model sent, as rows this page can draw.

    Deliberately forgiving about SHAPE and strict about CONTENT: a list of
    strings, a list of dicts, or one dict of label -> value all arrive here
    depending on how the sentence was phrased, and refusing them would mean ARC
    apologising to the user about JSON. What is not forgiven is the value of
    `live`, which is checked against the allow-list above.
    """
    out = []
    if isinstance(items, dict):
        items = [{"label": k, "value": v} for k, v in items.items()]
    if isinstance(items, str):
        items = [items]
    for it in (items or [])[:MAX_ITEMS]:
        if isinstance(it, str):
            # "Flight: BA15" written as one line, which is how it is said.
            label, _, value = it.partition(":")
            row = {"label": _clean(label, MAX_LABEL), "value": _clean(value, MAX_VALUE)}
            if not row["value"]:
                row = {"label": "", "value": _clean(it, MAX_VALUE)}
        elif isinstance(it, dict):
            row = {"label": _clean(it.get("label") or it.get("name"), MAX_LABEL),
                   "value": _clean(it.get("value") or it.get("text"), MAX_VALUE)}
            live = _clean(it.get("live"), 32)
            if live and LIVE.match(live):
                row["live"] = live
        else:
            continue
        if row["label"] or row["value"] or row.get("live"):
            out.append(row)
    return out


def make_panel(title: str = "", items=None) -> str:
    """Create or replace a panel on the user's screen."""
    t = _clean(title, MAX_TITLE)
    if not t:
        return "What should the panel be called?"
    rows = _tidy(items)
    if not rows:
        return (f"I need something to put in '{t}' — a few label and value pairs, "
                "or lines like 'Flight: BA15'.")
    mine = _load()
    # Same title = the same panel, updated. Otherwise asking ARC to "change the
    # trip panel" would leave two of them and no way to say which.
    kept = [p for p in mine if (p.get("title") or "").lower() != t.lower()]
    replaced = len(kept) != len(mine)
    if not replaced and len(kept) >= MAX_PANELS:
        return (f"There are already {MAX_PANELS} panels, which is as many as fit. "
                "Remove one first — tell me which.")
    kept.append({"id": "p%d" % int(time.time() * 1000), "title": t,
                 "items": rows, "made": time.time()})
    _save(kept)
    what = "Updated" if replaced else "Put"
    where = "" if replaced else " on your screen"
    return (f"{what} '{t}'{where}, sir — {len(rows)} "
            f"{'line' if len(rows) == 1 else 'lines'}. They can be dragged and "
            "resized like the other cards.")


def list_panels() -> str:
    mine = _load()
    if not mine:
        return "No panels on screen. Describe one and I'll build it."
    out = []
    for p in mine:
        rows = p.get("items") or []
        out.append("• %s — %d %s" % (p.get("title", "?"), len(rows),
                                     "line" if len(rows) == 1 else "lines"))
    return "\n".join(out)


def remove_panel(which: str = "") -> str:
    w = _clean(which, MAX_TITLE).lower()
    if not w:
        return "Which panel should I take down?"
    mine = _load()
    if not mine:
        return "There are no panels to remove."
    if w == "all":
        _save([])
        return f"Cleared {len(mine)} {'panel' if len(mine) == 1 else 'panels'}, sir."
    kept = [p for p in mine if w not in (p.get("title") or "").lower()]
    if len(kept) == len(mine):
        return "Nothing matched '%s'. On screen: %s." % (
            which, ", ".join((p.get("title") or "?") for p in mine))
    _save(kept)
    return f"Taken down, sir. {len(kept)} left."


def panels_for_screen() -> list:
    """What the HUD polls for. Shape is fixed here rather than in the route so
    the page can trust it: every row has a label and a value, and `live` is
    either absent or one of the allow-listed kinds."""
    out = []
    for p in _load()[:MAX_PANELS]:
        rows = []
        for r in (p.get("items") or [])[:MAX_ITEMS]:
            row = {"label": _clean(r.get("label"), MAX_LABEL),
                   "value": _clean(r.get("value"), MAX_VALUE)}
            live = _clean(r.get("live"), 32)
            if live and LIVE.match(live):
                row["live"] = live
            rows.append(row)
        out.append({"id": p.get("id") or "p0", "title": _clean(p.get("title"), MAX_TITLE),
                    "items": rows})
    return out


TOOLS = [
    {"name": "make_panel",
     "description": (
         "Build a panel (a small card) on the user's screen, or replace one with the same "
         "title. Use when they ask for a panel, card, dashboard, tracker or countdown of "
         "their own — 'make me a panel for the trip', 'put a countdown to my exam on "
         "screen', 'give me a card with the flight details'. 'items' is up to 8 rows, each "
         "with a 'label' and a 'value'. A row may also carry 'live' to show something that "
         "updates by itself: 'ticker:NVDA' for a live price, 'countdown:2026-11-03' for "
         "time remaining to a date, or 'clock' for the time. Anything else in 'live' is "
         "ignored and the row stays plain text. Panels are the user's own — a guest gets "
         "their own and never sees anybody else's."),
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"},
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "label": {"type": "string"},
             "value": {"type": "string"},
             "live": {"type": "string"}}}}},
         "required": ["title", "items"]}},
    {"name": "list_panels",
     "description": "What panels are on the user's screen. Use for 'what panels do I have'.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "remove_panel",
     "description": ("Take a panel off the screen by its title, or 'all' for every one. Use "
                     "for 'remove the trip panel', 'clear my panels'."),
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}},
                      "required": ["which"]}},
]

_DISPATCH = {"make_panel": make_panel, "list_panels": list_panels,
             "remove_panel": remove_panel}


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
