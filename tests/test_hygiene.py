# -*- coding: utf-8 -*-
"""What has to stay true once sessions stop expiring.

Every timeout that got removed for the owner was also doing a second job:
clearing away the Google refresh token that sits beside each session. With no
clock left, three things have to be checked deliberately —

  1. sessions cannot pile up per account for ever;
  2. taking an address out of .env still takes access away;
  3. a token file no session points at gets deleted rather than lingering.
"""
import io
import json
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

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

ok = True
GDIR = run.GOOGLE_DIR


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def token_for(sid, text='{"token": "pretend"}'):
    """Write the token file a real sign-in would have written."""
    p = run.google_path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    io.open(p, "w", encoding="utf-8").write(text)
    return p


session.revoke_all()
for f in GDIR.glob("*.json"):
    f.unlink()

print("A never-expiring session must not mean a never-ending pile of them:")
check("the cap is on by default", session.MAX_PER_ACCOUNT, 8)
sids = []
base = time.time() - 1000
for i in range(12):
    s = session.create("owner@example.com", "device %d" % i)
    token_for(s)
    # Give each one a distinct idle clock, in order. Twelve sign-ins in a tight
    # loop land inside the same clock tick on a fast machine, and sessions that
    # tie on last_seen are dropped in whatever order their storage keys sort —
    # so without this the assertions below are testing a hash, not
    # least-recently-used. CI found that; this machine never would have.
    session._sessions[session.key_for(s)]["last_seen"] = base + i
    sids.append(s)
check("12 sign-ins -> 8 live sessions", session.count(), 8)
check("the newest survived", bool(session.validate(sids[-1], touch=False)), True)
check("the oldest did not", session.validate(sids[0], touch=False), None)
check("and neither did the next four", [session.validate(s, touch=False) for s in sids[:4]],
      [None] * 4)
# The whole point of evicting is that the credential goes too.
check("the dropped sessions' google tokens were deleted",
      [run.google_path(s).exists() for s in sids[:4]], [False] * 4)
truthy("the surviving ones kept theirs", all(run.google_path(s).exists() for s in sids[-8:]))
check("no token file is left behind for a dead session", len(list(GDIR.glob("*.json"))), 8)

print("\nThe cap is per account, not global:")
g = session.create("guest@example.com", "phone")
check("a guest signing in evicts nobody", session.count(), 9)
truthy("and the owner's newest is untouched", session.validate(sids[-1], touch=False))

print("\nLeast-recently-USED is what goes, not oldest-created:")
session.revoke_all()
a = session.create("owner@example.com", "old but active")
rest = [session.create("owner@example.com", "newer %d" % i) for i in range(7)]
session._sessions[session.key_for(a)]["last_seen"] += 9999      # used just now
session._sessions[session.key_for(rest[0])]["last_seen"] -= 9999  # untouched for ages
session.create("owner@example.com", "the ninth")
truthy("the old-but-active session survived", session.validate(a, touch=False))
check("the idle one was dropped instead", session.validate(rest[0], touch=False), None)

print("\nTaking an address out of the allowlist ends its access NOW:")
session.revoke_all()
with TestClient(run.app) as client:
    sid = session.create("owner@example.com", "desktop")
    token_for(sid)
    C = {run.COOKIE: sid}
    check("signed in to start with", client.get("/api/health", cookies=C).status_code, 200)

    saved = run.ALLOWED_EMAILS
    run.ALLOWED_EMAILS = {"someone@else.com"}     # as if .env changed and ARC restarted
    check("refused on the very next request",
          client.get("/api/health", cookies=C).status_code, 401)
    check("  the session was revoked, not merely refused",
          session.validate(sid, touch=False), None)
    check("  and its google token went with it", run.google_path(sid).exists(), False)
    run.ALLOWED_EMAILS = saved

    print("\n  (a still-allowed session is unaffected by that check)")
    sid2 = session.create("owner@example.com", "desktop")
    C2 = {run.COOKIE: sid2}
    for _ in range(3):
        check("  still served", client.get("/api/health", cookies=C2).status_code, 200)
    truthy("  and still live", session.validate(sid2, touch=False))

print("\nOrphaned google tokens are cleared at startup:")
session.revoke_all()
for f in GDIR.glob("*.json"):
    f.unlink()
live = session.create("owner@example.com", "desktop")
token_for(live)
orphan = GDIR / ("a" * 64 + ".json")            # a session that no longer exists
io.open(orphan, "w", encoding="utf-8").write("{}")
none = GDIR / "none.json"                        # the deliberate dead end
io.open(none, "w", encoding="utf-8").write("{}")
odd = GDIR / "notes.json"                        # nothing to do with us
io.open(odd, "w", encoding="utf-8").write("{}")

check("one orphan cleared", run.prune_orphan_google_tokens(), 1)
check("  the orphan is gone", orphan.exists(), False)
truthy("  the live session's token is untouched", run.google_path(live).exists())
truthy("  none.json is left alone", none.exists())
truthy("  and so is anything not named like a session key", odd.exists())
check("running it again finds nothing", run.prune_orphan_google_tokens(), 0)

print("\n  ...which is what makes 'delete sessions.json' actually work:")
# The README tells you to delete the file to sign everyone out. Before this it
# signed them out and left their refresh tokens on disk for ever.
os.remove(session.STORE)
session._sessions.clear()
session._loaded = False
check("every token goes with the store", run.prune_orphan_google_tokens(), 1)
check("  nothing left", len(list(GDIR.glob("*.json"))), 2)   # none.json + notes.json

for f in [none, odd]:
    f.unlink(missing_ok=True)
session.revoke_all()

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
