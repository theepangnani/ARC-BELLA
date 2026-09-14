# -*- coding: utf-8 -*-
"""Disconnect means disconnected: unlinking revokes the token at the service.

links.unlink used to delete only the local file, so the access and refresh
tokens stayed valid at Dropbox, Spotify and the rest — anyone holding a copy
kept the link. Asked for by Claude 2; written by Claude 4. What this guards:

  · a service with a documented revoke is told, with a live access token
    (an expired one refreshed first, since Dropbox's revoke authenticates with
    the token it revokes, and revoking it takes the refresh token too);
  · the file is removed ALWAYS — a revoke that raises, times out or is refused
    never leaves the link in place;
  · a service with no revoke endpoint makes no network call at all, and none
    was guessed: Spotify, Microsoft and GitHub have no revoke a client-id-only
    app can call (see links._revoke for why, per provider);
  · no token text reaches the log, even when the exception carries it.
"""
import contextlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ["DROPBOX_CLIENT_ID"] = "dbx-client"
os.environ["SPOTIFY_CLIENT_ID"] = "sp-client"
os.environ["GITHUB_CLIENT_ID"] = "gh-client"

import httpx   # noqa: E402
import links   # noqa: E402
import whose   # noqa: E402

c = Check()
OWNER = "owner@example.com"
ACCESS, REFRESH = "acc-SECRET-111", "ref-SECRET-222"
whose.use(OWNER)


def resp(status, body=None, url="https://x.example/"):
    return httpx.Response(status, json=body if body is not None else {},
                          request=httpx.Request("POST", url))


def link(sid, expired=False):
    links._store_token(sid, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600})
    if expired:
        p = links._path(sid)
        data = json.loads(p.read_text(encoding="utf-8"))
        data["expires_at"] = time.time() - 5
        p.write_text(json.dumps(data), encoding="utf-8")


def file_gone(sid):
    return not links._path(sid).exists()


def unlink_logged(sid, post):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        removed = links.unlink(sid, post=post)
    return removed, out.getvalue()


print("Which services revoke, and that none was guessed:")
c("  only Dropbox has a revoke endpoint",
  sorted(k for k, s in links.SERVICES.items() if s.get("revoke_url")), ["dropbox"])
c("  and it is the documented one", links.SERVICES["dropbox"]["revoke_url"],
  "https://api.dropboxapi.com/2/auth/token/revoke")
c.truthy("  every revoke URL is https", all(s["revoke_url"].startswith("https://")
                                        for s in links.SERVICES.values() if s.get("revoke_url")))
src = io.open(ARC / "links.py", encoding="utf-8").read()
c.truthy("  the reason each other service has none is written down",
         all(w in src for w in ("spotify.com/account/apps", "revokeSignInSessions", "client secret")))

print("\nA live Dropbox link is revoked, then removed:")
calls = []


def ok_post(u, data=None, headers=None, timeout=None):
    calls.append({"url": u, "data": dict(data or {}), "headers": dict(headers or {}), "timeout": timeout})
    return resp(200 if "revoke" in u else 400, None if "revoke" in u else {"error": "unexpected"}, u)


link("dropbox")
removed, log = unlink_logged("dropbox", ok_post)
c("  removed", removed, True)
c("  the file is gone", file_gone("dropbox"), True)
c("  one call, to the revoke endpoint", [x["url"] for x in calls], [links.SERVICES["dropbox"]["revoke_url"]])
c("  authenticated with the token being revoked", calls[0]["headers"].get("Authorization"), "Bearer " + ACCESS)
c.truthy("  with a short timeout", calls[0]["timeout"] and calls[0]["timeout"] <= 5)
c.truthy("  the log says it was revoked", "dropbox" in log and "revoked" in log)
c.truthy("  and holds no token", ACCESS not in log and REFRESH not in log)

print("\nAn expired Dropbox link is refreshed first, so the refresh token is spent on the revoke:")
calls.clear()


def refresh_then_revoke(u, data=None, headers=None, timeout=None):
    calls.append({"url": u, "data": dict(data or {}), "headers": dict(headers or {})})
    if "revoke" in u:
        return resp(200, None, u)
    return resp(200, {"access_token": "acc-FRESH-333", "expires_in": 14400}, u)


link("dropbox", expired=True)
removed, log = unlink_logged("dropbox", refresh_then_revoke)
c("  removed", removed, True)
c("  the refresh used the stored refresh token", calls[0]["data"].get("refresh_token"), REFRESH)
c("  then the fresh access token was revoked", calls[-1]["headers"].get("Authorization"), "Bearer acc-FRESH-333")
c("  the file is gone", file_gone("dropbox"), True)
c.truthy("  no token in the log", all(t not in log for t in (ACCESS, REFRESH, "acc-FRESH-333")))

print("\nA revoke that goes wrong still removes the link:")


def raising(u, data=None, headers=None, timeout=None):
    # The exception text carries the token, as a real request error could.
    raise httpx.ConnectError("boom Bearer %s %s" % (ACCESS, REFRESH))


def timing_out(u, data=None, headers=None, timeout=None):
    raise httpx.ReadTimeout("timed out")


def refused(u, data=None, headers=None, timeout=None):
    return resp(401, {"error_summary": "expired_access_token"}, u)


def refresh_fails(u, data=None, headers=None, timeout=None):
    return resp(400, {"error": "invalid_grant"}, u)


for label, post, expired in (("raising", raising, False), ("timing out", timing_out, False),
                             ("refused", refused, False),
                             ("a refresh that fails first", refresh_fails, True)):
    link("dropbox", expired=expired)
    try:
        removed, log = unlink_logged("dropbox", post)
        c("  %-28s removed" % label, (removed, file_gone("dropbox")), (True, True))
        c.truthy("  %-28s logged, without a token" % label,
                 "dropbox" in log and "revoked" not in log and ACCESS not in log and REFRESH not in log)
    except Exception as e:
        c.truthy("  %-28s did not raise out of unlink (%s)" % (label, type(e).__name__), False)
c("  a revoke that fails is still not a link", links.linked("dropbox"), False)

print("\nServices without a revoke make no network call:")


def no_network(u, data=None, headers=None, timeout=None):
    raise AssertionError("network called for a service with no revoke endpoint")


real_post = httpx.post
httpx.post = no_network
try:
    for sid in ("spotify", "github", "microsoft"):
        if sid == "microsoft":
            os.environ["MS_CLIENT_ID"] = "ms-client"
        link(sid)
        removed, log = unlink_logged(sid, None)
        c("  %-10s removed, no call" % sid, (removed, file_gone(sid)), (True, True))
        c.truthy("  %-10s log holds no token" % sid, ACCESS not in log and REFRESH not in log)
    c("  a token-flow service is untouched (nothing to remove)", links.unlink("notion"), False)
    c("  unlinking what was never linked calls nothing", links.unlink("dropbox"), False)
finally:
    httpx.post = real_post

print("\nOne person's disconnect is their own:")
link("dropbox")
whose.use("guest@example.com")
calls.clear()
c("  another account's unlink removes nothing", links.unlink("dropbox", post=ok_post), False)
c("  and revokes nothing", calls, [])
whose.use(OWNER)
c("  the owner is still linked", links._path("dropbox").exists(), True)

# Where Disconnect cannot reach the service, it says where to finish the job,
# so "unlinked" never reads as "Bella's access is gone" when it is not.
print("\nEvery signed-in service either revokes or says where to:")
for sid, s in links.SERVICES.items():
    if s["flow"] == "token":
        continue
    c("  %-10s exactly one of revoke_url / unlink_note" % sid,
      bool(s.get("revoke_url")) != bool(s.get("unlink_note")), True)
c.truthy("  the page is sent the note",
         all("unlink_note" in e for e in links.catalogue()))
_page = io.open(os.path.join(str(ARC), "static", "index.html"), encoding="utf-8").read()
_run = io.open(os.path.join(str(ARC), "run.py"), encoding="utf-8").read()
c.truthy("  (and no request URL, which can carry a key, is ever logged by httpx)",
         'for _noisy in ("httpx", "httpcore"):' in _run
         and "logging.getLogger(_noisy).setLevel(logging.WARNING)" in _run)
c.truthy("  and shows it after an Unlink",
         'if (s.unlink_note) addEntry("sys", "SYSTEM", s.name + " unlinked. " + s.unlink_note);' in _page)

c.done()
