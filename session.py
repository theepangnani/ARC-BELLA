#!/usr/bin/env python3
"""
Server-side login sessions.

The old scheme was a signed expiry stamp in a cookie and nothing else — no
record on the server, so nothing could be revoked. Rotating the password
logged nobody out, and a stolen cookie was good for its full thirty days.

This is the replacement: an opaque random id in the cookie, and the actual
session state here, on disk, deletable.

Two clocks, and a session dies when EITHER runs out:

    idle       ARC_SESSION_IDLE_MINUTES   default 30   time since you last used it
    absolute   ARC_SESSION_MAX_HOURS      0 = off      time since sign-in

Idle is the one that matters, and "idle" means YOU stopped, not the tab. The
HUD polls on its own schedule — screen-watch every four seconds, plus
reminders, alerts and health — so refreshing the clock on every request meant a
browser left open on a locked laptop stayed signed in for ever. Only requests a
person actually caused extend a session now; see validate(touch=...) below and
BACKGROUND_PATHS in run.py. Walk away, and thirty minutes later you are signed
out whether or not the window is still sitting there.

The absolute cap is a separate, optional thing: a hard stop that fires however
busy the session is. Set ARC_SESSION_MAX_HOURS to a number of hours to turn it
on (clamped to four, since a larger figure is a typo rather than a preference).
At 0 there is no such stop and idle is the only clock — sign in, use it all
day, close the lid, and it ends by itself.

Both clocks are skipped entirely for the owner — see set_unlimited() below.
Timeouts exist to stop a borrowed or forgotten browser from carrying somebody
else's ARC around; that reasoning applies to guests, not to the person whose
machine, keys and life this is. The owner signs in and stays signed in.

Sessions are stored under sha256(id), never the id itself. Anyone who reads
sessions.json off a backup learns that sessions exist and whose they are, but
cannot mint a cookie from it.
"""

import os
import json
import time
import hashlib
import secrets
import threading
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "sessions.json"

# Four hours is the ceiling when the cap is on at all, not merely the default.
MAX_HOURS_CEILING = 4.0

# 0 (the default) means no absolute cap — idle is the only clock.
_configured_hours = float(os.getenv("ARC_SESSION_MAX_HOURS", "0") or 0)
MAX_AGE = min(_configured_hours, MAX_HOURS_CEILING) * 3600 if _configured_hours > 0 else 0
IDLE_AGE = float(os.getenv("ARC_SESSION_IDLE_MINUTES", "30") or 30) * 60

# How long a session survives after the browser says it is leaving. Not zero:
# a reload fires the same event as a close, so ending the session the instant
# the page goes away would log you out every time you refreshed. The window only
# has to be long enough for the page to come back — a minute is generous.
LEAVE_GRACE = float(os.getenv("ARC_SESSION_LEAVE_GRACE_SECONDS", "60") or 60)
CLAMPED = _configured_hours > MAX_HOURS_CEILING

# How many live sessions one address may hold at once. A ninth sign-in drops
# whichever has gone longest without being used, not the oldest one — the
# desktop you use daily should outlive a hotel laptop you signed in on once.
#
# This exists because of the exemption above. Every sign-in mints a session and
# a Google token file beside it; the clocks used to clear both away within half
# an hour of the browser going quiet. Nothing clears an unlimited session, so
# without a cap a year of signing in on new phones, fresh profiles and cleared
# cookies leaves a heap of live refresh tokens on disk that nothing references.
# Eight is well past the number of devices anyone actually uses at once, so in
# practice this only ever collects litter.
MAX_PER_ACCOUNT = int(os.getenv("ARC_MAX_SESSIONS_PER_ACCOUNT", "8") or 8)

# Writing to disk on every request would mean a file write every four seconds
# from the screen-watch poll alone. last_seen is authoritative in memory; the
# file only needs to be close enough to survive a restart.
PERSIST_EVERY = 60

_lock = threading.RLock()
_sessions: dict[str, dict] = {}
_loaded = False
_last_persist = 0.0

# run.py registers a hook here so a session's Google token file is deleted
# with the session, rather than outliving it on disk.
_on_evict = None

# Addresses whose sessions never expire. Empty here on purpose: this module
# knows nothing about who owns the instance, so run.py fills it in from
# ARC_ALLOWED_EMAILS minus ARC_GUEST_EMAILS. Nobody is unlimited by default,
# which is the right way round — a mistake leaves a session too short, not
# for ever.
_unlimited: set[str] = set()


def set_evict_hook(fn):
    global _on_evict
    _on_evict = fn


def set_unlimited(emails):
    """Name the accounts that are exempt from both clocks.

    Idle and absolute timeouts protect against a session outliving the person
    using it — a shared laptop, a phone left on a table, a cookie stolen from a
    guest. The owner is a different case: it is their machine, their keys and
    their data, and signing in again every half hour is a cost with no matching
    benefit. Their sessions are still revocable (revoke_all, or deleting
    sessions.json), which is what actually ends one when it has to be ended.
    """
    global _unlimited
    _unlimited = {(e or "").strip().lower() for e in (emails or ()) if (e or "").strip()}


def unlimited(email: str) -> bool:
    return (email or "").strip().lower() in _unlimited


def key_for(sid: str) -> str:
    """The storage key for a session id. Also names its Google token file, so
    that filename is no longer a live credential sitting in a directory."""
    return hashlib.sha256(sid.encode()).hexdigest()


def _load():
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _sessions.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        # A corrupt store must not lock everyone out permanently, and must not
        # fall open either: start empty, so everyone simply signs in again.
        _sessions.clear()


def _write():
    global _last_persist
    _last_persist = time.time()
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_sessions), encoding="utf-8")
        os.replace(tmp, STORE)          # atomic; never a half-written store
    except Exception:
        pass


def _evict(key: str):
    _sessions.pop(key, None)
    if _on_evict:
        try:
            _on_evict(key)
        except Exception:
            pass


def _expired(rec: dict, now: float) -> bool:
    # The owner's session has no clock on it at all — not idle, not absolute,
    # and closing the tab is not a logout either. Checked first so none of the
    # three below can quietly re-introduce one.
    if unlimited(rec.get("email", "")):
        return False
    # MAX_AGE of 0 means no absolute cap. Testing it before comparing matters:
    # without the guard, "age > 0" is true the instant a session is created and
    # turning the cap off would log everybody out on their very next request.
    if MAX_AGE and now - rec.get("created", 0) > MAX_AGE:
        return True
    # The browser told us it was going. Once the grace window passes without it
    # coming back, that is a logout — leaving the site ends the session rather
    # than leaving it lying around for the idle clock to find half an hour later.
    left = rec.get("left_at") or 0
    if left and now - left > LEAVE_GRACE:
        return True
    return now - rec.get("last_seen", 0) > IDLE_AGE


def _cap_account(email: str):
    """Keep only the most recently used sessions for one address.

    Sorted by last_seen, so what goes is whatever has been sitting unused
    longest — and since the caller has just created one, the new session is
    never the one dropped. Eviction takes the Google token with it, which is
    the point: an unreachable session must not leave a live credential behind.
    """
    if MAX_PER_ACCOUNT <= 0:
        return
    who = (email or "").strip().lower()
    mine = sorted(((r.get("last_seen", 0), k) for k, r in _sessions.items()
                   if (r.get("email") or "").strip().lower() == who), reverse=True)
    for _, key in mine[MAX_PER_ACCOUNT:]:
        _evict(key)


def sweep():
    """Drop everything past either clock. Cheap; the store holds a handful."""
    with _lock:
        _load()
        now = time.time()
        dead = [k for k, r in _sessions.items() if _expired(r, now)]
        for key in dead:
            _evict(key)
        # Persist immediately rather than waiting for whatever writes next.
        # Eviction has already deleted the Google token on disk, so leaving the
        # record in the file would survive a restart as a session that exists
        # but has no credentials behind it.
        if dead:
            _write()


def create(email: str, user_agent: str = "") -> str:
    """Start a session for an address that has already passed the allowlist.
    Returns the raw id — the only time it exists outside the cookie."""
    with _lock:
        _load()
        sweep()
        sid = secrets.token_urlsafe(32)
        now = time.time()
        _sessions[key_for(sid)] = {
            "email": email,
            "created": now,
            "last_seen": now,
            "ua": (user_agent or "")[:120],
        }
        _cap_account(email)
        _write()
        return sid


def validate(sid: str, touch: bool = True):
    """The session record if it is live, else None.

    touch=True refreshes the idle clock — but never the absolute one, which is
    what makes the cap a cap when it is switched on.

    touch=False checks a session without extending it, and is what makes idle
    expiry mean anything. The HUD polls several endpoints on a timer of its
    own; if those counted as use, the only way to ever go idle would be to
    close the browser. Background requests still get served on a live session,
    they just don't argue that somebody is there.
    """
    if not sid:
        return None
    with _lock:
        _load()
        key = key_for(sid)
        rec = _sessions.get(key)
        if not rec:
            return None
        now = time.time()
        if _expired(rec, now):
            _evict(key)
            _write()
            return None
        if touch:
            rec["last_seen"] = now
            # They came back inside the grace window — a reload, or a tab
            # reopened. Cancel the pending departure.
            if rec.pop("left_at", None) is not None:
                _write()
            elif now - _last_persist > PERSIST_EVERY:
                _write()
        return dict(rec)


def mark_left(sid: str) -> bool:
    """The browser is going away — sent by the page as it unloads.

    Deliberately not an immediate revoke. The same event fires on a refresh, so
    this only starts a short countdown; anything that proves somebody is still
    there cancels it in validate() above.
    """
    if not sid:
        return False
    with _lock:
        _load()
        rec = _sessions.get(key_for(sid))
        if not rec:
            return False
        # Nothing to start a countdown for, and no reason to write to disk every
        # time the owner reloads a page.
        if unlimited(rec.get("email", "")):
            return False
        rec["left_at"] = time.time()
        _write()
        return True


def revoke(sid: str):
    with _lock:
        _load()
        _evict(key_for(sid))
        _write()


def revoke_all():
    with _lock:
        _load()
        for key in list(_sessions):
            _evict(key)
        _write()


def describe(sid: str):
    """What the HUD needs to warn before the session ends under someone.

    Deliberately does NOT touch: this is the status poll, and a session that
    stays alive because something asked how long it had left would make the
    countdown a lie about itself.
    """
    rec = validate(sid, touch=False)
    if not rec:
        return None
    # An unlimited session has nothing to count down to, so every clock reads
    # None rather than a time that will never arrive. The HUD already treats a
    # missing expires_at as "no warning to show".
    free = unlimited(rec["email"])
    return {
        "email": rec["email"],
        "unlimited": free,
        # None when there is no absolute cap.
        "expires_at": None if free else ((rec["created"] + MAX_AGE) if MAX_AGE else None),
        "idle_expires_at": None if free else rec["last_seen"] + IDLE_AGE,
        "max_hours": None if free else ((MAX_AGE / 3600) if MAX_AGE else None),
        "idle_minutes": None if free else IDLE_AGE / 60,
    }


def live_keys() -> set:
    """Storage keys of every live session — what a Google token file must be
    named after to still belong to somebody."""
    with _lock:
        _load()
        sweep()
        return set(_sessions)


def count() -> int:
    with _lock:
        _load()
        return len(_sessions)
