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

# ---------------------------------------------------------------- the walls
# A cookie is a bearer token: whoever holds it is you. Two things follow, and
# neither existed before — a lifted cookie should not WORK somewhere else, and
# if one does, you should be able to SEE it and end it.
CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
CHROME_WIN_NEWER = CHROME_WIN.replace("Chrome/141", "Chrome/142")
EDGE_WIN = CHROME_WIN + " Edg/141.0.0.0"
FIREFOX_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:130.0) Gecko/20100101 Firefox/130.0"
CHROME_ANDROID = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36")

print("\nA session is bound to the browser it was issued to:")
# Coarse ON PURPOSE. Comparing whole user-agents signs you out every time
# Chrome updates itself, and a control that logs you out weekly is one you
# switch off.
check("a browser update is the SAME browser",
      session.ua_key(CHROME_WIN), session.ua_key(CHROME_WIN_NEWER))
check("Edge is not Chrome", session.ua_key(EDGE_WIN) == session.ua_key(CHROME_WIN), False)
check("another machine is not this one",
      session.ua_key(FIREFOX_MAC) == session.ua_key(CHROME_WIN), False)
check("and the phone is not the desktop",
      session.ua_key(CHROME_ANDROID) == session.ua_key(CHROME_WIN), False)

sid = session.create("owner@example.com", CHROME_WIN)
check("  it survives a browser update", bool(session.validate(sid, ua=CHROME_WIN_NEWER)), True)
check("  and the cookie pasted into another machine is refused",
      session.validate(sid, ua=FIREFOX_MAC), None)
# Evicted, not merely refused: it is either theft or a session that can never
# be used again, and neither is worth keeping on disk.
check("  ...and burned, not left lying there", session.validate(sid, ua=CHROME_WIN), None)

# The lockout this must never become: a request with no user-agent fingerprints
# as "unknown", which would mismatch every real browser.
sid = session.create("owner@example.com", CHROME_WIN)
check("a request with no user-agent is not locked out",
      bool(session.validate(sid, ua="")), True)
check("...nor is one whose session predates this",
      bool(session.validate(session.create("owner@example.com", ""), ua=CHROME_WIN)), True)

print("\nYou can see who is signed in, which is the half that was missing:")
session.revoke_all()
mine = session.create("owner@example.com", CHROME_WIN)
phone = session.create("owner@example.com", CHROME_ANDROID)
rows = session.list_all(mine)
check("both devices are listed", len(rows), 2)
check("and the one asking is marked", [r["current"] for r in rows].count(True), 1)
# The stored key is a hash of the cookie and still never leaves the server.
check("no row carries anything replayable",
      [r for r in rows if len(r.get("id", "")) > 12], [])
check("no row carries the cookie itself",
      [r for r in rows if mine in str(r)], [])

print("\nAnd end them — others, or all:")
check("ending others keeps this one", session.revoke_others(mine), 1)
check("  it is still live", bool(session.validate(mine, ua=CHROME_WIN)), True)
check("  the other is not", session.validate(phone, ua=CHROME_ANDROID), None)

CK = {run.COOKIE: mine}
HDRS = {"user-agent": CHROME_WIN}
check("the owner may look",
      client.get("/api/sessions", cookies=CK, headers=HDRS).status_code, 200)
check("the owner may end",
      client.post("/api/sessions/revoke", cookies=CK, headers=HDRS, json={}).status_code, 200)
guest = session.create("guest@example.com", CHROME_WIN)
GK = {run.COOKIE: guest}
check("a guest may not see the owner's devices",
      client.get("/api/sessions", cookies=GK, headers=HDRS).status_code, 403)
check("nor end them",
      client.post("/api/sessions/revoke", cookies=GK, headers=HDRS, json={}).status_code, 403)
check("  and the owner is still signed in",
      bool(session.validate(mine, ua=CHROME_WIN)), True)

# Revoking has to take Google with it, or "I signed that device out" is a
# sentence about the cookie and not about the mail it could still read.
src = open(ARC / "run.py", encoding="utf-8").read()
check("ending a session takes its Google token too",
      "session.set_evict_hook(" in src and "unlink(missing_ok=True)" in src, True)
session.revoke_all()

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
