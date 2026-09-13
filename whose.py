#!/usr/bin/env python3
"""Whose request this is, and therefore whose data.

Every store in ARC — notes, alarms, reminders, to-dos, price alerts, standing
rules — was a single flat list in a single file. That was exactly right while
one person used it, and it is the reason a second person cannot be let in: they
would not get their own notes, they would get YOURS, and their first reminder
would appear in your evening.

memory.py already solved this for itself: an address set once per request, and
a file keyed by it. This is that idea pulled out so the other six stores can
share ONE notion of who is asking, rather than six that drift.

WHY SET ONCE AND NOT AN ARGUMENT. The alternative is threading an address
through every call site, including the tool dispatcher, which hands a tool a
dict of arguments the model wrote. That would mean either trusting the model to
say who it is — it must never be able to — or rewriting forty tool signatures.
The request sets it once; everything underneath reads it.

WHY A CONTEXTVAR AND NOT threading.local(), which is what this was, and it was
wrong in exactly the way that matters. /api/chat is async: every request runs
on the SAME thread, the event loop's, so a thread-local is one slot shared by
everybody. The owner's turn set "owner", awaited Claude, and while it waited a
guest's request set the guest; when the owner's turn came back to run
add_note, plan_step or list_memory, current() answered with the guest's address
— the owner's note filed in the guest's store, or the reverse. Found on the
laptop while planning streaming, reproduced on the desktop with two overlapping
requests. A ContextVar belongs to the asyncio task, so each request keeps its
own across every await, and gauth.py's token path has always worked this way.

Two consequences, both wanted. Work a request hands to a thread
(asyncio.to_thread, anyio.to_thread) copies the context and so still knows who
asked — a thread-local silently read the default there. And a task or thread
started OUTSIDE any request, like the background loops born in lifespan, sees
the default, as before.

THE COST OF THAT CHOICE, stated plainly because it is real: code that runs
OUTSIDE a request has no address set, and would quietly read the default one.
That is precisely what the background loops do — the thing that rings alarms at
seven in the morning belongs to no request at all. So they must use everyone()
and never mine(), and there is a test that fails if a firing loop reads a store
through the per-request door.
"""

import contextvars
from contextlib import contextmanager

# The address used when nobody has said otherwise: a single-user install, the
# CLI, a test. Deliberately not an email — it can never collide with a real one.
DEFAULT = "owner"

_who = contextvars.ContextVar("arc_whose", default=DEFAULT)
_owners: set = set()


def use(email: str) -> None:
    """Whose data the rest of this request is about. Set by run.py, once."""
    _who.set((email or "").strip().lower() or DEFAULT)


def current() -> str:
    return _who.get() or DEFAULT


def set_owners(emails) -> None:
    """The addresses that count as the owner, for the legacy pile below."""
    global _owners
    _owners = {str(e).strip().lower() for e in (emails or []) if str(e).strip()}


def is_owner(email: str = None) -> bool:
    # No allowlist configured means a single-user install — there is nobody
    # else for the data to belong to, so the one person using it is the owner.
    if not _owners:
        return True
    return (email or current()).strip().lower() in _owners


def _owner_key() -> str:
    """Where a legacy flat pile lands. The signed-in owner if one is asking,
    otherwise the first configured owner — never the guest doing the writing."""
    if is_owner() and current() != DEFAULT:
        return current()
    return sorted(_owners)[0] if _owners else DEFAULT


def mine(blob) -> list:
    """This account's slice of a store.

    A LIST is a store written before any of this existed, when there was only
    one person and everything in it was theirs. It reads as the owner's, and a
    guest arriving at a file that has never been split gets nothing rather than
    inheriting somebody's reminders.
    """
    if isinstance(blob, list):
        return list(blob) if is_owner() else []
    if isinstance(blob, dict):
        got = blob.get(current())
        return list(got) if isinstance(got, list) else []
    return []


def replace(blob, items) -> dict:
    """The whole store back, with this account's slice set to `items`.

    The migration happens here rather than at load: nothing is rewritten until
    somebody actually writes, so a version that reads a file and never changes
    it leaves that file exactly as it found it.
    """
    out = {}
    if isinstance(blob, dict):
        out = {k: list(v) for k, v in blob.items() if isinstance(v, list)}
    elif isinstance(blob, list) and blob:
        # First write since the split. The old pile is the owner's, and stays
        # the owner's even when it is a guest who triggered the rewrite.
        out[_owner_key()] = list(blob)
    out[current()] = list(items)
    return out


def everyone(blob):
    """(address, items) for every account in a store.

    What the background loops must use. An alarm rings for the person who set
    it, and the loop that rings it is not serving anybody's request — so it has
    no address, and asking mine() would silently give it the default one and
    ring only that account's alarms.
    """
    if isinstance(blob, dict):
        return [(k, list(v)) for k, v in blob.items() if isinstance(v, list)]
    if isinstance(blob, list):
        return [(_owner_key(), list(blob))]
    return []


def accounts(blob) -> list:
    """Every address that has anything in a store."""
    return [email for email, _ in everyone(blob)]


@contextmanager
def acting_as(email: str):
    """Be this account for the length of a block, then be whoever you were.

    How the background loops work through a store one person at a time. The
    alternative — a second, loop-only way of reading and writing each file —
    is two code paths for one store, and the day they disagree about whose an
    alarm is, it rings in nobody's tab. With this, the loop reads and writes
    through the same _load() and _save() a request does, so there is only one
    notion of "this person's alarms" to get right.
    """
    token = _who.set((email or "").strip().lower() or DEFAULT)
    try:
        yield
    finally:
        _who.reset(token)


def flatten(blob) -> list:
    """Every item in a store, whoever it belongs to, each tagged with `_who`.

    For loops that only need to know an item is due, not who to hand it to.
    The tag is added rather than the address being dropped, because "fire it"
    and "tell the right person" are the same journey and losing the address
    halfway is how one person's reminder ends up read out to another.
    """
    out = []
    for email, items in everyone(blob):
        for it in items:
            if isinstance(it, dict):
                row = dict(it)
                row["_who"] = email
                out.append(row)
    return out
