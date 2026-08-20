# -*- coding: utf-8 -*-
"""Leaving the site signs you out; refreshing it does not.

Those two actions fire the SAME browser event, so the whole design rests on the
grace window and on the return trip cancelling the countdown. If that's wrong,
pressing F5 logs you out — which is worse than the problem being solved.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
# The owner is exempt from both clocks by default, so the clock machinery
# itself has to be tested with the exemption off. That the exemption is on
# by default, and what it does, is test_unlimited.py.
os.environ["ARC_OWNER_SESSION_UNLIMITED"] = "0"
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
          ("" if good else "   got %r want %r" % (got, want)))


def rec(sid):
    return session._sessions[session.key_for(sid)]


def rewind_departure(sid, seconds):
    """As though the browser had left that many seconds ago."""
    rec(sid)["left_at"] = time.time() - seconds


client = TestClient(run.app)
print("Grace window: %gs, idle: %gmin, no absolute cap"
      % (session.LEAVE_GRACE, session.IDLE_AGE / 60))

print("\nClosing the tab signs you out:")
sid = session.create("owner@example.com", "browser")
C = {run.COOKIE: sid}
check("signed in to start", client.get("/api/health", cookies=C).status_code, 200)
check("beacon accepted", client.post("/api/leave", cookies=C).status_code, 200)
check("departure recorded", "left_at" in rec(sid), True)
check("still valid inside the grace window", bool(session.validate(sid, touch=False)), True)
rewind_departure(sid, 61)
check("gone once the grace window passes", session.validate(sid, touch=False), None)
check("and the request is refused", client.get("/api/health", cookies=C).status_code, 401)

print("\nRefreshing the page does NOT sign you out (same browser event):")
sid = session.create("owner@example.com", "browser")
C = {run.COOKIE: sid}
client.post("/api/leave", cookies=C)                 # pagehide on refresh
check("departure pending", "left_at" in rec(sid), True)
r = client.get("/", cookies=C)                       # the page comes straight back
check("page loads", r.status_code, 200)
check("departure cancelled", "left_at" in rec(sid), False)
rewind_departure(sid, 0) if False else None
check("still signed in a minute later", bool(session.validate(sid, touch=False)), True)

print("\nBackground polling does NOT cancel a departure:")
sid = session.create("owner@example.com", "browser")
C = {run.COOKIE: sid}
client.post("/api/leave", cookies=C)
for path in ["/api/health", "/api/session", "/api/reminders/due", "/api/display"]:
    client.get(path, cookies=C)
    check("%-22s left the departure standing" % path, "left_at" in rec(sid), True)
rewind_departure(sid, 61)
check("so it still expires on schedule", session.validate(sid, touch=False), None)

print("\nActually using it cancels a departure:")
for path, how in [("/", "loading the HUD")]:
    sid = session.create("owner@example.com", "browser")
    C = {run.COOKIE: sid}
    client.post("/api/leave", cookies=C)
    client.get(path, cookies=C)
    check("%s cancels it" % how, "left_at" in rec(sid), False)

sid = session.create("owner@example.com", "browser")
C = {run.COOKIE: sid}
client.post("/api/leave", cookies=C)
client.post("/api/tts", cookies=C, json={"text": "hello"})
check("speaking cancels it", "left_at" in rec(sid), False)

print("\nThe beacon needs a real session — it can't be used to attack one:")
check("no cookie -> 401", client.post("/api/leave").status_code, 401)
check("junk cookie -> 401",
      client.post("/api/leave", cookies={run.COOKIE: "not-a-real-session"}).status_code, 401)
other = session.create("guest@example.com", "someone else")
before = dict(rec(other))
client.post("/api/leave", cookies={run.COOKIE: "not-a-real-session"})
check("another session untouched", rec(other) == before, True)

print("\nThe idle clock still backstops a beacon that never arrives:")
sid = session.create("owner@example.com", "crashed browser")
rec(sid)["last_seen"] = time.time() - 31 * 60
check("31 min silent -> signed out", session.validate(sid, touch=False), None)

session.revoke_all()
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
