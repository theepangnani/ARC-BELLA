#!/usr/bin/env python3
"""Whose request this is, and therefore whose data.

Every store in ARC — notes, alarms, reminders, to-dos, price alerts, standing
rules — was a single flat list in a single file. That was exactly right while
one person used it, and it is the reason a second person cannot be let in: they
would not get their own notes, they would get YOURS, and their first reminder
would appear in your evening.

memory.py already solved this for itself: a thread-local address set once per
request, and a file keyed by it. This is that idea pulled out so the other six
stores can share ONE notion of who is asking, rather than six that drift.

WHY THREAD-LOCAL AND NOT AN ARGUMENT. The alternative is threading an address
through every call site, including the tool dispatcher, which hands a tool a
dict of arguments the model wrote. That would mean either trusting the model to
say who it is — it must never be able to — or rewriting forty tool signatures.
The request sets it once; everything underneath reads it.

THE COST OF THAT CHOICE, stated plainly because it is real: code that runs
OUTSIDE a request has no address set, and would quietly read the default one.
That is precisely what the background loops do — the thing that rings alarms at
seven in the morning belongs to no request at all. So they must use everyone()
and never mine(), and there is a test that fails if a firing loop reads a store
through the per-request door.
"""

import threading

# The address used when nobody has said otherwise: a single-user install, the
# CLI, a test. Deliberately not an email — it can never collide with a real one.
DEFAULT = "owner"

_who = threading.local()
_owners: set = set()


def use(email: str) -> None:
    """Whose data the rest of this request is about. Set by run.py, once."""
    _who.email = (email or "").strip().lower() or DEFAULT


def current() -> str:
    return getattr(_who, "email", "") or DEFAULT


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
