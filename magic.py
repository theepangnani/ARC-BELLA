#!/usr/bin/env python3
"""Sign-in by email link, for the people Google cannot let in.

WHY THIS EXISTS. Google sign-in was doing two different jobs at once: proving
who somebody is, and authorising access to their Google calendar and mail. For
anybody with a Yahoo or Proton or work address those are not the same job at
all — the second one is meaningless, because they have no Google data to reach,
so requiring a Google account is asking them to create one purely to prove
identity. Nine of the fifteen guest tools read the user's OWN Google data; a
Yahoo user loses nothing by not having them, and gains everything else.

WHY A LINK RATHER THAN ANOTHER PROVIDER. Adding Microsoft covers Outlook and
still misses Yahoo. Adding Apple costs a developer subscription and still misses
Yahoo. An emailed link covers every address there has ever been, in one
mechanism, with no third-party app to register and no consent screen to explain.

WHAT IT IS NOT. It is not a way around the allowlist. A link is only ever sent
to an address already on it — this is a second DOOR, not an open one. Everything
below is written so that being on the list is not observable from outside:
requesting a link for an unknown address takes the same path, the same time and
the same words as requesting one for a known address, because "no such user" is
the answer that turns a login form into a directory of who has access.

THE TOKEN. Random, never derived from the email, so it cannot be guessed from
knowing who somebody is. Only its HASH is stored, so the file is not a folder
of working links. Single use — consumed on the way in, because a link that
still works after it has been clicked is a link that still works in the sent-
folder of whoever the mail passed through. Ten minutes to live, because the one
attack an emailed credential invites is somebody reading the mailbox later.
"""

import hashlib
import hmac
import io
import json
import os
import secrets
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "magic.json"

# Ten minutes. Long enough for mail to arrive and somebody to notice it, short
# enough that a link sitting in an inbox tomorrow is already dead.
TTL = int(os.getenv("ARC_MAGIC_TTL_MINUTES", "10")) * 60

# One address may ask for a link this often. Without it, a form that emails
# somebody is a form that can be used to send somebody a hundred emails.
COOLDOWN = int(os.getenv("ARC_MAGIC_COOLDOWN_SECONDS", "60"))

_lock = threading.RLock()
_pending: dict = {}
_asked: dict = {}          # email -> when a link was last sent
_loaded = False


def enabled() -> bool:
    """Off unless switched on. A second way into the app is not something to
    acquire by upgrading."""
    return os.getenv("ARC_MAGIC_LINK", "").strip().lower() in ("1", "true", "yes", "on")


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load():
    global _loaded, _pending
    if _loaded:
        return
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        _pending = data if isinstance(data, dict) else {}
    except Exception:
        _pending = {}
    _loaded = True


def _write():
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_name(STORE.name + ".tmp")
        tmp.write_text(json.dumps(_pending), encoding="utf-8")
        os.replace(tmp, STORE)      # atomic, like every other store here
    except Exception:
        pass


def _sweep(now=None):
    now = now or time.time()
    dead = [k for k, v in _pending.items() if v.get("expires", 0) <= now]
    for k in dead:
        _pending.pop(k, None)
    return len(dead)


def may_ask(email: str) -> bool:
    """Whether this address is allowed to be sent another link yet."""
    e = (email or "").strip().lower()
    with _lock:
        last = _asked.get(e, 0)
        return (time.time() - last) >= COOLDOWN


def issue(email: str) -> str:
    """A fresh single-use token for an address that has already been checked
    against the allowlist by the caller. Returns the raw token — the only time
    it exists outside the email."""
    e = (email or "").strip().lower()
    token = secrets.token_urlsafe(32)
    with _lock:
        _load()
        _sweep()
        _pending[_key(token)] = {"email": e, "expires": time.time() + TTL}
        _asked[e] = time.time()
        _write()
    return token


def consume(token: str):
    """The address this token belongs to, and the token dies in the asking.

    Consumed whether or not it was still valid, so a link cannot be probed
    repeatedly — and consumed BEFORE the expiry is checked, so an expired one
    cannot be held onto in the hope of a clock change.
    """
    if not token:
        return None
    with _lock:
        _load()
        rec = _pending.pop(_key(token), None)
        _sweep()
        _write()
    if not rec:
        return None
    if rec.get("expires", 0) <= time.time():
        return None
    return rec.get("email") or None


def count() -> int:
    with _lock:
        _load()
        return len(_pending)


# --------------------------------------------------------------------------
# sending it
# --------------------------------------------------------------------------

def sender_ready() -> bool:
    return bool(os.getenv("ARC_SMTP_HOST") and os.getenv("ARC_SMTP_USER")
                and os.getenv("ARC_SMTP_PASS"))


def send(email: str, link: str) -> tuple:
    """(sent, why-not). Never raises — a login form that returns a stack trace
    is a login form that tells a stranger what mail server you use."""
    if not sender_ready():
        return False, "no mail sender configured"
    import smtplib
    from email.message import EmailMessage
    host = os.getenv("ARC_SMTP_HOST", "")
    port = int(os.getenv("ARC_SMTP_PORT", "587"))
    user = os.getenv("ARC_SMTP_USER", "")
    frm = os.getenv("ARC_SMTP_FROM") or user
    msg = EmailMessage()
    msg["Subject"] = "Your ARC sign-in link"
    msg["From"] = frm
    msg["To"] = email
    msg.set_content(
        "Someone asked to sign in to ARC with this address.\n\n"
        f"{link}\n\n"
        f"The link works once and expires in {TTL // 60} minutes.\n"
        "If this wasn't you, nothing has happened and you can ignore it.\n")
    try:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, os.getenv("ARC_SMTP_PASS", ""))
            s.send_message(msg)
        return True, ""
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
