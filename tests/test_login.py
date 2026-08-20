# -*- coding: utf-8 -*-
"""The sign-in round trip, including the exact header that broke it.

Max-Age=0 is not "no expiry" — it is "delete this cookie now". The server was
minting a perfectly good session and then telling the browser to bin the only
proof of it, so every sign-in landed back on the sign-in page. Asserting on the
raw Set-Cookie header is the only way to catch that class of bug: every
server-side check passed while it was happening.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, fake_web_credentials   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_SESSION_MAX_HOURS"] = "0"
os.environ["ARC_SESSION_IDLE_MINUTES"] = "30"

from starlette.responses import JSONResponse  # noqa: E402
from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

# credentials_web.json is gitignored, so a fresh clone and every CI runner is
# without one; and where it exists it is the developer's own. Either way the
# sign-in flow should be tested against a fixed, fake client, not a real one.
fake_web_credentials()

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "   got %r want %r" % (got, want)))


class FakeReq:
    """Just enough Request for cookie_secure()."""
    def __init__(self, https):
        self.headers = {"x-forwarded-proto": "https"} if https else {}
        self.url = type("U", (), {"scheme": "https" if https else "http"})()


print("The Set-Cookie header the browser actually receives:")
resp = JSONResponse({})
run.set_session_cookie(resp, "test-sid-12345", FakeReq(https=False))
raw = resp.raw_headers
hdr = next((v.decode() for k, v in raw if k.decode().lower() == "set-cookie"), "")
print("    " + hdr)

m = re.search(r"[Mm]ax-[Aa]ge=(-?\d+)", hdr)
check("Max-Age present", bool(m), True)
max_age = int(m.group(1)) if m else -1
check("Max-Age is NOT 0 (the bug)", max_age == 0, False)
check("Max-Age comfortably outlives the idle window",
      max_age > session.IDLE_AGE * 2, True)
check("HttpOnly set", "httponly" in hdr.lower(), True)
check("SameSite=lax set", "samesite=lax" in hdr.lower(), True)
check("no Secure over plain http", "secure" in hdr.lower(), False)

print("\nOver HTTPS (the tunnel) the Secure flag appears:")
resp2 = JSONResponse({})
run.set_session_cookie(resp2, "test-sid-12345", FakeReq(https=True))
hdr2 = next((v.decode() for k, v in resp2.raw_headers if k.decode().lower() == "set-cookie"), "")
check("Secure set over https", "secure" in hdr2.lower(), True)
check("HttpOnly still set", "httponly" in hdr2.lower(), True)

print("\nWith an absolute cap configured the cookie tracks it:")
saved = session.MAX_AGE
session.MAX_AGE = 4 * 3600
resp3 = JSONResponse({})
run.set_session_cookie(resp3, "sid", FakeReq(https=False))
hdr3 = next((v.decode() for k, v in resp3.raw_headers if k.decode().lower() == "set-cookie"), "")
check("Max-Age == 4h", int(re.search(r"[Mm]ax-[Aa]ge=(\d+)", hdr3).group(1)), 14400)
session.MAX_AGE = saved

print("\nThe round trip: cookie in hand, the HUD loads:")
client = TestClient(run.app)
sid = session.create("owner@example.com", "test-browser")
r = client.get("/", cookies={run.COOKIE: sid})
check("GET / with a session -> 200", r.status_code, 200)
check("served the HUD, not the login page", "<title" in r.text.lower(), True)

print("\nWithout one, a browser gets the sign-in PAGE (not raw JSON):")
r = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
check("status 401", r.status_code, 401)
check("html body", "<html" in r.text.lower() or "<!doctype" in r.text.lower(), True)
check("has the Google button", "auth/login" in r.text, True)

print("\n/auth/login redirects to Google and stamps the state cookie:")
r = client.get("/auth/login", follow_redirects=False)
check("302/307", r.status_code in (302, 303, 307), True)
loc = r.headers.get("location", "")
check("points at Google", "accounts.google.com" in loc, True)
check("prompt=consent every time", "prompt=consent" in loc, True)
check("PKCE challenge present", "code_challenge=" in loc, True)
check("nonce present", "nonce=" in loc, True)
check("state cookie set", run.OAUTH_COOKIE in r.headers.get("set-cookie", ""), True)

print("\nThe callback still refuses a forged state:")
r = client.get("/oauth/callback?code=abc&state=wrong", follow_redirects=False)
check("not signed in", r.status_code in (302, 303, 307, 400, 401), True)
check("no session cookie issued",
      run.COOKIE in r.headers.get("set-cookie", ""), False)

session.revoke_all()
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
