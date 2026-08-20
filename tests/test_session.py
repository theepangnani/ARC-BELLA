# -*- coding: utf-8 -*-
"""Sessions end when YOU stop, not when the tab stops.

The claim being tested is behavioural, so the clocks are moved rather than
waited on: last_seen/created are edited directly, which is exactly what the
passage of time would have done.
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
os.environ["ARC_SESSION_MAX_HOURS"] = "0"      # the new default: no hard cap
os.environ["ARC_SESSION_IDLE_MINUTES"] = "30"

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


def age_session(sid, idle_minutes=None, created_hours_ago=None):
    """Rewind a session's clocks as though that much time had passed."""
    rec = session._sessions[session.key_for(sid)]
    if idle_minutes is not None:
        rec["last_seen"] = time.time() - idle_minutes * 60
    if created_hours_ago is not None:
        rec["created"] = time.time() - created_hours_ago * 3600
    return rec


client = TestClient(run.app)

print("Config: no absolute cap, 30 minute idle")
check("MAX_AGE disabled", session.MAX_AGE, 0)
check("IDLE_AGE is 30 min", session.IDLE_AGE, 1800.0)

print("\nA brand-new session is NOT instantly expired (the 0-cap trap):")
sid = session.create("owner@example.com", "test")
check("valid immediately", bool(session.validate(sid, touch=False)), True)
check("still valid 12 hours after sign-in", (
    age_session(sid, created_hours_ago=12) and
    bool(session.validate(sid, touch=False))), True)

print("\nBackground polling does NOT keep it alive — this is the whole point:")
COOKIES = {run.COOKIE: sid}
age_session(sid, idle_minutes=29)
for path in ["/api/health", "/api/session", "/api/reminders/due", "/api/alerts/due",
             "/api/calendar/upcoming", "/api/stocks?symbols=AAPL", "/api/push/status",
             "/api/display"]:
    before = session._sessions[session.key_for(sid)]["last_seen"]
    client.get(path, cookies=COOKIES)
    after = session._sessions[session.key_for(sid)]["last_seen"]
    check("%-32s left the idle clock alone" % path, after == before, True)

print("\n...so 30 minutes of nothing but polling signs you out:")
age_session(sid, idle_minutes=31)
check("poll on an idle-expired session -> 401",
      client.get("/api/health", cookies=COOKIES).status_code, 401)
check("session really gone", session.validate(sid, touch=False), None)

print("\nUsing it DOES keep it alive:")
sid2 = session.create("owner@example.com", "test")
C2 = {run.COOKIE: sid2}
age_session(sid2, idle_minutes=29)
before = session._sessions[session.key_for(sid2)]["last_seen"]
client.get("/", cookies=C2)                      # loading the page is a person
after = session._sessions[session.key_for(sid2)]["last_seen"]
check("loading the HUD refreshes the idle clock", after > before, True)

age_session(sid2, idle_minutes=29)
before = session._sessions[session.key_for(sid2)]["last_seen"]
client.post("/api/tts", cookies=C2, json={"text": "hello"})
after = session._sessions[session.key_for(sid2)]["last_seen"]
check("speaking refreshes the idle clock", after > before, True)

print("\nAn active session now survives past four hours (the requested change):")
age_session(sid2, created_hours_ago=9, idle_minutes=1)
check("9 hours old, recently used -> still valid",
      bool(session.validate(sid2, touch=False)), True)
check("and still served", client.get("/api/health", cookies=C2).status_code, 200)

print("\n/api/session reports no countdown when there is no cap:")
info = client.get("/api/session", cookies=C2).json()
check("expires_at is null", info.get("expires_at"), None)
check("max_hours is null", info.get("max_hours"), None)
check("idle_expires_at present", isinstance(info.get("idle_expires_at"), (int, float)), True)

print("\nThe cap still works when someone switches it back on:")
saved = session.MAX_AGE
session.MAX_AGE = 4 * 3600
sid3 = session.create("owner@example.com", "test")
age_session(sid3, created_hours_ago=5, idle_minutes=0)   # busy, but too old
check("5 hours old with a 4h cap -> expired", session.validate(sid3, touch=False), None)
sid4 = session.create("owner@example.com", "test")
age_session(sid4, created_hours_ago=3, idle_minutes=0)
check("3 hours old with a 4h cap -> valid", bool(session.validate(sid4, touch=False)), True)
session.MAX_AGE = saved

print("\nRevocation is unchanged:")
session.revoke_all()
check("everyone signed out", client.get("/api/health", cookies=C2).status_code, 401)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
