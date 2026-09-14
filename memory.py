#!/usr/bin/env python3
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
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

import contextvars
import io
import json
import os
import re
import threading
import time

import lessons
import redact
import storefile
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "memory.json"

# The file's one lock, shared with anything else in the process that writes it.
# It was a private RLock, which only stopped memory.py interleaving with itself.
_lock = storefile.lock(STORE)

# Said instead of "Noted." when the file could not be read or written. Before
# storefile, a failed save was swallowed and the reply still said "Noted." —
# she promised to remember, forgot, and nobody could tell.
COULD_NOT = "I couldn't reach my memory just now, so nothing was changed. Try again in a moment."

# Said when the file is damaged rather than busy. "Try again in a moment" never
# works for damage, since the file is just as broken a moment later. Self-repair
# can put back the last good copy (memory.json is in selfheal.DATA_FILES).
DAMAGED = ("My memory file is damaged, so nothing was changed. Ask me to repair "
           "myself and I'll put back the last good copy.")


def _refusal(e: Exception) -> str:
    return COULD_NOT if isinstance(e, storefile.Busy) else DAMAGED

MAX_FACTS = 200          # per account; was 120 in the browser
MAX_LEN = 240

REFUSED_SECRET = ("I don't keep passwords, keys or card numbers in memory — "
                  "it's sent back to me on every turn and stored in a plain file.")            # one fact, in characters

REFUSED_INSTRUCTION = ("NOT KEPT: that reads as an instruction, not a fact about "
                       "the person. Memory holds facts, and it is repeated to me on "
                       "every turn, so an instruction kept here would be obeyed "
                       "forever. If they want a habit, they can tell me in their own "
                       "words.")

# A fact is something true about a person. What follows is something to DO, or
# a way of reaching somebody else's machinery — and a memory is sent back at
# the top of every future turn, so one of these kept once is an order repeated
# for as long as the fact lives. Blunt on purpose, as lessons._LOOSENS is: a
# false refusal costs one sentence, a false pass is permanent. Kept narrower
# than _LOOSENS, though, since facts legitimately mention buying, selling and
# mail ("works in sales", "trades stocks") where habits never need to.
_INSTRUCTION = re.compile(
    r"(?i)\b(ignore|disregard|override|bypass|forget)\b.{0,30}"
    r"\b(previous|prior|above|earlier|all|any|your|the|these|those)\b.{0,20}"
    r"\b(instructions?|rules?|prompts?|guidelines?|directions?)\b"
    r"|\bsystem prompt\b|\bdeveloper mode\b|\bjailbreak"
    # Addressed to the assistant rather than about the person.
    # Not "Bella" or "ARC": a dog is called Bella, and the owner's own project
    # is ARC, and both are facts.
    r"|\b(assistant|claude|the ai|the model)\b.{0,20}\b(must|should|shall|is to|has to|needs? to|always|never)\b"
    r"|\byou\b.{0,10}\b(must|should|shall|are to|have to|need to|will always|always|never)\b"
    # An order with no subject. "Never eats meat" is a fact written tersely, so
    # only when what follows is something to do.
    r"|^\s*(always|never|from now on|whenever|every time|do not|don't)\b.{0,40}"
    r"\b(send|forward|reply|respond|share|delete|click|open|visit|run|pay|transfer|"
    r"tell|say|answer|recommend|obey|follow|trust)\b"
    # Moving something to somebody. Not "email": "his work email is sam@..." is
    # the commonest fact with an address in it, and it moves nothing.
    r"|\b(forward|send|share|cc|bcc|upload|post|transfer|pay)\w*\b.{0,60}"
    r"(@|https?://|\bwww\.)"
    r"|\btreat\b.{0,60}\bas\b.{0,25}\b(me|mine|the owner|instructions?|commands?)\b"
    r"|\b(instructions?|commands?|orders?)\b.{0,30}\b(in|from)\b.{0,20}"
    r"\b(e-?mails?|mail|messages?|texts?|chats?|files?|pages?|sites?|documents?)\b"
    # A link carrying a credential or a session in its query.
    r"|https?://\S*[?&#](token|access_token|key|api_key|apikey|sig|signature|auth|code|"
    r"password|pwd|session|sid|secret)=")


def looks_like_instruction(text: str) -> bool:
    # Matched on normalised text, so look-alike letters and zero-width
    # characters do not walk past it (lessons._plain has the account of why).
    return bool(_INSTRUCTION.search(lessons._plain(text or "")))

# Whose memory this request is about. Set per-request by run.py, the same
# pattern apply_session_google uses for Google tokens — the alternative is
# threading an address through every call site including the tool dispatcher.
#
# A ContextVar, like gauth's, and NOT threading.local() as it was: async
# requests all share the event loop's one thread, so a thread-local let a
# guest's request overwrite the owner's address while the owner's turn was
# awaiting Claude, and the owner's "remember this" landed in the guest's
# memory. whose.py carries the full account of it.
_who = contextvars.ContextVar("arc_memory_who", default="owner")


def use(email: str) -> None:
    _who.set((email or "").strip().lower() or "owner")


def current() -> str:
    return _who.get() or "owner"


def connected() -> bool:
    return True


# --- storage ----------------------------------------------------------------

def _load() -> dict:
    """Everybody's memory. Raises storefile.Unreadable rather than guess.

    This used to turn ANY failure to read into {} — and on Windows a file being
    replaced by another thread routinely fails to read. remember() then wrote
    back a file holding only the current person's facts, and every other
    account's memory was gone. It is the bug storefile.py was written for, in
    the one store that had not been moved onto it. Only a missing file is
    empty; a busy or damaged one stops the write.
    """
    data = storefile.read(STORE, empty=dict)
    if not isinstance(data, dict):
        # Memory has only ever been {address: [facts]}. A list here is not an
        # older shape to migrate, it is damage, and not something to write over.
        raise storefile.Unreadable("memory.json holds a %s, not a dict"
                                   % type(data).__name__)
    return data


def _save(all_of_it: dict) -> None:
    """Raises OSError if it could not be saved. Callers say so."""
    storefile.write(STORE, all_of_it)


def _mine(all_of_it: dict) -> list:
    got = all_of_it.get(current())
    return got if isinstance(got, list) else []


def facts(limit: int = 0) -> list:
    """Newest last, which is the order they should be read in.

    A DISPLAY read, so a busy or damaged file shows as nothing rather than
    failing the turn. Nothing that saves may use it to decide what to write.
    """
    with _lock:
        try:
            out = _mine(_load())
        except storefile.Unreadable:
            out = []
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


# It only catches a RESTATEMENT — "his sister is called Maya" replacing "his
# sister is Maya". Not an update.
#
# The tempting threshold is about two thirds, which would also fold "finished
# the Python course" onto "is learning Python". It would equally fold "works in
# Toronto" onto "lives in Toronto", which are two true facts, and deleting one
# of them is unrecoverable. A duplicate is untidy; a wrongly-replaced fact is
# simply gone.
#
# It was an 80% overlap measured against the SMALLER of the two facts, and that
# was the hole: anything wholly inside an older fact counted as restating it.
# "Her sister is Maya" deleted "Her sister Maya lives in Leeds", and "allergic
# to peanuts" deleted "allergic to peanuts and shellfish" — the short one was
# kept and the detail was gone. The next rule let a richer fact replace any
# fact it covered, and that lasted until words were folded to their stems
# (below): "likes dog food" covered "likes dogs", "the owner's wife drives a
# red Tesla" covered "drives a red Tesla" — different facts, one of them gone.
#
# So now only a RESTATEMENT replaces: the same meaning-carrying words, once
# plurals and -ing are folded ("likes hiking" / "likes to hike"), the way it is
# phrased is set aside ("is called Maya" / "is Maya"), and whoever it is about
# is set aside too ("the owner" / "the user" / their name). Anything richer or
# poorer is kept beside it. A duplicate is cheap; a lost fact is not.
#
# So staleness is not handled here at all. Every fact carries its date into the
# prompt, and the model weighs "5 months ago" against "today" itself — which is
# a judgement it can actually make and a word-overlap ratio cannot.

# Words that change how a fact is phrased but not what it says, ignored only
# when deciding whether one fact restates another — "his sister is called
# Maya" and "his sister is Maya" are the same fact.
_PHRASING = {"called", "named", "known", "name"}

# Ways a fact names the person it is about. The model writes "the owner", "the
# user", "I" and their name more or less at random from one day to the next,
# and each wording was kept as a separate fact ("wording drift"). Generic words
# only: the repo is public and no one's name belongs in it. A NAME joins these
# per person, and only from a fact that says outright it is theirs — see
# _subjects.
_SELF = {"owner", "user", "guest", "person", "me", "myself", "mine", "you",
         "your", "yours", "them", "him", "herself", "himself", "themselves"}

_THEIR_NAME = re.compile(
    r"^\s*(?:my|(?:the\s+)?(?:owner|user)'?s)\s+name\s+is\s+([^\W\d_]+)"
    r"|^\s*(?:the\s+)?(?:owner|user)\s+is\s+(?:called|named)\s+([^\W\d_]+)"
    r"|^\s*i\s+am\s+(?:called|named)\s+([^\W\d_]+)", re.I | re.UNICODE)


def _subjects(mine: list) -> set:
    """_SELF, plus this account's own name if one of ITS facts gives it.

    Only this slice: a guest's facts say who the guest is, never who the owner
    is, and a guest's "the user" must not fold into the owner's name. Only a
    fact that names THEM — "His sister's name is Maya" does not make Maya a
    word for the owner.
    """
    out = set(_SELF)
    for m in mine:
        hit = _THEIR_NAME.match(m.get("text") or "")
        if hit:
            out.add(_stem(next(g for g in hit.groups() if g).lower()))
    return out


def _stem(w: str) -> str:
    """Folded crudely: dogs/dog, running/run, hiking/hike, lives/lived/live.

    Not a stemmer and not trying to be. Wrong folds only ever make two words
    look alike, and that matters only where looking alike decides something —
    so it is used for search and for spotting a restatement, where the rule is
    whole-set equality, and never for forget, which deletes.
    """
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith(("ches", "shes", "sses", "xes", "zes")):
        w = w[:-2]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouls":
        w = w[:-1]                      # runn -> run, stopp -> stop
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]                      # hike/hik(ing), live/liv(ed)
    return w


def _stems(text: str) -> set:
    return {_stem(w) for w in _keys(text)}


def _supersedes(new: str, old: str, subjects=frozenset(_SELF)) -> bool:
    drop = {_stem(w) for w in _PHRASING | set(subjects)}
    a, b = _stems(new) - drop, _stems(old) - drop
    return bool(a) and a == b


def _new_id(mine: list) -> str:
    """Unique within this account's facts.

    It was "m" + the millisecond, so two facts saved in the same millisecond —
    an import, or a fast disk — shared an id, and forget(id) took both. Same
    shape as ever, since stored facts and anything holding an id already use it;
    it just steps past one that is taken.
    """
    taken = {m.get("id") for m in mine}
    n = int(time.time() * 1000)
    while "m%d" % n in taken:
        n += 1
    return "m%d" % n


def remember(fact: str = "", supersede: bool = True) -> str:
    """Learn one thing. Never raises."""
    f = re.sub(r"\s+", " ", (fact or "").strip())[:MAX_LEN]
    if len(f) < 3:
        return "There was nothing to remember."
    # Never a password, key or card number. Memory is sent back to the model at
    # the top of every turn, so a secret remembered is a secret repeated for as
    # long as the fact lives — and memory.json is a plain file. Refused whole
    # rather than stored with the value blanked: "my wifi password is [redacted]"
    # is a fact about nothing, and keeping it would suggest the rest was kept.
    if redact.looks_secret(f):
        return (REFUSED_SECRET + " A password manager is the right place for it.")
    if looks_like_instruction(f):
        return REFUSED_INSTRUCTION
    with _lock:
        try:
            all_of_it = _load()
        except storefile.Unreadable as e:
            return _refusal(e)
        mine = list(_mine(all_of_it))
        # Compared as words, so "coffee black." is the fact already held rather
        # than a new one — which, on an import, was kept a second time.
        flat = _flat(f)
        if any(_flat(m.get("text")) == flat for m in mine):
            return "I already knew that."
        replaced = ""
        if supersede:
            subjects = _subjects(mine + [{"text": f}])
            # Every older copy, not just the first: drift leaves several.
            for m in list(mine):
                if _supersedes(f, m.get("text") or "", subjects):
                    replaced = replaced or (m.get("text") or "")
                    mine.remove(m)
        mine.append({"id": _new_id(mine), "text": f, "at": time.time()})
        del mine[:-MAX_FACTS]
        all_of_it[current()] = mine
        try:
            _save(all_of_it)
        except OSError:
            return COULD_NOT
    if replaced:
        return "Noted, and I've dropped the older version (%s)." % replaced[:60]
    return "Noted."


# Said about a subject rather than being part of it: "forget the tea thing".
_VAGUE = {"thing", "things", "stuff", "fact", "facts", "about", "bit", "one",
          "part", "remember", "forget", "please", "what", "said"}


def _flat(text: str) -> str:
    """Lower case, words only — for "is this phrase that whole fact?"."""
    return " ".join(re.findall(r"[^\W_]+", (text or "").lower(), re.UNICODE))


def _names(text: str, words) -> bool:
    """Every word appears in text as a WHOLE word (a plural allowed).

    Not a substring. forget("tea") used to take "a team at work" with it, and
    forget("cat") took "studied communication": whatever held the letters went.
    """
    low = (text or "").lower()
    return all(re.search(r"(?<![^\W_])%s(?:s|es)?(?![^\W_])" % re.escape(w), low, re.UNICODE)
               for w in words)


def _matching(mine: list, w: str) -> list:
    """The facts a forget(w) is about: an id, the whole fact, or its words."""
    by_id = [m for m in mine if m.get("id") == w]
    if by_id:
        return by_id
    exact = [m for m in mine if _flat(m.get("text")) == _flat(w)]
    if exact:
        return exact
    phrase = _flat(w)
    if phrase:
        hits = [m for m in mine if _names(m.get("text"), [phrase])]
        if hits:
            return hits
    words = _keys(w) - _VAGUE
    return [m for m in mine if _names(m.get("text"), words)] if words else []


def forget(which: str = "") -> str:
    w = (which or "").strip().lower()
    if not w:
        return "Forget what?"
    with _lock:
        try:
            all_of_it = _load()
        except storefile.Unreadable as e:
            return _refusal(e)
        mine = _mine(all_of_it)
        if not mine:
            return "I don't know anything about you yet."
        if w in ("all", "everything"):
            n = len(mine)
            all_of_it[current()] = []
            said = "Forgotten all %d." % n
        else:
            gone = _matching(mine, w)
            if not gone:
                return "Nothing I know matches '%s'." % which
            # Several DIFFERENT facts share the subject: delete none, and ask.
            # "Forget my sister" might mean the wrong name or everything about
            # her, and a guess that deletes is the one that cannot be taken
            # back. Copies of one identical fact are not a choice, so they go
            # together — as does an id shared by two facts saved before ids
            # were unique, but only if they say the same thing.
            if len({_flat(m.get("text")) for m in gone}) > 1:
                return ("That matches %d things, so I haven't forgotten any yet: %s. "
                        "Say which one." % (
                            len(gone), "; ".join("'%s' (id %s)" % (m.get("text"), m.get("id"))
                                                 for m in gone[:10])
                            + ("; and %d more" % (len(gone) - 10) if len(gone) > 10 else "")))
            keep = [m for m in mine if all(m is not g for g in gone)]
            all_of_it[current()] = keep
            said = "Forgotten %d." % (len(mine) - len(keep))
        # "Forgotten" when it was not is the worse lie of the two: somebody
        # asked for a thing to be gone, and it is still sent on every turn.
        try:
            _save(all_of_it)
        except OSError:
            return COULD_NOT
        return said


def import_facts(items, only_if_empty: bool = False) -> int:
    """Take what a browser had in localStorage, once.

    Nobody should lose months of accumulated memory because the storage moved.
    Runs only when this account has none — see /api/memory/import, which is
    where the "only once" actually lives.
    """
    n = 0
    # only_if_empty: "only when this account has none", decided under the lock
    # and from a strict read. The route's own check goes through count(), which
    # is a display read and calls a busy file empty — so a stale browser could
    # pass it while the real facts were only momentarily locked, and then push
    # its old ones in on top. The lock is re-entrant, so remember() below can
    # still take it.
    with _lock:
        if only_if_empty:
            try:
                if _mine(_load()):
                    return 0
            except storefile.Unreadable:
                return 0
        for raw in (items or []):
            text = raw if isinstance(raw, str) else (raw or {}).get("text") or ""
            if isinstance(text, str) and text.strip():
                # No superseding on an import: these arrived without dates and in
                # an order nobody can vouch for, so guessing which replaced which
                # would delete things on the strength of a coin toss.
                # Counted only if it was actually kept — a refused secret is not
                # an import, and saying it was would be the one lie this can tell.
                if remember(text, supersede=False).startswith("Noted"):
                    n += 1
    return n


# --- reading ----------------------------------------------------------------

# "What do you know about me?" is a request for all of it. "me" was too short
# to be a word to search for, so the search fell back to hunting the text for
# the letters m-e — "name", "home" — and usually answered that nothing matched.
_EVERYTHING = {"me", "myself", "about me", "all", "everything", "anything",
               "all of it", "everything about me", "you know", "what you know"}

# Asked about a subject rather than naming one.
_ASKING = {"know", "tell", "does", "remember", "anything", "something"}


def search(query: str = "") -> list:
    """Best match first, and on a tie the newer fact first.

    Newer first because that is the one that is probably still true: asked
    where they live, "lives in Leeds" from this week belongs above "lives in
    York" from last year. It listed them oldest first.
    """
    q = (query or "").strip().lower()
    if not q or _flat(q) in _EVERYTHING:
        return facts()
    # Folded, so "dogs" finds "dog" and "running" finds "run".
    want = _stems(q) - {_stem(w) for w in _VAGUE | _ASKING}
    phrase = _flat(q)
    hits = []
    for i, m in enumerate(facts()):
        text = (m.get("text") or "")
        # The whole query as whole words: "Go" is not the start of "Google".
        score = (2 if phrase and _names(text, [phrase]) else 0) + len(want & _stems(text))
        if score:
            hits.append((score, i, m))
    return [m for _, _, m in sorted(hits, key=lambda p: (-p[0], -p[1]))]


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
    # Years past a year. "26 months ago" is a sum the model and the person
    # listening both had to do, and "13 months" sounds more recent than it is.
    if days < 365:
        return "%d months ago" % max(1, days // 30)
    years = days // 365
    return "%d year%s ago" % (years, "" if years == 1 else "s")


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
         "replaces it. If a subject matches several different facts, nothing is "
         "removed and they are listed with ids: ask which, then forget by id."),
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
