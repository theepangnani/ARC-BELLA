#!/usr/bin/env python3
"""Reading and writing one JSON store, without ever mistaking a busy file for an
empty one.

Every personal store in ARC — notes, plan, panels, to-dos, reminders, alarms,
price alerts, standing rules, the tour — holds everybody's items in one file,
split by address (whose.py). Saving one person's slice means reading the whole
file, replacing that slice, and writing the whole file back.

Each store used to do that with a helper that turned ANY failure to read into
"the file is empty". On Windows that failure is routine: reading a file at the
moment another thread os.replace()s it raises PermissionError. So a guest
adding a reminder while the background loop marked the owner's as pushed read
"nothing", replaced their own slice of nothing, and wrote the file back holding
only the guest's — every other person's reminders gone, silently. Measured
before this existed: two threads adding reminders as two people ended with 47
of the owner's 205.

Three things stop that, and every store now goes through all three:

  · A BUSY FILE IS RETRIED, never read as empty. Only a file that does not
    exist is empty. A file that exists and will not parse is DAMAGED, and
    raises Unreadable, so nothing is written over it — self_repair can put a
    copy back; a save on top of it cannot be undone.
  · ONE LOCK PER FILE, shared by everything in the process that writes it, so
    a read-modify-write cannot interleave with another one.
  · A TEMPORARY FILE OF ITS OWN for every write. They all used "<name>.tmp",
    so two writers could rename each other's half-finished file into place.
"""

import itertools
import json
import os
import threading
import time
from pathlib import Path

RETRIES = 40          # x WAIT: about two seconds of a file being busy
WAIT = 0.05


class Unreadable(Exception):
    """The file is there and is not a store. Nothing should be written over it."""


_locks: dict = {}
_guard = threading.Lock()
_seq = itertools.count()


def lock(path) -> threading.RLock:
    """The one lock for this file in this process. Re-entrant, so a function
    holding it can call another that takes it too."""
    key = os.path.normcase(str(Path(path).resolve()))
    with _guard:
        got = _locks.get(key)
        if got is None:
            got = _locks[key] = threading.RLock()
        return got


def read(path, empty=list):
    """The parsed file. `empty()` for a file that does not exist (or is blank).

    Raises Unreadable for a file that exists but is not JSON, or that stayed
    busy past RETRIES. Callers that only DISPLAY may catch it and show nothing;
    callers about to write must let it stop them.
    """
    p = Path(path)
    for _ in range(RETRIES):
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty()
        except PermissionError:
            time.sleep(WAIT)            # another thread is replacing it
            continue
        if not text.strip():
            return empty()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise Unreadable("%s is not valid JSON (line %d)" % (p.name, e.lineno))
    raise Unreadable("%s stayed busy for %.0f seconds" % (p.name, RETRIES * WAIT))


def write(path, blob, indent=None) -> None:
    """Write the whole file atomically, through a temporary file of its own."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("%s.%d-%d-%d.tmp" % (p.name, os.getpid(),
                                           threading.get_ident(), next(_seq)))
    tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=indent), encoding="utf-8")
    try:
        for _ in range(RETRIES):
            try:
                os.replace(tmp, p)      # cannot land halfway
                return
            except PermissionError:
                time.sleep(WAIT)        # a reader has it open this instant
        raise OSError("%s stayed busy; not saved" % p.name)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def shaped(blob):
    """A store is a list (from before the split) or {address: [...]}. Anything
    else is damage, and reported as such rather than coerced into emptiness."""
    if isinstance(blob, list):
        return blob
    if isinstance(blob, dict):
        return blob
    raise Unreadable("the store holds a %s, not a list or a dict" % type(blob).__name__)
