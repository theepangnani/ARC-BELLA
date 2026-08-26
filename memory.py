#!/usr/bin/env python3
"""What ARC knows about you — kept once, not once per device.

This was `localStorage` in the browser, which meant it was not one memory at
all. It was one per device. Tell Bella at your desk that you are learning
Python and the phone had never heard of it; correct her on the phone and the
desktop went on believing the old thing. Nobody chose that — it is just what
storing something in a browser does — and it is the sort of fault that reads as
the assistant being scatty rather than as a bug.

So it lives here now, on the server, keyed by the address you signed in with.
One memory, every device, and a guest's is their own rather than a window into
the owner's.

Three things it gained on the way over, because they were impossible before:

  · A DATE on every fact. "He is learning Python" from March should not outrank
    "he finished the course" from August, and without timestamps there is no
    way to tell which came first, let alone to age one out.
  · SEARCH. A hundred and twenty facts is past the point where "what do you
    know about me?" can be answered by reciting all of them.
  · THE ABILITY TO CORRECT IT. There was no way to see a wrong fact, let alone
    remove one — the only control was Forget All, which is not an edit, it is
    a demolition.

Superseding rather than accumulating is the interesting part. A new fact that
looks like an update to an old one REPLACES it, so memory stays a picture of
what is true instead of a pile of everything that ever was.
"""

import io
import json
import os
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "memory.json"

_lock = threading.RLock()

MAX_FACTS = 200          # per account; was 120 in the browser
MAX_LEN = 240            # one fact, in characters

# Whose memory this request is about. Set per-request by run.py, the same
# pattern apply_session_google uses for Google tokens — the alternative is
# threading an address through every call site including the tool dispatcher.
_who = threading.local()


def use(email: str) -> None:
    _who.email = (email or "").strip().lower() or "owner"


def current() -> str:
    return getattr(_who, "email", "") or "owner"


def connected() -> bool:
    return True


# --- storage ----------------------------------------------------------------

def _load() -> dict:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(all_of_it: dict) -> None:
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_name(STORE.name + ".tmp")
        tmp.write_text(json.dumps(all_of_it, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STORE)     # atomic, like every other data file here
    except Exception:
        pass


def _mine(all_of_it: dict) -> list:
    got = all_of_it.get(current())
    return got if isinstance(got, list) else []


def facts(limit: int = 0) -> list:
    """Newest last, which is the order they should be read in."""
    with _lock:
        out = _mine(_load())
    return out[-limit:] if limit else out


def count() -> int:
    return len(facts())


# --- writing ----------------------------------------------------------------

_STOP = {"the", "a", "an", "is", "was", "are", "his", "her", "their", "my",
         "he", "she", "they", "i", "and", "to", "of", "in", "on", "for",
         "it", "that", "this", "with", "has", "have", "had", "at", "as"}


def _keys(text: str) -> set:
    """The words that carry the meaning, for spotting an updated fact.

    Crude on purpose. The job is to notice that "he is learning Python" and
    "he finished the Python course" are about the same thing — not to
    understand either of them.
    """
    words = re.findall(r"[^\W\d_]+", (text or "").lower(), re.UNICODE)
    return {w for w in words if len(w) > 2 and w not in _STOP}


# Deliberately high, and it only catches a RESTATEMENT — "his sister is called
# Maya" replacing "his sister is Maya". Not an update.
#
# The tempting threshold is about two thirds, which would also fold "finished
# the Python course" onto "is learning Python". It would equally fold "works in
# Toronto" onto "lives in Toronto", which are two true facts, and deleting one
# of them is unrecoverable. A duplicate is untidy; a wrongly-replaced fact is
# simply gone.
#
# So staleness is not handled here at all. Every fact carries its date into the
# prompt, and the model weighs "5 months ago" against "today" itself — which is
# a judgement it can actually make and a word-overlap ratio cannot.
SUPERSEDE_AT = 0.8


def _supersedes(new: str, old: str) -> bool:
    a, b = _keys(new), _keys(old)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= SUPERSEDE_AT


def remember(fact: str = "", supersede: bool = True) -> str:
    """Learn one thing. Never raises."""
    f = re.sub(r"\s+", " ", (fact or "").strip())[:MAX_LEN]
    if len(f) < 3:
        return "There was nothing to remember."
    with _lock:
        all_of_it = _load()
        mine = list(_mine(all_of_it))
        low = f.lower()
        if any((m.get("text") or "").lower() == low for m in mine):
            return "I already knew that."
        replaced = ""
        if supersede:
            for m in list(mine):
                if _supersedes(f, m.get("text") or ""):
                    replaced = m.get("text") or ""
                    mine.remove(m)
                    break
        mine.append({"id": "m%d" % int(time.time() * 1000),
                     "text": f, "at": time.time()})
        del mine[:-MAX_FACTS]
        all_of_it[current()] = mine
        _save(all_of_it)
    if replaced:
        return "Noted, and I've dropped the older version (%s)." % replaced[:60]
    return "Noted."


def forget(which: str = "") -> str:
    w = (which or "").strip().lower()
    if not w:
        return "Forget what?"
    with _lock:
        all_of_it = _load()
        mine = _mine(all_of_it)
        if not mine:
            return "I don't know anything about you yet."
        if w in ("all", "everything"):
            n = len(mine)
            all_of_it[current()] = []
            _save(all_of_it)
            return "Forgotten all %d." % n
        keep = [m for m in mine
                if m.get("id") != w and w not in (m.get("text") or "").lower()]
        if len(keep) == len(mine):
            return "Nothing I know matches '%s'." % which
        all_of_it[current()] = keep
        _save(all_of_it)
        return "Forgotten %d." % (len(mine) - len(keep))


def import_facts(items) -> int:
    """Take what a browser had in localStorage, once.

    Nobody should lose months of accumulated memory because the storage moved.
    Runs only when this account has none — see /api/memory/import, which is
    where the "only once" actually lives.
    """
    n = 0
    for raw in (items or []):
        text = raw if isinstance(raw, str) else (raw or {}).get("text") or ""
        if isinstance(text, str) and text.strip():
            # No superseding on an import: these arrived without dates and in
            # an order nobody can vouch for, so guessing which replaced which
            # would delete things on the strength of a coin toss.
            if "already knew" not in remember(text, supersede=False):
                n += 1
    return n


# --- reading ----------------------------------------------------------------

def search(query: str = "") -> list:
    q = (query or "").strip().lower()
    if not q:
        return facts()
    want = _keys(q) or {q}
    hits = []
    for m in facts():
        text = (m.get("text") or "")
        low = text.lower()
        score = (2 if q in low else 0) + len(want & _keys(text))
        if score:
            hits.append((score, m))
    return [m for _, m in sorted(hits, key=lambda p: -p[0])]


def block() -> str:
    """The system-prompt section. Empty when there is nothing to say."""
    mine = facts()
    if not mine:
        return ""
    # Each one dated, because that is how staleness gets resolved: a fact from
    # five months ago and a contradicting one from today are both true of when
    # they were said, and the model can tell which still holds. Nothing here
    # deletes on a guess.
    return ("\n\nWHAT YOU ALREADY KNOW ABOUT THIS USER (from previous "
            "conversations — use it naturally, never recite it back). Each is "
            "dated: where two disagree, the newer one is what is true now, and "
            "an old one may simply be out of date rather than wrong:\n"
            + "\n".join("- %s  (%s)" % (m.get("text") or "", _ago(m.get("at")))
                        for m in mine))


def _ago(at) -> str:
    d = max(0, time.time() - float(at or 0))
    if d < 3600:
        return "just now"
    if d < 86400:
        return "today"
    days = int(d // 86400)
    if days < 14:
        return "%d day%s ago" % (days, "" if days == 1 else "s")
    if days < 60:
        return "%d weeks ago" % (days // 7)
    return "%d months ago" % max(1, days // 30)


def list_memory(about: str = "") -> str:
    mine = search(about) if (about or "").strip() else facts()
    if not mine:
        return ("I don't know anything about you yet." if not about
                else "Nothing I know matches that.")
    head = ("%d thing%s I know" % (len(mine), "" if len(mine) == 1 else "s")
            + (" about %s" % about if about else "") + ": ")
    return head + "  ".join("%s (%s)" % (m.get("text"), _ago(m.get("at")))
                            for m in mine[:40])


TOOLS = [
    {"name": "list_memory",
     "description": (
         "What ARC remembers about the user, optionally filtered. Use for 'what do "
         "you know about me', 'what do you remember', 'do you know my sister's "
         "name'. Reads only."),
     "input_schema": {"type": "object", "properties": {
         "about": {"type": "string", "description": "Optional subject to search for"}}}},

    {"name": "forget",
     "description": (
         "Remove something ARC remembers — by subject, or 'all'. Use when the user "
         "says 'forget that', 'that's wrong', 'stop remembering X'. If a fact is "
         "merely out of date, prefer just remembering the new version, which "
         "replaces it."),
     "input_schema": {"type": "object", "properties": {
         "which": {"type": "string", "description": "Subject, id, or 'all'"}},
         "required": ["which"]}},
]

_DISPATCH = {"list_memory": list_memory, "forget": forget}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return "No such tool: %s" % name, True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return "Wrong arguments for %s: %s" % (name, e), True
    except Exception as e:
        return "Could not reach my memory: %s" % e, True
