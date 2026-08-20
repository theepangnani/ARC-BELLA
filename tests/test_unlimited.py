# -*- coding: utf-8 -*-
"""The owner is not on a timer; guests still are.

Timeouts exist so a session cannot outlive the person using it — a borrowed
laptop, a phone left on a table, a stolen cookie. That reasoning covers guests
and not the person whose machine, keys and data this is. So both clocks are
skipped for ARC_ALLOWED_EMAILS minus ARC_GUEST_EMAILS, and kept for everyone
else.

The two things that must NOT come with that: a guest quietly inheriting the
exemption, and a session that cannot be ended. Both are checked below.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"
os.environ["ARC_SESSION_MAX_HOURS"] = "0"
os.environ["ARC_SESSION_IDLE_MINUTES"] = "30"
os.environ["ARC_SESSION_LEAVE_GRACE_SECONDS"] = "60"

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def age(sid, idle_minutes=0, created_hours_ago=0, left_seconds_ago=None):
    """Move a session's clocks back, which is what time would have done."""
    rec = session._sessions[session.key_for(sid)]
    now = time.time()
    rec["last_seen"] = now - idle_minutes * 60
    if created_hours_ago:
        rec["created"] = now - created_hours_ago * 3600
    if left_seconds_ago is not None:
        rec["left_at"] = now - left_seconds_ago


print("Who is exempt, and who is not:")
check("the owner is", sorted(run.OWNER_EMAILS), ["owner@example.com"])
truthy("...and the flag is on by default", run.OWNER_UNLIMITED)
truthy("session.unlimited agrees", session.unlimited("owner@example.com"))
truthy("  and is case-insensitive", session.unlimited("Owner@Example.COM"))
check("the guest is NOT, despite being on the allowlist",
      session.unlimited("guest@example.com"), False)
check("nor is a stranger", session.unlimited("someone@else.com"), False)
check("nor is nobody at all", session.unlimited(""), False)

with TestClient(run.app) as client:
    owner = session.create("owner@example.com", "desktop")
    guest = session.create("guest@example.com", "phone")
    O, G = {run.COOKIE: owner}, {run.COOKIE: guest}

    print("\nThe idle clock does not apply to the owner:")
    age(owner, idle_minutes=31)
    truthy("31 minutes idle -> still live", session.validate(owner, touch=False))
    check("  and still served", client.get("/api/health", cookies=O).status_code, 200)
    age(owner, idle_minutes=60 * 24 * 30)
    truthy("a month idle -> still live", session.validate(owner, touch=False))
    check("  and still served", client.get("/api/health", cookies=O).status_code, 200)

    print("\nNor does the absolute cap, even switched on:")
    saved = session.MAX_AGE
    session.MAX_AGE = 4 * 3600
    age(owner, created_hours_ago=500, idle_minutes=0)
    truthy("500 hours since sign-in -> still live", session.validate(owner, touch=False))
    session.MAX_AGE = saved

    print("\nNor does closing the tab:")
    # The leave beacon starts a countdown for everyone else. For the owner
    # there is nothing to count: shutting the lid is not a sign-out.
    check("the beacon declines to record a departure",
          session.mark_left(owner), False)
    check("  nothing was written on the record",
          "left_at" in session._sessions[session.key_for(owner)], False)
    age(owner, left_seconds_ago=99999)   # forced on, to prove it is ignored
    truthy("even a stale left_at is ignored", session.validate(owner, touch=False))
    session._sessions[session.key_for(owner)].pop("left_at", None)

    print("\n/api/session tells the truth about it:")
    info = client.get("/api/session", cookies=O).json()
    check("unlimited", info.get("unlimited"), True)
    check("no absolute expiry to count down to", info.get("expires_at"), None)
    check("no idle expiry either", info.get("idle_expires_at"), None)
    check("no idle_minutes to quote", info.get("idle_minutes"), None)
    check("the email is still reported", info.get("email"), "owner@example.com")

    print("\nThe browser's copy of the cookie is not the weak link:")

    class FakeResp:
        def __init__(self):
            self.kw = {}

        def set_cookie(self, name, value, **kw):
            self.kw = kw

    saved = session.MAX_AGE
    session.MAX_AGE = 4 * 3600      # the case that used to shorten it
    r = FakeResp()
    run.set_session_cookie(r, owner, client.get("/api/health", cookies=O).request,
                           "owner@example.com")
    check("owner's cookie outlives the cap (400 days)",
          r.kw.get("max_age"), run.COOKIE_MAX_AGE_UNLIMITED)
    r2 = FakeResp()
    run.set_session_cookie(r2, guest, client.get("/api/health", cookies=O).request,
                           "guest@example.com")
    check("guest's cookie still follows the cap", r2.kw.get("max_age"), 4 * 3600)
    truthy("both still HttpOnly", r.kw.get("httponly") and r2.kw.get("httponly"))
    check("both still SameSite=Lax", (r.kw.get("samesite"), r2.kw.get("samesite")),
          ("lax", "lax"))
    session.MAX_AGE = saved

    print("\nGuests are untouched by any of this:")
    age(guest, idle_minutes=31)
    check("31 minutes idle -> signed out", session.validate(guest, touch=False), None)
    guest = session.create("guest@example.com", "phone")
    G = {run.COOKIE: guest}
    truthy("a fresh guest session works", session.validate(guest, touch=False))
    truthy("the beacon still records their departure", session.mark_left(guest))
    age(guest, left_seconds_ago=120)    # past the 60s grace
    check("and they are gone once the grace passes",
          session.validate(guest, touch=False), None)

    print("\nAn unlimited session is still a REVOCABLE session:")
    owner2 = session.create("owner@example.com", "desktop")
    O2 = {run.COOKIE: owner2}
    check("live to start with", client.get("/api/health", cookies=O2).status_code, 200)
    check("logout ends it", client.post("/api/logout", cookies=O2).status_code, 200)
    check("  really gone", session.validate(owner2, touch=False), None)
    check("  and refused", client.get("/api/health", cookies=O2).status_code, 401)

    owner3 = session.create("owner@example.com", "desktop")
    O3 = {run.COOKIE: owner3}
    session.revoke_all()
    check("revoke_all still clears everything",
          client.get("/api/health", cookies=O3).status_code, 401)
    check("  including the one that never expires",
          session.validate(owner, touch=False), None)

print("\nThe sign-in page no longer promises a timeout that won't happen:")
truthy("it says the owner stays signed in", "stays signed in" in run.LOGIN_HTML)
# "signed out after 30 minutes idle" IS still on the page -- attached to
# guests, where it is true. What must be gone is the old sentence that said it
# of whoever is reading, which was the owner.
check("and no longer says it of the reader",
      "You stay signed in while you're using it, and are signed out" in run.LOGIN_HTML,
      False)
truthy("guests are still told their own limit", "guests are signed out" in run.LOGIN_HTML)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
