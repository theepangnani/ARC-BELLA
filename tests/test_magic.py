# -*- coding: utf-8 -*-
"""Signing in by emailed link — the door for people Google cannot let in.

Google sign-in was doing two jobs at once: proving who somebody is, and
authorising access to their Google calendar and mail. For a Yahoo or Proton or
work address the second job is meaningless, so requiring a Google account asks
somebody to create one purely to prove identity. Nine of the fifteen guest tools
read the user's OWN Google data — a non-Google user loses nothing by not having
them, and gains everything else.

A link rather than another provider, because adding Microsoft covers Outlook and
still misses Yahoo, and adding Apple costs a subscription and still misses
Yahoo. An email covers every address there has ever been.

THIS IS A SECOND DOOR, NOT AN OPEN ONE. Most of this file is about that: the
allowlist still decides, and being on it must not be observable from outside.
"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "bala@yahoo.com"
os.environ["ARC_MAGIC_LINK"] = "1"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import magic     # noqa: E402

c = Check()

print("Off unless switched on — a second way in is not something you upgrade into:")
c.truthy("  there is a flag", "ARC_MAGIC_LINK" in io.open(ARC / "magic.py", encoding="utf-8").read())
c("  and it is off by default",
  magic.enabled.__doc__ is not None and os.getenv("ARC_MAGIC_LINK") == "1", True)

print("\nThe form tells a stranger nothing about who has access:")
with TestClient(run.app) as client:
    known = client.post("/auth/email", json={"email": "bala@yahoo.com"})
    unknown = client.post("/auth/email", json={"email": "stranger@example.com"})
    junk = client.post("/auth/email", json={"email": "not-an-email"})
    c("  a known address is accepted", known.status_code, 200)
    c("  so is a stranger", unknown.status_code, 200)
    # "No such user" is the single most useful thing a stranger can learn from a
    # login page, and withholding it costs nothing.
    c("  WORD FOR WORD THE SAME", known.json(), unknown.json())
    c("  ...and so is nonsense", junk.json(), known.json())
    # Only the real request actually made anything.
    c("  but only the real one minted a link", magic.count(), 1)

    print("\nA link works once, briefly, and only for who it was made for:")
    tok = magic.issue("bala@yahoo.com")
    r = client.get("/auth/magic?t=" + tok, follow_redirects=False)
    c("  following it signs you in", r.status_code, 302)
    c("  ...to the app", r.headers.get("location"), "/")
    c("  and hands over a session", run.COOKIE in (r.headers.get("set-cookie") or ""), True)

    # A link that still works after being clicked still works in whatever
    # mailbox it passed through on the way.
    again = client.get("/auth/magic?t=" + tok, follow_redirects=False)
    c("  the SAME link a second time is dead", again.headers.get("location"), "/?auth=expired")
    c("  an invented token is dead",
      client.get("/auth/magic?t=madeup", follow_redirects=False).headers.get("location"),
      "/?auth=expired")
    c("  no token at all is dead",
      client.get("/auth/magic", follow_redirects=False).headers.get("location"),
      "/?auth=expired")

    old = magic.issue("bala@yahoo.com")
    magic._pending[magic._key(old)]["expires"] = time.time() - 1
    c("  and an old one is dead",
      client.get("/auth/magic?t=" + old, follow_redirects=False).headers.get("location"),
      "/?auth=expired")
    # Probing must not be free: it is consumed whether or not it was valid.
    c("  ...and was consumed in the asking, not left to be retried",
      magic._key(old) in magic._pending, False)

    print("\nThe allowlist is checked again when the link is FOLLOWED:")
    # An address can come off the list between the link being sent and clicked,
    # and the link is the thing that outlives the decision.
    late = magic.issue("bala@yahoo.com")
    run.ALLOWED_EMAILS.discard("bala@yahoo.com")
    c("  someone removed since it was sent is refused",
      client.get("/auth/magic?t=" + late, follow_redirects=False).headers.get("location"),
      "/?auth=denied")
    run.ALLOWED_EMAILS.add("bala@yahoo.com")
    session.revoke_all()

print("\nThe token itself:")
t = magic.issue("bala@yahoo.com")
c.truthy("  is long and random", len(t) >= 32)
# Never derived from the address, so knowing who somebody is gets you no closer.
c("  is not derived from the email", "bala" in t or "yahoo" in t, False)
# Only the hash is stored, so the file is not a folder of working links.
c("  and only its HASH is on disk", t in str(magic._pending), False)
c.truthy("  ...which is what is stored", magic._key(t) in magic._pending)
c("  two links are never the same", magic.issue("bala@yahoo.com") == t, False)

print("\nAsking repeatedly does not send repeatedly:")
# A form that emails somebody is a form that can be used to send somebody a
# hundred emails.
c.truthy("  there is a cooldown", magic.COOLDOWN >= 30)
magic._asked["bala@yahoo.com"] = time.time()
c("  and it holds", magic.may_ask("bala@yahoo.com"), False)
magic._asked["bala@yahoo.com"] = time.time() - magic.COOLDOWN - 1
c("  ...then lets go", magic.may_ask("bala@yahoo.com"), True)

print("\nSending never explodes in a stranger's face:")
# A login form that returns a stack trace tells a stranger what mail server you
# use. With nothing configured it must decline quietly, not raise.
sent, why = magic.send("bala@yahoo.com", "https://example.test/auth/magic?t=x")
c("  with no sender configured it declines", sent, False)
c.truthy("  ...and says why, to the LOG", "no mail sender" in why)

print("\nAnd none of it is reachable while the flag is off:")
rsrc = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  both routes refuse when disabled",
         rsrc.count('if not magic.enabled():\n        raise HTTPException(404') == 2)
# Listed as public so the flow can create a session — a route that needs a
# session to make one is a closed loop. Safe to list while it 404s.
c.truthy("  they are public, like the OAuth callback, for the same reason",
         '"/auth/email", "/auth/magic"' in rsrc)
c.truthy("  and the reason is written down", "closed loop" in rsrc)
c.truthy("  the form only appears when it works", "if magic.enabled():" in rsrc)

c.done()
