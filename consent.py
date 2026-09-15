#!/usr/bin/env python3
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""A yes is for the action it was asked about, not for anything in that turn.

The consent gate used to take one boolean, allow_actions, for a whole turn. The
page set it when the person said yes to something Bella was holding back — and
from then until the reply ended, ANY action ran: the one they had been told
about, and any other the model reached for, including one a web page or an
email had talked it into (Claude 1's security list; designed by Claude 4,
reviewed by Claude 2).

Now, when the gate holds an action back, it issues a token for exactly that
call: this person, this browser, this tool, these arguments. The page shows
what it is and sends the token back with the yes. The server lets that call
through once and nothing else. A token is:

  · single use — redeemed once, then gone;
  · short-lived — PENDING_SECONDS;
  · bound to the account AND the browser session that saw it (the same
    cookie hash as links' sign-in binding). In open mode — private Bella on
    8421, reached only from this PC — there is no session cookie, the binding
    is a constant, and only the account binding does anything;
  · for the exact arguments — a digest of them, so "type 'hello'" does not
    approve "type 'rm -rf'";
  · kept in memory only, never on disk and never in a log.

FAMILIES is the one widening, and it is a table on purpose. Screen work is a
chain of small steps, each after a fresh look at the screen, and a yes per
click would make "fill in this form" unusable. So redeeming a token for a tool
in a family lets further calls of THAT family run for the rest of the same
turn. Everything not in a family stays strict. The owner chose this on 15 Sep
2026, as "same kind of action": a yes covers the exact call and more of the
same family for the rest of that turn, while anything riskier (running a
command, sending, deleting, standing rules) still needs its own yes. Emptying
the table makes every action strict.
"""

import hashlib
import json
import secrets
import threading
import time

PENDING_SECONDS = 300
PER_PERSON = 16
MAX_PENDING = 256
PREVIEW_CHARS = 160

# Tools that, once one of them is approved, may continue for the rest of that
# turn. Low-consequence steps of one job at the screen. Deliberately NOT here:
# closing windows, the clipboard, macros and auto-clickers, running commands,
# sending, deleting, rules, anything touching an account. tests/test_consenttoken.py
# pins this table, so widening it is a decision somebody makes.
FAMILIES = {
    "screen": frozenset({"mouse_control", "keyboard", "focus_window"}),
}

_pending: dict = {}         # token -> {who, bind, tool, digest, preview, at, expires}
_lock = threading.Lock()


def digest(args) -> str:
    """The same arguments always give the same digest, whatever order the
    model wrote the keys in."""
    blob = json.dumps(args or {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def family_of(tool: str) -> str:
    for name, tools in FAMILIES.items():
        if tool in tools:
            return name
    return ""


# Who or where an action reaches, shown before what it says. Sorted JSON put
# "text" before "to", so a long Telegram message pushed the recipient past the
# cap, and the person said yes without seeing who it went to (Claude 6's review).
_FIRST = ("to", "recipient", "recipients", "chat", "contact", "name", "email",
          "attendees", "number", "path", "command", "command_id", "pending_id",
          "url", "app", "window", "key", "keys", "when", "start", "title")
_VALUE_CHARS = 48


def _args_line(args) -> str:
    if not isinstance(args, dict):
        return json.dumps(args, ensure_ascii=False, default=str)
    keys = [k for k in _FIRST if k in args] + sorted(k for k in args if k not in _FIRST)
    parts = []
    for k in keys:
        v = json.dumps(args[k], ensure_ascii=False, default=str)
        if len(v) > _VALUE_CHARS:
            v = v[:_VALUE_CHARS - 1] + "…"
        parts.append("%s=%s" % (k, v))
    return "{" + ", ".join(parts) + "}"


def preview(tool: str, args, describe: str = "") -> str:
    """What the person is shown before they say yes. `describe` replaces the
    raw arguments when the caller knows better (a prepared command's text)."""
    body = describe or _args_line(args)
    text = "%s %s" % (tool, body)
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS - 1] + "…"


def _sweep(now: float) -> None:
    """Caller holds _lock."""
    for k in [k for k, v in _pending.items() if now > v["expires"]]:
        del _pending[k]


def issue(who: str, bind: str, tool: str, args, describe: str = "") -> dict:
    """A token for exactly this call. Returns what the page may see: the token,
    the tool and the preview — never the digest."""
    now = time.time()
    token = secrets.token_urlsafe(18)
    entry = {"who": who, "bind": bind, "tool": tool, "digest": digest(args),
             "preview": preview(tool, args, describe), "at": now,
             "expires": now + PENDING_SECONDS}
    with _lock:
        _sweep(now)
        # Oldest by insertion order, as in links._pending: a busy turn issues
        # several in the same clock tick.
        mine = [k for k, v in _pending.items() if v["who"] == who]
        for k in mine[:max(0, len(mine) - PER_PERSON + 1)]:
            del _pending[k]
        for k in list(_pending)[:max(0, len(_pending) - MAX_PENDING + 1)]:
            del _pending[k]
        _pending[token] = entry
    return {"token": token, "tool": tool, "preview": entry["preview"]}


def approved(who: str, bind: str, tokens) -> list:
    """The previews of the offered tokens that are still good for this person
    and browser, for telling the model what was approved. Consumes nothing."""
    now = time.time()
    out = []
    with _lock:
        _sweep(now)
        for t in tokens or ():
            v = _pending.get(str(t))
            if v and v["who"] == who and v["bind"] == bind:
                out.append(v["preview"])
    return out


def redeem(who: str, bind: str, tokens, tool: str, args) -> bool:
    """True, once, if an offered token was issued for exactly this call."""
    want = digest(args)
    now = time.time()
    with _lock:
        _sweep(now)
        for t in tokens or ():
            v = _pending.get(str(t))
            if (v and v["who"] == who and v["bind"] == bind
                    and v["tool"] == tool and v["digest"] == want):
                del _pending[str(t)]
                return True
    return False
