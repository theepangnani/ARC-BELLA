# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Requests must come from ARC's own page, under ARC's own name.

run.RequestGate sits outside everything else. It refuses a Host that is not
one of ours, a path that walks with ".." or a backslash, a state-changing
request whose Origin is some other page's, a body that is not JSON, and a body
larger than the route could need, counted on the bytes as they arrive. The
polls that hand something over once are POST. is_local_request, which puts
the shell within reach, now also wants a loopback Host and no proxy header of
any kind.

The suites elsewhere talk to the app as "testserver", a name the gate allows
only inside the test sandbox. This one talks to it as the browser does.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ["ARC_ALLOWED_HOSTS"] = "arc.example.ts.net"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from starlette.requests import Request       # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
ME = "http://localhost:8420"
local = TestClient(run.app, base_url=ME)
OWNER = sorted(run.OWNER_EMAILS)[0]


def cookie():
    return {run.COOKIE: session.create(OWNER, "browser")}


print("The gate is the outermost layer:")
c("  added last, so it runs first", run.app.user_middleware[0].cls is run.RequestGate, True)
c.truthy("  and it does not care which sign-in mode is on",
         "AUTH_MODE" not in inspect.getsource(run.RequestGate))

print("\nOnly our own names are answered:")
c("  localhost at the listening port", local.get("/api/health").status_code, 401)
c("  127.0.0.1 at that port",
  local.get("/api/health", headers={"host": "127.0.0.1:8420"}).status_code, 401)
c("  [::1] at that port", local.get("/api/health", headers={"host": "[::1]:8420"}).status_code, 401)
c("  a name made to resolve here is refused",
  local.get("/api/health", headers={"host": "evil.example:8420"}).status_code, 421)
c("  so is localhost at another port",
  local.get("/api/health", headers={"host": "localhost:8421"}).status_code, 421)
c("  and no Host at all", local.get("/api/health", headers={"host": ""}).status_code, 421)
c("  the refusal happens before sign-in, even for the page",
  local.get("/", headers={"host": "evil.example:8420", "accept": "text/html"}).status_code, 421)
c("  a name in ARC_ALLOWED_HOSTS is answered",
  local.get("/api/health", headers={"host": "arc.example.ts.net"}).status_code, 401)
c("  loopback without a port only when listening on 80",
  sorted(run.loopback_hosts(80)) == sorted(["localhost", "127.0.0.1", "[::1]",
                                            "localhost:80", "127.0.0.1:80", "[::1]:80"])
  and "localhost" not in run.loopback_hosts(8420), True)
c.truthy("  ARC_PUBLIC_URL's host is allowed too",
         "ALLOWED_HOSTS.add(urllib.parse.urlsplit(PUBLIC_URL).netloc.lower())" in
         inspect.getsource(sys.modules["run"]))

print("\nThe funnel's own Tailscale name is found at startup:")


class Ran:
    def __init__(self, stdout="", returncode=0):
        self.stdout, self.returncode = stdout, returncode


def fake(stdout="", returncode=0, boom=None):
    def run_(cmd, **kw):
        fake.cmd = cmd
        if boom:
            raise boom
        return Ran(stdout, returncode)
    return run_


real_which = run.shutil.which
run.shutil.which = lambda name: "tailscale"
FAKE = "desk.tail0000.ts.net"
c("  Self.DNSName, trailing dot gone",
  run.tailscale_name(fake('{"Self": {"DNSName": "Desk.tail0000.ts.net."}}')), FAKE)
c("  asked with status --json", fake.cmd[1:], ["status", "--json"])
c("  a name that is not ts.net is not taken",
  run.tailscale_name(fake('{"Self": {"DNSName": "evil.example."}}')), "")
c("  nor one dressed up as it",
  run.tailscale_name(fake('{"Self": {"DNSName": "evil.example/.ts.net."}}')), "")
c("  a failing tailscale", run.tailscale_name(fake("{}", returncode=1)), "")
c("  a slow one", run.tailscale_name(fake(boom=run.subprocess.TimeoutExpired("tailscale", 5))), "")
c("  a missing one", run.tailscale_name(fake(boom=FileNotFoundError())), "")
c("  an answer that is not JSON", run.tailscale_name(fake("signed out")), "")
c("  no Self in it", run.tailscale_name(fake('{"Self": null}')), "")
run.shutil.which = lambda name: None
real_exe, run.TAILSCALE_EXE = run.TAILSCALE_EXE, os.path.join(run.ROOT, "no-such-tailscale.exe")
c("  not installed at all, and nothing is run", run.tailscale_name(fake(boom=AssertionError())), "")
run.TAILSCALE_EXE = real_exe
run.shutil.which = lambda name: "tailscale"
c("  refused before it is found",
  local.get("/api/health", headers={"host": FAKE}).status_code, 421)
c("  found and allowed", run.allow_tailscale_name(fake('{"Self": {"DNSName": "%s."}}' % FAKE)), FAKE)
c("  answered after", local.get("/api/health", headers={"host": FAKE}).status_code, 401)
c("  still not local", run.is_local_request(Request({
    "type": "http", "method": "GET", "path": "/", "headers": [(b"host", FAKE.encode())],
    "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8420), "query_string": b""})), False)
run.ALLOWED_HOSTS.discard(FAKE)
run.shutil.which = real_which

print("\nThe other ways README gives a phone, and a Tailscale that was late:")
c("  a phone on the same Wi-Fi, by address", run.host_allowed("192.168.1.20:8420", 8420), True)
c("  an IPv6 address too", run.host_allowed("[fe80::1]:8420", 8420), True)
c("  but only at the listening port", run.host_allowed("192.168.1.20:9999", 8420), False)
c("  and a name that merely looks like one is not", run.host_allowed("192.168.1.20.evil.example:8420", 8420), False)
c("  Cloudflare's quick tunnel, whatever it is called today",
  run.host_allowed("brave-otter-tuesday.trycloudflare.com", 8420), True)
c("  but not a lookalike", run.host_allowed("trycloudflare.com.evil.example", 8420), False)
c("  a Wi-Fi address is still not local", run.is_local_request(Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"host", b"192.168.1.20:8420")], "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8420), "query_string": b""})), False)
late = FAKE
real_name = run.tailscale_name
calls = []
run.tailscale_name = lambda *a, **k: (calls.append(1), late)[1]
run._ts_retry["at"] = -1e9
c("  a ts.net name missing at startup is asked for again, and let in",
  local.get("/api/health", headers={"host": late}).status_code, 401)
run.ALLOWED_HOSTS.discard(late)
c("  but not asked again within the minute",
  (local.get("/api/health", headers={"host": late}).status_code, len(calls)), (421, 1))
run._ts_retry["at"] = -1e9
c("  and only this machine's own name gets in",
  local.get("/api/health", headers={"host": "other.example.ts.net"}).status_code, 421)
run.tailscale_name = real_name
run.ALLOWED_HOSTS.discard(late)
for fn in ("main", "serve_cloud"):
    src = inspect.getsource(getattr(run, fn))
    c.truthy("  %s looks before it serves" % fn,
             0 < src.index("allow_tailscale_name()") < src.index("uvicorn.run("))
import re  # noqa: E402
for f in (run.ROOT / "run.py", run.ROOT / "tests" / "test_requestgate.py"):
    names = set(re.findall(r"[a-z0-9-]+\.tail[0-9a-f]+\.ts\.net", f.read_text(encoding="utf-8").lower()))
    c("  no real tailnet name written into %s" % f.name, names - {FAKE}, set())

print("\nThe test clients' made-up names exist only in the sandbox:")
c("  allowed here", run.SANDBOX_HOSTS, {"testserver", "test"})
flag = os.environ.pop("ARC_TEST_SANDBOX")
c("  not without the sandbox flag", run._sandbox_hosts(), set())
os.environ["ARC_TEST_SANDBOX"] = flag
real_dir = run.DATA_DIR
run.DATA_DIR = run.ROOT
c("  not with a real data directory, flag or no flag", run._sandbox_hosts(), set())
run.DATA_DIR = real_dir

print("\nA state-changing request must come from our own page:")
c("  same origin", local.post("/api/logout", headers={"origin": ME}).status_code, 200)
c("  Sec-Fetch-Site: same-origin with no Origin",
  local.post("/api/logout", headers={"sec-fetch-site": "same-origin"}).status_code, 200)
c("  neither is refused", local.post("/api/logout").status_code, 403)
c("  another site", local.post("/api/logout", headers={"origin": "http://evil.example"}).status_code, 403)
c("  another port on this machine",
  local.post("/api/logout", headers={"origin": "http://localhost:9999"}).status_code, 403)
c("  an opaque origin", local.post("/api/logout", headers={"origin": "null"}).status_code, 403)
c("  a cross-site fetch with no Origin",
  local.post("/api/logout", headers={"sec-fetch-site": "cross-site"}).status_code, 403)
c("  the refusal says why",
  local.post("/api/logout").json()["detail"], "Requests must come from ARC's own page.")
c("  the public name, from the public page",
  local.post("/api/logout", headers={"host": "arc.example.ts.net",
                                     "origin": "https://arc.example.ts.net"}).status_code, 200)
c("  the public name, from a localhost page",
  local.post("/api/logout", headers={"host": "arc.example.ts.net", "origin": ME}).status_code, 403)
c("  GET is not asked for an Origin", local.get("/api/health").status_code, 401)

print("\nA body must be JSON:")
c("  text/plain with a body is refused",
  local.post("/api/logout", headers={"origin": ME, "content-type": "text/plain"},
             content=b'{"a": 1}').status_code, 415)
c("  a form is refused",
  local.post("/api/logout", headers={"origin": ME},
             data={"a": "1"}).status_code, 415)
c("  JSON with a charset is fine",
  local.post("/api/logout", headers={"origin": ME, "content-type": "application/json; charset=utf-8"},
             content=b"{}").status_code, 200)
c("  an empty text/plain beacon is fine",
  local.post("/api/logout", headers={"origin": ME, "content-type": "text/plain"}).status_code, 200)
# The page's goodbye: navigator.sendBeacon("/api/leave", new Blob([], {type: "text/plain"})).
# A beacon is a POST, so the browser sends Origin with it.
c.truthy("  the page still says goodbye with an empty text/plain beacon",
         'navigator.sendBeacon("/api/leave", new Blob([], { type: "text/plain" }))'
         in HUD.read_text(encoding="utf-8"))
c("  and that beacon gets through the gate to /api/leave",
  local.post("/api/leave", cookies=cookie(), headers={
      "origin": ME, "content-type": "text/plain", "sec-fetch-site": "same-origin",
      "sec-fetch-mode": "no-cors"}).status_code, 200)
c("  another site's beacon does not",
  local.post("/api/leave", cookies=cookie(), headers={
      "origin": "http://evil.example", "content-type": "text/plain",
      "sec-fetch-site": "cross-site"}).status_code, 403)

print("\nBodies are bounded:")
c("  256 KB for an ordinary route", run.body_limit("/api/memory/forget"), 256 * 1024)
c("  12 MB for a chat turn", run.body_limit("/api/chat"), 12 * 1024 * 1024)
c("  and its stream", run.body_limit("/api/chat/stream"), 12 * 1024 * 1024)
c("  a name that only starts the same is ordinary", run.body_limit("/api/chatter"), 256 * 1024)
big = b'{"x": "' + b"a" * (300 * 1024) + b'"}'
c("  a Content-Length over the limit is refused before reading",
  local.post("/api/memory/forget", headers={"origin": ME, "content-type": "application/json"},
             cookies=cookie(), content=big).status_code, 413)


def chunks():
    for _ in range(30):
        yield b"a" * (16 * 1024)


r = local.post("/api/memory/forget", headers={"origin": ME, "content-type": "application/json"},
               cookies=cookie(), content=chunks())
c("  a chunked body with no length is counted as it arrives", r.status_code, 413)
c("  and answered as too large, not as bad JSON", r.json()["detail"], "That request is too large.")


async def through_gate(pieces):
    """The gate around a bare app that reads the whole body and answers 200,
    fed in pieces with no Content-Length, the way a chunked upload arrives."""
    got = {"read": 0}

    async def inner(scope, receive, send):
        while True:
            m = await receive()
            got["read"] += len(m.get("body") or b"")
            if not m.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = [{"type": "http.request", "body": p, "more_body": True} for p in pieces]
    queue.append({"type": "http.request", "body": b"", "more_body": False})
    sent = []

    async def receive():
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "method": "POST", "path": "/api/memory/forget",
             "server": ("localhost", 8420), "client": ("127.0.0.1", 1),
             "headers": [(b"host", b"localhost:8420"), (b"origin", ME.encode()),
                         (b"content-type", b"application/json"),
                         (b"transfer-encoding", b"chunked")]}
    await run.RequestGate(inner)(scope, receive, send)
    return sent[0]["status"], got["read"]


status, read = asyncio.run(through_gate([b"a" * (64 * 1024)] * 6))
c("  the gate itself, fed in pieces past the limit, answers 413", status, 413)
c.truthy("  and the app never saw more than the limit", read <= 256 * 1024)
c("  the same pieces under the limit pass", asyncio.run(through_gate([b"a" * 1024] * 6)), (200, 6 * 1024))
c("  under the limit is untouched",
  local.post("/api/memory/forget", headers={"origin": ME}, cookies=cookie(),
             json={"which": "nothing like this"}).status_code, 200)

print("\nThe stream counts the turn before taking the upload:")
src = inspect.getsource(run.chat_stream)
c.truthy("  check_rate comes before the body is read",
         0 < src.index("check_rate(request)") < src.index("await request.body()"))
c.truthy("  and chat() does not count the same turn twice",
         "if not getattr(request.state, \"rate_checked\", False):" in inspect.getsource(run.chat))

print("\nPaths that walk are refused, and public files are named exactly:")
c("  an encoded .. out of an icon name",
  local.get("/static/icon-x/%2e%2e/index.html").status_code, 400)
c("  a backslash", local.get("/static/icon-192.png%5c..%5cindex.html").status_code, 400)
c("  a real icon, signed out", local.get("/static/icon-192.png").status_code, 200)
c("  a made-up name that starts like one is not public",
  local.get("/static/icon-anything.png").status_code, 401)
c("  the app itself stays behind sign-in", local.get("/static/index.html").status_code, 401)
c("  every public name is a real file",
  [p for p in run.PUBLIC_STATIC if not (run.ROOT / p.lstrip("/")).is_file()], [])

print("\nThe polls that hand something over once are POST:")
C = cookie()
for path in ("/api/reminders/due", "/api/alerts/due", "/api/alarms/due", "/api/triggers/due"):
    c("  %-20s GET is gone" % path, local.get(path, cookies=C).status_code, 405)
    c("  %-20s POST answers" % path, local.post(path, cookies=C, headers={"origin": ME}).status_code, 200)
    c("  %-20s and the page asks with POST" % path,
      ('fetch("%s", { method: "POST" })' % path) in HUD.read_text(encoding="utf-8"), True)

print("\nOnly the desktop itself is local:")


def req(host, peer="127.0.0.1", port=8420, extra=()):
    headers = [(b"host", host.encode())] + [(k.encode(), v.encode()) for k, v in extra]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (peer, 50000), "server": ("127.0.0.1", port), "query_string": b""})


c("  loopback peer, loopback name, no proxy", run.is_local_request(req("localhost:8420")), True)
c("  ::1 as well", run.is_local_request(req("[::1]:8420", peer="::1")), True)
c("  another peer", run.is_local_request(req("localhost:8420", peer="192.168.1.20")), False)
c("  the public name, even from loopback", run.is_local_request(req("arc.example.ts.net")), False)
c("  a loopback name at another port", run.is_local_request(req("localhost:8421")), False)
for name in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded",
             "x-real-ip", "cf-connecting-ip", "true-client-ip", "tailscale-user-login",
             "tailscale-funnel-request"):
    c("  %-25s means remote" % name,
      run.is_local_request(req("localhost:8420", extra=[(name, "1")])), False)

session.revoke_all()
c.done()
