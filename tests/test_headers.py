# -*- coding: utf-8 -*-
"""Security headers, and the policy staying honest about what the page loads.

A Content-Security-Policy is the one security control that fails towards
breaking the product: too loose and it buys nothing, too tight and a feature
stops working with an error only the browser console ever sees. So the check
that matters most here is not "is there a CSP" — it is that every external
origin the page actually reaches is allowed by it. Add an API to index.html
and forget run.py, and this fails rather than the feature dying quietly on
somebody's phone.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
page = io.open(HUD, encoding="utf-8").read()


def directive(name, policy=None):
    for part in (policy or run.CSP).split(";"):
        part = part.strip()
        if part.split(" ")[0] == name:
            return part
    return ""


print("Every external origin in the HUD is allowed by the CSP:")
origins = sorted({m.group(0) for m in
                  re.finditer(r"https://[a-zA-Z0-9.-]+", page)})
c.truthy("  the page does reach outside at all", origins)
for o in origins:
    c.truthy("  %-32s allowed" % o, o in run.CSP)
print("      (%s)" % ", ".join(origins))

print("\nThe policy says what it means to say:")
c.truthy("  a default that denies", "default-src 'self'" in run.CSP)
# Not an oversight: the HUD is one file of inline script with no build step,
# and without this the entire application is dead on arrival.
c.truthy("  inline script is allowed, because the app IS inline",
         "'unsafe-inline'" in directive("script-src"))
c("  ...but no script may be loaded from elsewhere",
  directive("script-src"), "script-src 'self' 'unsafe-inline'")
c("  nothing may be sent anywhere unexpected",
  directive("connect-src").startswith("connect-src 'self' https://"), True)
c("  there are no <img> tags to need a remote source", page.count("<img"), 0)
c("  so images stay local", directive("img-src"), "img-src 'self' data: blob:")
c.truthy("  the page cannot be framed", "frame-ancestors 'none'" in run.CSP)
c.truthy("  <base> cannot be rewritten", "base-uri 'none'" in run.CSP)
c.truthy("  no plugins", "object-src 'none'" in run.CSP)

print("\nPermissions-Policy restricts only what ARC does not use:")
for feature in ["microphone", "camera", "geolocation", "autoplay", "display-capture"]:
    c("  %-16s left alone (listing it would disable it)" % feature,
      feature in run.PERMISSIONS_POLICY, False)
for feature in ["payment", "usb", "serial", "bluetooth"]:
    c.truthy("  %-16s denied" % feature, "%s=()" % feature in run.PERMISSIONS_POLICY)

with TestClient(run.app) as client:
    print("\nThey are on every response, signed in or not:")
    sid = session.create("owner@example.com", "test")
    C = {run.COOKIE: sid}
    cases = [("the sign-in page (401)", client.get("/", headers={"accept": "text/html"})),
             ("the public homepage", client.get("/home")),
             ("an API call with a session", client.get("/api/health", cookies=C)),
             ("a static asset", client.get("/static/arc-logo.svg")),
             ("the service worker", client.get("/sw.js"))]
    for label, r in cases:
        h = r.headers
        c.truthy("  %-26s CSP" % label, h.get("content-security-policy"))
        c.truthy("  %-26s nosniff" % label,
                 h.get("x-content-type-options") == "nosniff")
        c.truthy("  %-26s no framing" % label, h.get("x-frame-options") == "DENY")
        c.truthy("  %-26s referrer" % label, h.get("referrer-policy"))

    print("\nHSTS follows the scheme, rather than being sent regardless:")
    r = client.get("/api/health", cookies=C)
    c("  plain http -> not sent", r.headers.get("strict-transport-security"), None)
    r = client.get("/api/health", cookies=C, headers={"x-forwarded-proto": "https"})
    c.truthy("  behind an https tunnel -> sent",
             r.headers.get("strict-transport-security"))
    c("  ...for a year", "max-age=31536000" in
      (r.headers.get("strict-transport-security") or ""), True)
    # includeSubDomains on a tailnet hostname reaches further than this app.
    c("  and not for anything it doesn't serve",
      "includeSubDomains" in (r.headers.get("strict-transport-security") or ""), False)

    print("\nAnd nothing else was disturbed:")
    c("  the HUD still answers", client.get("/", cookies=C).status_code, 200)
    c("  the no-cache header survives",
      "no-store" in (client.get("/", cookies=C).headers.get("cache-control") or ""), True)
    session.revoke_all()

print("\nThere is a way out if a policy ever breaks the page:")
c.truthy("  ARC_SECURITY_HEADERS exists", "ARC_SECURITY_HEADERS" in
         io.open(ARC / "run.py", encoding="utf-8").read())
c.truthy("  on by default", run.SECURITY_HEADERS)
c.truthy("  and documented", "ARC_SECURITY_HEADERS" in
         io.open(ARC / "README.md", encoding="utf-8").read())

c.done()
