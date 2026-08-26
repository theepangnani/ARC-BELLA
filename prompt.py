#!/usr/bin/env python3
"""Who ARC is — held by the server, not by the browser.

For as long as ARC has existed, the personality prompt lived in the page and
arrived with every request. That was fine while there was exactly one user and
it was the person who wrote it: nobody edits their own assistant's rulebook in
devtools to cheat themselves.

It stops being fine the moment anybody else signs in. A prompt the client sends
is a prompt the client can change — delete the safety lock, remove the guest
restrictions, hand itself a different persona — and the server has no way to
tell an edited one from the real thing, because there is nothing to compare it
against. Every limit expressed in that prompt was a request, politely worded.

So the base prompt now lives here, in files the browser cannot reach, and the
page sends only the parts that genuinely vary per turn: which persona is
selected, what mode, what language, the date, the alarms, what is on screen.
The server puts its own text first and the client's after it, which means the
client can ADD to the instructions and can never remove them.

The same move pays for itself twice over, because a prompt that no longer
changes per request is a prompt that can be CACHED. It is around twelve
thousand tokens and was being re-sent, in full, on every turn and on every one
of the six tool rounds a single turn can take. Cached, those tokens cost a
tenth as much. See run.py's build().
"""

import io
import os
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PROMPTS = ROOT / "prompts"

# The two prompts ARC actually runs on. Named, so the client asks for one by
# name rather than supplying its own — "main" for a conversation, "watch" for
# the passive screen glance, which is a different job with a much shorter brief
# and must never inherit the conversational one.
#
# Anything not on this list falls back to "main". A caller cannot invent a
# third prompt, and cannot ask for a file: the name never touches the path.
NAMES = ("main", "watch")

_cache: dict = {}


def _read(name: str) -> str:
    path = PROMPTS / ("%s.md" % name)
    try:
        return io.open(path, encoding="utf-8").read().strip()
    except Exception as e:
        # A missing prompt file is not survivable, and must not fail quietly:
        # an empty base is exactly the state this module exists to prevent.
        raise RuntimeError("Cannot read %s: %s" % (path, e))


def base(name: str = "main") -> str:
    """The server's own instructions. Read once, then held in memory."""
    key = name if name in NAMES else "main"
    if key not in _cache:
        _cache[key] = _read(key)
    return _cache[key]


def reload() -> None:
    """Forget the cached text, so an edited prompt takes effect on next use.

    Only useful in development — and deliberately not wired to a route, since
    "reload your own instructions from disk" is not something a request should
    be able to ask for.
    """
    _cache.clear()


def sizes() -> dict:
    return {n: len(base(n)) for n in NAMES}
