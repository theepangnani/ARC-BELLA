# -*- coding: utf-8 -*-
"""End-to-end through the real FastAPI app, with real sessions.

The unit test proved the functions. This proves the WIRING — that a guest's
actual HTTP request gets refused and the owner's identical request does not.
Those are different claims and only this one is about what ships.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient  # noqa: E402
import run  # noqa: E402
import session  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "   got %r want %r" % (got, want)))


client = TestClient(run.app)

# Real sessions, minted the same way the OAuth callback mints them.
owner_sid = session.create("owner@example.com", "test-owner")
guest_sid = session.create("guest@example.com", "test-guest")
OWNER = {run.COOKIE: owner_sid}
GUEST = {run.COOKIE: guest_sid}

print("Sessions are real and the tier is read from the SERVER record:")
check("owner session valid", bool(session.validate(owner_sid)), True)
check("guest session valid", bool(session.validate(guest_sid)), True)

print("\nRoutes that touch the owner's own data — guest 403, owner 200:")
OWNER_ONLY = ["/api/reminders/due", "/api/alerts/due", "/api/display"]
for path in OWNER_ONLY:
    check("%-22s guest" % path, client.get(path, cookies=GUEST).status_code, 403)
    check("%-22s owner" % path, client.get(path, cookies=OWNER).status_code, 200)

check("/api/push/test         guest", client.post("/api/push/test", cookies=GUEST).status_code, 403)
check("/api/push/test         owner",
      client.post("/api/push/test", cookies=OWNER).status_code, 200)

print("\nSigned out is still 401, not 403 — the gate order is unchanged:")
for path in OWNER_ONLY:
    check("%-22s anon" % path, client.get(path).status_code, 401)

print("\nShared routes stay open to both (public data only):")
for path in ["/api/stocks?symbols=AAPL", "/api/stock-search?q=apple", "/api/push/status"]:
    check("%-26s guest" % path, client.get(path, cookies=GUEST).status_code, 200)
    check("%-26s owner" % path, client.get(path, cookies=OWNER).status_code, 200)

print("\n/api/health tells each account the truth about itself:")
gh = client.get("/api/health", cookies=GUEST).json()
oh = client.get("/api/health", cookies=OWNER).json()
check("guest: guest flag set", gh.get("guest"), True)
check("guest: telegram reported off", gh.get("telegram"), False)
check("guest: computer reported off", gh.get("computer"), False)
check("guest: monitors reported 0", gh.get("monitors"), 0)
check("owner: guest flag clear", oh.get("guest"), False)
check("owner: telegram truthful", oh.get("telegram"), run.tg.connected())

print("\n/api/session reports the tier:")
check("guest", client.get("/api/session", cookies=GUEST).json().get("guest"), True)
check("owner", client.get("/api/session", cookies=OWNER).json().get("guest"), False)

print("\nThe guest cannot promote itself by asking:")
# Every client-controlled knob that might look like a way in.
for body in [{"guest": False}, {"local": True}, {"allow_actions": True},
             {"email": "owner@example.com"}]:
    r = client.get("/api/display", cookies=GUEST, params=body)
    check("/api/display with %-28s" % str(body), r.status_code, 403)

print("\nA revoked guest session is simply gone:")
session.revoke(guest_sid)
check("guest revoked -> 401", client.get("/api/display", cookies=GUEST).status_code, 401)
check("owner unaffected", client.get("/api/display", cookies=OWNER).status_code, 200)

session.revoke(owner_sid)
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
