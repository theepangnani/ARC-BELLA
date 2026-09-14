# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The voice has limits: nobody can make this machine, or the owner's bill, speak all day.

/api/tts had no rate or concurrency limit, and with ARC_PREFER_ELEVEN on every
render a guest asked for was billed to the owner. What this holds:

  · every account has at most TTS_PER_ACCOUNT renders in flight, and the slot
    is given back when a render fails, too
  · a guest may send TTS_GUEST_MAX_CHARS per request and TTS_GUEST_CHARS_PER_MIN
    a minute; the owner has no per-minute budget
  · a guest never reaches ElevenLabs, whatever the setting; the owner still does
  · a failed render tells the page the error's type, never its text
  · none of it is felt by the page: it speaks in chunks of ~220 characters,
    at most three at a time, and falls back to the browser voice on a refusal
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

import edge_tts    # noqa: E402
from starlette.testclient import TestClient   # noqa: E402
import run         # noqa: E402
import session     # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()
renders, eleven = [], []
behaviour = {"mode": "ok"}


class FakeCommunicate:
    def __init__(self, text, voice, rate="+0%", pitch="+0Hz", **kw):
        renders.append(text)

    async def stream(self):
        if behaviour["mode"] == "fail":
            raise ConnectionError("upstream said: secret-ish detail 12345")
        yield {"type": "audio", "data": b"m" * 100}


async def fake_eleven(http, text, lang):
    eleven.append(text)
    return b"e" * 100


def reset():
    run._tts_cache.clear()
    run._tts_cache_bytes = 0
    run._tts_inflight.clear()
    run._tts_guest_chars.clear()
    renders.clear()
    eleven.clear()
    behaviour["mode"] = "ok"


def text_of(n, tag):
    """n characters, different per tag, and over the cache's 80 so every one renders."""
    return (tag + " " + "a" * n)[:n]


print("The numbers, pinned so moving one is a decision:")
c("  renders at once, anybody's", run.TTS_RENDERS, 8)
c("  in flight per account", run.TTS_PER_ACCOUNT, 3)
c("  a guest's longest request", run.TTS_GUEST_MAX_CHARS, 1000)
c("  a guest's characters a minute", run.TTS_GUEST_CHARS_PER_MIN, 4000)
c.truthy("  ...all above what the page sends (chunks of 220, three at a time)",
         run.TTS_GUEST_MAX_CHARS > 220 and run.TTS_PER_ACCOUNT >= 3
         and run.TTS_GUEST_CHARS_PER_MIN >= 3 * 220 * 4)

print("\nThe per-minute budget on its own:")
reset()
t0 = 1000.0
c("  3000 characters fit", run._tts_guest_budget(GUEST, 3000, now=t0), True)
c("  1000 more fit, exactly at the budget", run._tts_guest_budget(GUEST, 1000, now=t0 + 1), True)
c("  one more does not", run._tts_guest_budget(GUEST, 1, now=t0 + 2), False)
c("  a minute later the first 3000 are back", run._tts_guest_budget(GUEST, 3000, now=t0 + 60), True)
c("  another guest has their own budget", run._tts_guest_budget("other@example.com", 4000, now=t0), True)

real_comm, real_eleven, real_prefer = edge_tts.Communicate, run._eleven_tts, run.PREFER_ELEVEN
edge_tts.Communicate = FakeCommunicate
run._eleven_tts = fake_eleven
run.PREFER_ELEVEN = False
try:
    with TestClient(run.app) as client:
        O = {run.COOKIE: session.create(OWNER, "browser")}
        G = {run.COOKIE: session.create(GUEST, "browser")}

        def say(text, cookies):
            return client.post("/api/tts", cookies=cookies, json={"text": text})

        print("\nA guest's requests:")
        reset()
        c("  1000 characters are spoken", say(text_of(1000, "g1"), G).status_code, 200)
        c("  1001 are refused as too long", say(text_of(1001, "g2"), G).status_code, 400)
        c("  ...and a refused request spends no budget", sum(n for _, n in run._tts_guest_chars[GUEST]), 1000)
        for i in range(3):
            say(text_of(1000, "g%d" % (i + 3)), G)
        r = say(text_of(200, "g9"), G)
        c("  past 4000 in a minute: slow down", (r.status_code, r.json().get("detail")),
          (429, "Slow down a moment."))
        c("  ...and nothing was rendered for it", len(renders), 4)

        print("\nThe owner:")
        reset()
        codes = {say(text_of(1000, "o%d" % i), O).status_code for i in range(8)}
        c("  8000 characters in a minute are all spoken", codes, {200})
        c("  a 5000-character request is still allowed", say(text_of(5000, "big"), O).status_code, 200)

        print("\nRenders in flight, per account:")
        reset()
        run._tts_inflight[OWNER] = run.TTS_PER_ACCOUNT
        c("  a fourth at once is refused", say(text_of(100, "busy"), O).status_code, 429)
        c("  ...while the guest's own count is separate", say(text_of(100, "free"), G).status_code, 200)
        run._tts_inflight.clear()
        behaviour["mode"] = "fail"
        r = say(text_of(100, "boom"), O)
        c("  a failed render is a 502", r.status_code, 502)
        c("  ...that names the error's type, not its text", r.json().get("detail"),
          "Text-to-speech failed (ConnectionError).")
        c.truthy("  ...and nothing of the upstream message reaches the page", "12345" not in r.text)
        c("  ...and gives its slot back", dict(run._tts_inflight), {})

        print("\nElevenLabs is the owner's money:")
        reset()
        run.PREFER_ELEVEN = True
        say(text_of(100, "guest-eleven"), G)
        c("  a guest is rendered by Edge", (len(eleven), len(renders)), (0, 1))
        reset()
        say(text_of(100, "owner-eleven"), O)
        c("  the owner still gets ElevenLabs", (len(eleven), len(renders)), (1, 0))
        run.PREFER_ELEVEN = False
        session.revoke_all()
finally:
    edge_tts.Communicate, run._eleven_tts, run.PREFER_ELEVEN = real_comm, real_eleven, real_prefer

c.done()
