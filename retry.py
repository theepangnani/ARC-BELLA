#!/usr/bin/env python3
"""When a tool fails: try again, or stop, or say why — and never twice by accident.

A tool that fails hands the model an error and leaves it to decide. The model
usually decides to call the tool again, which costs a round, and then again,
which costs another. A turn has sixteen rounds; a network blip three steps into
a six-step job can eat all of them without the job moving, and what the user
hears at the end is that ARC ran out of room. Nothing in that sentence is the
actual problem.

Three separate things are wrong there, and only the first is "add a retry":

  1. A HICCUP IS NOT A FAILURE. A DNS wobble or a 503 will very likely work a
     second later. Handing that to the model as an error spends a round to
     re-decide something that only needed waiting.
  2. A REFUSAL IS NOT A HICCUP. Permission denied, no such file, a guest asking
     for the owner's mail — those go exactly the same way the second time. A
     retry there is a round spent proving something already known.
  3. NOTHING STOPS THE MODEL LOOPING. Once the error is in the transcript
     nothing says "that is settled, move on", so the same call comes back. This
     is the one that actually burns the sixteen rounds, and no amount of
     retrying inside one call fixes it — the fix is an error message that ends
     the argument, and a count that stops answering when it does not.

THE PART THAT MATTERS MOST, and the reason this is a module with tests rather
than a try/except at the call site: RETRYING IS ONLY SAFE FOR SOME TOOLS. A
timeout is ambiguous — the request may have arrived and the answer got lost. Do
that to a lookup and the worst case is a wasted second. Do it to send_pending or
create_event and the worst case is the message sent twice, or two of the same
meeting in someone's diary, and the user finds out from the other end.

So RETRYABLE is default-deny, the same idiom as PASSIVE_TOOLS and GUEST_TOOLS
in run.py: a tool added later is never retried until somebody puts it in the
list on purpose. Getting that backwards is not a bug that shows up in testing.
It shows up as an apology to whoever got the message twice.
"""

import json
import re

# How many goes in total, including the first. Three is two retries.
ATTEMPTS = 3

# Waits between attempts, in seconds. Short: this is inside a turn somebody is
# waiting through, and a voice assistant that goes quiet for eight seconds has
# not recovered, it has hung.
BACKOFF = (0.4, 1.2)

# The same failing call, repeated this many times in ONE turn, stops being
# answered. This is the anti-loop guard, and it is separate from ATTEMPTS:
# ATTEMPTS covers one call site trying again, this covers the model coming back
# round and asking for the identical thing after being told it did not work.
SAME_CALL_LIMIT = 2

# --- what may be repeated at all -------------------------------------------
# Only things that can be done twice with no consequence. Everything here is a
# LOOKUP: it reads, it reports, and running it again changes nothing anywhere.
#
# Close to PASSIVE_TOOLS but deliberately its own list rather than an alias of
# it. Passive means "needs no consent"; retryable means "safe to do twice", and
# while they mostly agree they are answers to different questions — a tool could
# be added that reports something without changing it and still should not be
# repeated, and the day that happens the alias would be silently wrong.
RETRYABLE = {
    # Public lookups.
    "weather", "stock", "news", "market_outlook", "market_compare",
    "directions", "find_place", "convert_money", "sun_times",
    # web_search is billed per search, so a retry costs about a penny. Worth it
    # against a search that failed on a blip, and capped by ATTEMPTS.
    "web_search",
    # Reads of the user's own things.
    "list_events", "read_email", "search_email", "find_contact",
    "read_drive", "find_drive",
    "list_notes", "list_reminders", "list_todos", "list_price_alerts",
    "list_alarms",
    "tg_list_chats", "tg_read_chat",
    # The machine, read-only.
    "find_files", "read_file", "screenshot", "list_monitors",
    # ARC reading her own notepad.
    "plan_read",
}

# --- telling a hiccup from a refusal ---------------------------------------
# Matched against the error text, which run_tool formats as "TypeName: message"
# in every toolkit. Permanent is checked FIRST: "404 timeout" is a 404.
PERMANENT = (
    "no such tool", "not available on a guest", "wrong arguments",
    "permission denied", "not authorised", "not authorized", "forbidden",
    "unauthorized", "invalid_grant", "invalid credentials", "not found",
    "no such file", "filenotfounderror", "notadirectoryerror",
    "isadirectoryerror", "keyerror", "valueerror", "typeerror",
    "attributeerror", "401", "403", "404", "quota exceeded",
    "insufficient permission",
)

TRANSIENT = (
    "timeout", "timed out", "connectionerror", "connection reset",
    "connection aborted", "connection refused", "remotedisconnected",
    "incompleteread", "temporarily", "try again", "rate limit",
    "ratelimit", "too many requests", "429", "500", "502", "503", "504",
    "bad gateway", "service unavailable", "ssl", "econnreset",
    "network is unreachable", "name resolution",
)


def classify(out, failed: bool) -> str:
    """'ok', 'transient' or 'permanent' for one tool result."""
    if not failed:
        return "ok"
    text = (out if isinstance(out, str) else str(out)).lower()
    for m in PERMANENT:
        if m in text:
            return "permanent"
    for m in TRANSIENT:
        if m in text:
            return "transient"
    # Unrecognised failures are treated as permanent, which is the safe way to
    # be wrong: the cost is a round the model spends deciding for itself, and
    # the alternative — retrying anything unclassified — is how something that
    # half-succeeded gets done again.
    return "permanent"


def may_retry(name: str, out, failed: bool) -> bool:
    return bool(failed) and name in RETRYABLE and classify(out, failed) == "transient"


def wait_for(attempt: int) -> float:
    """Seconds to wait before attempt number `attempt` (1 is the first retry)."""
    i = max(1, int(attempt)) - 1
    return BACKOFF[i] if i < len(BACKOFF) else BACKOFF[-1]


def key_for(name: str, args) -> str:
    """One call, for counting repeats. Arguments included, because 'read_file
    a.txt' failing says nothing about 'read_file b.txt'."""
    try:
        return name + "|" + json.dumps(args or {}, sort_keys=True, default=str)
    except Exception:
        return name + "|" + repr(args)


class Turn:
    """The failures seen in one turn, so a model that keeps asking gets told."""

    def __init__(self):
        self.failed = {}

    def record(self, name: str, args) -> int:
        k = key_for(name, args)
        self.failed[k] = self.failed.get(k, 0) + 1
        return self.failed[k]

    def blocked(self, name: str, args) -> bool:
        """Whether this exact call has already failed enough times to stop."""
        return self.failed.get(key_for(name, args), 0) >= SAME_CALL_LIMIT


def _clean(last) -> str:
    text = last if isinstance(last, str) else str(last)
    return re.sub(r"\s+", " ", text).strip()[:300]


def giving_up(name: str, attempts: int, last) -> str:
    """What the model is told once a tool is not going to work.

    Written to END the matter. The old error said what went wrong and stopped,
    which reads as an invitation to have another go — and having another go, in
    a loop, is what emptied the round budget. This says the thing is settled,
    says what to do instead, and names both.
    """
    tried = ("%d times" % attempts) if attempts > 1 else "and it failed"
    return (
        "%s failed %s and is not going to work this turn. Last error: %s. "
        "Do NOT call %s again — you have the answer, and it is that this cannot "
        "be done right now. If you are working through a plan, mark the step "
        "blocked with plan_step and put this reason in the note, then carry on "
        "to the next step. If not, tell the user in one sentence what stopped "
        "you and what they could do about it. Never say it worked."
        % (name, tried, _clean(last), name)
    )


def already_said(name: str, last) -> str:
    """For a call the model has repeated after being told. Shorter, and firmer."""
    return (
        "%s has already failed this turn and you have been told why: %s. "
        "Asking again will not change it. Mark the step blocked or tell the "
        "user — do not call %s a third time."
        % (name, _clean(last), name)
    )
