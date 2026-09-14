# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Sign-ins started and never finished cannot fill the server's memory.

links._pending holds every sign-in between its start and its finish. It was
swept only when a redirect sign-in started, so device sign-ins could pile up
without end, and nothing capped how many one person could start (Claude 4's
security review). What this guards:

  · every start, finish and poll sweeps what has expired;
  · a device sign-in lives as long as its code does, not the redirect's ten
    minutes — sweeping it early ended sign-ins that were still being typed in;
  · one person holds at most PENDING_PER_PERSON, and a new one pushes out
    THEIR oldest, never somebody else's;
  · the whole store holds at most PENDING_MAX;
  · and the Unlink route runs off the event loop, since unlinking can now wait
    on the service (links.unlink revokes first).
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ["SPOTIFY_CLIENT_ID"] = "sp-client"
os.environ["GITHUB_CLIENT_ID"] = "gh-client"

import httpx   # noqa: E402
import links   # noqa: E402
import whose   # noqa: E402

c = Check()
REDIRECT = "https://arc.example/oauth/link/spotify/callback"


def device_answer(u, data=None, headers=None, timeout=None):
    return httpx.Response(200, json={"device_code": "D", "user_code": "U", "verification_uri": "v",
                                     "interval": 0, "expires_in": 900},
                          request=httpx.Request("POST", u))


def mine(who):
    return [k for k, v in links._pending.items() if v["who"] == who]


print("The caps:")
c("  a few per person", links.PENDING_PER_PERSON, 5)
c("  and a bound on the whole store", links.PENDING_MAX, 256)

print("\nOne person starting sign-ins over and over:")
links._pending.clear()
whose.use("owner@example.com")
states = []
for i in range(40):
    states.append(links.start_redirect("spotify", REDIRECT, bind="B"))
    links.start_device("github", post=device_answer, bind="B")
c("  holds only the cap", len(mine("owner@example.com")), links.PENDING_PER_PERSON)
c("  and the store holds nothing else", len(links._pending), links.PENDING_PER_PERSON)
newest = httpx.URL(states[-1]).params["state"]
c.truthy("  the newest sign-in is the one kept", newest in links._pending)
oldest = httpx.URL(states[0]).params["state"]
c("  the oldest was pushed out, and cannot finish",
  "expired" in links.finish_redirect("spotify", oldest, "code", bind="B"), True)

print("\nSomebody else's sign-ins are not theirs to push out:")
links._pending.clear()
whose.use("guest@example.com")
g_state = httpx.URL(links.start_redirect("spotify", REDIRECT, bind="G")).params["state"]
whose.use("owner@example.com")
for i in range(30):
    links.start_redirect("spotify", REDIRECT, bind="B")
c.truthy("  the other person's sign-in is still there", g_state in links._pending)

print("\nThe whole store is bounded:")
links._pending.clear()
for i in range(links.PENDING_MAX + 40):
    whose.use("person%d@example.com" % i)
    links.start_redirect("spotify", REDIRECT, bind="B")
c("  never more than PENDING_MAX", len(links._pending), links.PENDING_MAX)
c.truthy("  and it is the oldest that went", mine("person0@example.com") == []
         and mine("person%d@example.com" % (links.PENDING_MAX + 39)) != [])

print("\nExpired sign-ins are swept by a start, a finish and a poll:")
for label, act in (
        ("a redirect start", lambda: links.start_redirect("spotify", REDIRECT, bind="B")),
        ("a device start", lambda: links.start_device("github", post=device_answer, bind="B")),
        ("a finish", lambda: links.finish_redirect("spotify", "no-such-state", "code", bind="B")),
        ("a poll", lambda: links.poll_device("github", "no-such-handle", bind="B"))):
    links._pending.clear()
    whose.use("someone@example.com")
    links._pending["stale-r"] = {"service": "spotify", "who": "x@example.com", "bind": "", "verifier": "v",
                                 "redirect": REDIRECT, "at": time.time() - links.PENDING_SECONDS - 5}
    links._pending["stale-d"] = {"service": "github", "who": "x@example.com", "bind": "", "device_code": "D",
                                 "at": time.time() - 2000, "expires": time.time() - 5,
                                 "interval": 0, "last": 0.0}
    act()
    c("  %-17s sweeps both" % label, {"stale-r", "stale-d"} & set(links._pending), set())

print("\nA device sign-in lasts as long as its code:")
links._pending.clear()
whose.use("owner@example.com")
d = links.start_device("github", post=device_answer, bind="B")
links._pending[d["handle"]]["at"] = time.time() - 660     # 11 minutes in, of a 15-minute code
links._pending[d["handle"]]["expires"] = time.time() + 240
links.start_redirect("spotify", REDIRECT, bind="B")
c.truthy("  still there after a redirect start at eleven minutes", d["handle"] in links._pending)
links._pending[d["handle"]]["expires"] = time.time() - 1
c.truthy("  and a poll of its own expired code says so",
         "expired" in links.poll_device("github", d["handle"], bind="B"))
c("  and it is gone", d["handle"] in links._pending, False)

print("\nThe Unlink route does not wait on the service on the event loop:")
src = io.open(ARC / "run.py", encoding="utf-8").read()
route = src.split('@app.post("/api/links/{sid}/unlink")')[1].split("\n@app.")[0]
c.truthy("  it unlinks through to_thread", "to_thread(links.unlink" in route)
c("  and never calls it directly", route.replace("to_thread(links.unlink", "").count("links.unlink("), 0)

links._pending.clear()
c.done()
