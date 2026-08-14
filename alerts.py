#!/usr/bin/env python3
"""
Market price alerts for ARC.

"Bella, tell me when Nvidia hits 200" → ARC watches that ticker in the
background and, the moment it crosses, speaks it in the browser AND pushes it to
your phone (if ntfy is set up), even with no tab open.

How it fits together:
  · Alerts live in price_alerts.json (gitignored — it's your watchlist).
  · The server's 30-second monitor loop calls evaluate(), which fetches a live
    quote for each un-triggered alert and marks the ones that have crossed.
  · pending_push()   feeds the phone-push loop (delivered once to the phone).
  · pending_browser() feeds the /api/alerts/due poll (spoken once in the tab).
    Two independent flags, like reminders, so an alert can be both spoken and
    pushed without either swallowing the other.

Design note: like reminders, this is a single owner-side watchlist stored on the
server (it has to be, to fire when the browser is closed). Pushes only ever reach
the owner's ntfy topic, so a signed-in visitor can never be paged by it.
"""

import os
import json
import time
import threading
import itertools
from pathlib import Path

import extras   # yahoo_quote / yahoo_search — the same quote source the widget uses

ROOT = Path(__file__).parent.resolve()
# Per-instance data dir (see run.py); a second Bella keeps its own watchlist.
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
ALERTS_FILE = DATA_DIR / "price_alerts.json"

_ids = itertools.count(1)

# The monitor loop (evaluate/pending_push, on worker threads) and the browser
# poll (pending_browser, on the event-loop thread) both read-modify-write this
# file. Without a lock the last save wins and can clobber a just-set 'pushed' or
# 'triggered' flag — leading to a duplicate phone push or a dropped browser
# alert. Guard every load-mutate-save sequence with this. NEVER hold it during a
# network fetch (that would freeze the event loop); fetch first, lock to apply.
_LOCK = threading.RLock()


def connected() -> bool:
    # Always available — setting an alert works even before ntfy is configured
    # (it will still fire in the browser); phone delivery lights up once ntfy is.
    return True


# --- storage ---------------------------------------------------------------

def _load():
    try:
        return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items):
    # Atomic replace so a concurrent reader never sees a half-written file.
    tmp = ALERTS_FILE.with_name(ALERTS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ALERTS_FILE)


def _next_id() -> str:
    # Keep ids unique across a run without colliding with any already on disk.
    existing = {a.get("id") for a in _load()}
    while True:
        cid = f"al{next(_ids)}"
        if cid not in existing:
            return cid


# --- direction parsing -----------------------------------------------------

_ABOVE = {"above", "over", "hits", "hit", "reaches", "reach", "up", "crosses",
          "cross", "at_or_above", ">=", ">", "rises", "rise", "tops"}
_BELOW = {"below", "under", "drops", "drop", "falls", "fall", "down", "dips",
          "dip", "at_or_below", "<=", "<", "beneath", "sinks"}


def _norm_dir(direction: str, current, target) -> str:
    d = (direction or "").strip().lower()
    if d in _ABOVE:
        return "above"
    if d in _BELOW:
        return "below"
    # No/unknown direction: infer from where the price is now relative to target.
    if current is not None and target is not None:
        return "above" if target >= current else "below"
    return "above"


def _met(direction: str, price: float, target: float) -> bool:
    return price >= target if direction == "above" else price <= target


def _fmt_price(p):
    if p is None:
        return "?"
    return f"{round(p):,}" if p >= 1000 else f"{round(p * 100) / 100:g}"


def _describe(a: dict) -> str:
    sym = str(a["symbol"]).replace("-USD", "")
    arrow = "≥" if a["direction"] == "above" else "≤"
    line = f"{sym} {arrow} {_fmt_price(a['target'])}"
    note = (a.get("note") or "").strip()
    return f"{line} — {note}" if note else line


# --- tools -----------------------------------------------------------------

def set_price_alert(symbol: str = "", direction: str = "", price=None, note: str = "") -> str:
    """Create a price alert. Resolves a name/ticker, records the threshold, and
    tells the user if it's already satisfied."""
    name = (symbol or "").strip()
    if not name:
        return "Which stock or coin should I watch?"
    try:
        target = float(str(price).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return "Give me a price to watch for (a number)."

    sym = extras.yahoo_search(name) or name.upper()
    q = extras.yahoo_quote(sym)
    if not q:
        return f"I couldn't find a market price for '{name}'. Check the name or ticker."
    current = q["price"]
    d = _norm_dir(direction, current, target)
    disp = str(sym).replace("-USD", "")

    if _met(d, current, target):
        # Already true — don't arm a perpetually-fired alert; just report it.
        rel = "above" if d == "above" else "below"
        return (f"{disp} is already {rel} {_fmt_price(target)} — it's at "
                f"{_fmt_price(current)} right now, so there's nothing to wait for.")

    # The yahoo lookups above are done; only the load-append-save touches shared
    # state, so that is all the lock needs to cover.
    with _LOCK:
        item = {"id": _next_id(), "symbol": sym, "direction": d, "target": target,
                "note": (note or "").strip(), "created": time.time(),
                "created_price": current, "triggered": False,
                "triggered_at": None, "triggered_price": None,
                "pushed": False, "delivered": False}
        items = _load()
        items.append(item)
        _save(items)
    arrow = "rises to" if d == "above" else "drops to"
    return (f"Watching {disp} — I'll alert you when it {arrow} {_fmt_price(target)} "
            f"(it's at {_fmt_price(current)} now).")


def list_price_alerts() -> str:
    """Show the active (un-triggered) alerts."""
    with _LOCK:
        items = [a for a in _load() if not a.get("triggered")]
    if not items:
        return "No price alerts set."
    lines = "\n".join(f"  · {_describe(a)}  (now watching)" for a in items)
    return f"{len(items)} price alert(s):\n{lines}"


def clear_price_alert(which: str = "") -> str:
    """Remove alerts by id, by ticker/name, or 'all'."""
    w = (which or "").strip().lower()
    is_all = w in ("all", "everything", "*", "")
    # Resolve the name to a ticker OUTSIDE the lock (it hits the network); the
    # "all" path needs no resolution.
    resolved = "" if is_all else (extras.yahoo_search(w) or "").upper()
    with _LOCK:
        items = _load()
        if not items:
            return "There are no price alerts to clear."
        if is_all:
            _save([])
            return "Cleared all price alerts."
        kept, removed = [], []
        for a in items:
            sym = str(a["symbol"]).upper()
            bare = sym.replace("-USD", "")
            if (a.get("id", "").lower() == w or sym == w.upper() or bare == w.upper()
                    or (resolved and sym == resolved)):
                removed.append(a)
            else:
                kept.append(a)
        if not removed:
            return f"I couldn't find a price alert matching '{which}'."
        _save(kept)
    return "Removed: " + "; ".join(_describe(a) for a in removed) + "."


# --- background evaluation (called by run.py's monitor loop) ---------------

def evaluate():
    """Fetch a live quote for every un-triggered alert and mark the ones that
    have crossed. Network work happens here, on the server's 30s loop — the
    readers below are cheap and do no fetching.

    The lock is held only to snapshot and, later, to apply — NEVER across the
    quote fetches, or a browser poll (pending_browser on the event loop) would
    block for the whole network round-trip."""
    with _LOCK:
        pending = [dict(a) for a in _load() if not a.get("triggered")]
    if not pending:
        return

    crossed = {}   # id -> price at crossing
    for a in pending:
        q = extras.yahoo_quote(a["symbol"])
        if q and _met(a["direction"], q["price"], a["target"]):
            crossed[a["id"]] = q["price"]
    if not crossed:
        return

    with _LOCK:
        items = _load()
        changed = False
        for a in items:
            if a.get("id") in crossed and not a.get("triggered"):
                a["triggered"] = True
                a["triggered_at"] = time.time()
                a["triggered_price"] = crossed[a["id"]]
                changed = True
        if changed:
            _save(items)


def _message(a: dict) -> str:
    # Spoken aloud, so keep it words-only (no ≥/≤ symbols that TTS mangles).
    sym = str(a["symbol"]).replace("-USD", "")
    hit = "hit" if a["direction"] == "above" else "dropped to"
    msg = (f"{sym} {hit} {_fmt_price(a['target'])} — "
           f"now {_fmt_price(a.get('triggered_price'))}.")
    note = (a.get("note") or "").strip()
    return f"{msg} {note}" if note else msg


def pending_push():
    """Triggered alerts not yet sent to the phone. Marks them pushed."""
    with _LOCK:
        items = _load()
        out, changed = [], False
        for a in items:
            if a.get("triggered") and not a.get("pushed"):
                a["pushed"] = True
                changed = True
                out.append(_message(a))
        if changed:
            _save(items)
    return out


def pending_browser():
    """Triggered alerts not yet spoken in the browser. Marks them delivered.
    Cheap — no network — so the client can poll it often."""
    with _LOCK:
        items = _load()
        out, changed = [], False
        for a in items:
            if a.get("triggered") and not a.get("delivered"):
                a["delivered"] = True
                changed = True
                out.append(_message(a))
        if changed:
            _save(items)
    return out


# --- wire format -----------------------------------------------------------

TOOLS = [
    {"name": "set_price_alert",
     "description": ("Watch a stock or crypto and alert the user when it crosses a price. Use for "
                     "'tell me when NVDA hits 200', 'alert me if bitcoin drops below 60k', "
                     "'let me know when Apple goes over 310'. 'symbol' is a name or ticker; "
                     "'direction' is above/below (optional — inferred from the current price if "
                     "omitted); 'price' is the threshold; 'note' is an optional reminder of why."),
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string"},
         "direction": {"type": "string", "description": "'above' or 'below' (optional)"},
         "price": {"type": "number"},
         "note": {"type": "string"}},
         "required": ["symbol", "price"]}},
    {"name": "list_price_alerts",
     "description": "List the active market price alerts the user has set.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "clear_price_alert",
     "description": ("Remove a price alert. 'which' can be a ticker/name (e.g. 'NVDA', 'bitcoin'), "
                     "an alert id, or 'all' to clear every alert."),
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}},
                      "required": ["which"]}},
]

_DISPATCH = {"set_price_alert": set_price_alert,
             "list_price_alerts": list_price_alerts,
             "clear_price_alert": clear_price_alert}


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
