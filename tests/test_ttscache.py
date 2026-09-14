# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The voice cache: the same short sentence is rendered once, and only once.

Bella says "Okay." and "Done." all day, and every one was a fresh ~0.7 s trip
to Edge for audio identical to the last. /api/tts now keeps finished clips in
memory. What this holds in place is less the speed than the limits, because
the cache is made of what Bella said:

  · a hit skips the render and is served exactly as a miss would be
  · only short text is kept; a long, personal sentence is never cached
  · bounded by count and by bytes, the least recently used going first
  · a failed or empty render is never remembered
  · the RESOLVED voice, the rate and the pitch are all part of the key
  · one person's cache does not answer for another (a timing oracle otherwise)
  · memory only, never a file
"""
import asyncio
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

import edge_tts    # noqa: E402
from starlette.testclient import TestClient   # noqa: E402
import run         # noqa: E402
import session     # noqa: E402
import voices      # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()

# Every render goes through a fake Communicate, so nothing here reaches
# Microsoft and every render is counted.
renders = []
behaviour = {"mode": "ok", "size": 1000}


class FakeCommunicate:
    def __init__(self, text, voice, rate="+0%", pitch="+0Hz", **kw):
        renders.append((text, voice, rate, pitch))

    async def stream(self):
        if behaviour["mode"] == "fail":
            raise ConnectionError("Session is closed")
        if behaviour["mode"] == "empty":
            return
        yield {"type": "audio", "data": b"m" * behaviour["size"]}


def reset():
    run._tts_cache.clear()
    run._tts_cache_bytes = 0
    for k in run.TTS_CACHE_STATS:
        run.TTS_CACHE_STATS[k] = 0
    renders.clear()
    behaviour.update(mode="ok", size=1000)


real_comm = edge_tts.Communicate
edge_tts.Communicate = FakeCommunicate
real_prefer = run.PREFER_ELEVEN
run.PREFER_ELEVEN = False
try:
    with TestClient(run.app) as client:
        O = {run.COOKIE: session.create(OWNER, "browser")}
        G = {run.COOKIE: session.create(GUEST, "browser")}

        def say(text, cookies=O, **extra):
            return client.post("/api/tts", cookies=cookies, json=dict(text=text, **extra))

        # ------------------------------------------------------------ a hit
        print("The same short sentence is rendered once:")
        reset()
        first = say("Okay.")
        second = say("Okay.")
        c("  the first is rendered", first.status_code, 200)
        c("  the second is not", len(renders), 1)
        c("  ...and is counted as a hit", run.TTS_CACHE_STATS["hits"], 1)
        c("  the first was a miss", run.TTS_CACHE_STATS["misses"], 1)
        c("  the hit is the same audio", second.content, first.content)
        c("  served the same way", second.headers.get("content-type"),
          first.headers.get("content-type"))
        c("  ...as audio", (second.headers.get("content-type") or "").split(";")[0], "audio/mpeg")
        c("  surrounding space does not make a new clip",
          (say("  Okay.  ").status_code, len(renders)), (200, 1))

        # ------------------------------------------------------- long text
        print("\nA long sentence is never kept:")
        reset()
        edge = "x" * run.TTS_CACHE_MAX_CHARS
        c("  the limit is 80 characters", run.TTS_CACHE_MAX_CHARS, 80)
        say(edge)
        say(edge)
        c("  80 characters is cached", len(renders), 1)
        reset()
        longer = "Your appointment with Dr Patel is on Thursday at three, bring the forms."
        longer = longer + "!" * (81 - len(longer))
        c("  (the sentence really is 81 characters)", len(longer), 81)
        say(longer)
        say(longer)
        c("  81 is rendered every time", len(renders), 2)
        c("  and nothing was stored", len(run._tts_cache), 0)
        c("  nor counted as a miss: it was never a candidate",
          run.TTS_CACHE_STATS["misses"], 0)

        # --------------------------------------------------- failures
        print("\nA failed or empty render is not remembered:")
        reset()
        behaviour["mode"] = "fail"
        c("  a failure is a 502", say("Done.").status_code, 502)
        c("  and stores nothing", len(run._tts_cache), 0)
        behaviour["mode"] = "ok"
        c("  the next try renders for real", (say("Done.").status_code, len(renders)), (200, 2))
        reset()
        behaviour["mode"] = "empty"
        c("  an empty clip is a 502", say("Done.").status_code, 502)
        c("  and stores nothing", len(run._tts_cache), 0)
        behaviour["mode"] = "ok"
        say("Done.")
        c("  so the next try renders again", len(renders), 2)

        # --------------------------------------------------- the key
        print("\nThe voice as rendered, the rate and the pitch are all in the key:")
        reset()
        butler = voices.persona("butler")
        say("Good evening.", voice="persona:butler")
        say("Good evening.", voice=butler["voice"])
        c("  the butler's voice at its own rate is not the plain voice",
          len(renders), 2)
        c.truthy("  (they really do differ only in rate or pitch)",
                 renders[0][1] == renders[1][1] and renders[0][2:] != renders[1][2:])
        say("Good evening.", voice="persona:butler")
        say("Good evening.", voice=butler["voice"])
        c("  and each is then a hit of its own", len(renders), 2)
        say("Good evening.", voice="persona:nobody")
        say("Good evening.", voice="")
        c("  an unknown character and no voice resolve the same, so share a clip",
          len(renders), 3)
        # Rate and pitch one at a time, on the key itself: no persona differs
        # from another in exactly one of them.

        class _Url:
            path = "/api/tts"

        class _Req:
            cookies = {}
            headers = {}
            url = _Url()
        base = run._tts_key(_Req(), "Hi.", "edge", "en-GB-RyanNeural", "+0%", "+0Hz")
        c.truthy("  a different voice is a different key",
                 base != run._tts_key(_Req(), "Hi.", "edge", "en-GB-SoniaNeural", "+0%", "+0Hz"))
        c.truthy("  a different rate is a different key",
                 base != run._tts_key(_Req(), "Hi.", "edge", "en-GB-RyanNeural", "+10%", "+0Hz"))
        c.truthy("  a different pitch is a different key",
                 base != run._tts_key(_Req(), "Hi.", "edge", "en-GB-RyanNeural", "+0%", "-5Hz"))
        c.truthy("  and the same everything is the same key",
                 base == run._tts_key(_Req(), "Hi.", "edge", "en-GB-RyanNeural", "+0%", "+0Hz"))

        print("\nOne person's cache does not answer for another:")
        reset()
        say("Your dentist is at three.", cookies=O)
        say("Your dentist is at three.", cookies=G)
        c("  the guest's identical sentence is rendered for them", len(renders), 2)
        say("Your dentist is at three.", cookies=G)
        c("  ...and then cached for them", len(renders), 2)

        # ---------------------------------------------------- ElevenLabs
        print("\nElevenLabs clips are kept apart from Edge's:")
        reset()
        eleven_calls = []

        async def fake_eleven(http, text, lang=""):
            eleven_calls.append(text)
            return b"e" * 500

        real_eleven = run._eleven_tts
        run._eleven_tts = fake_eleven
        run.PREFER_ELEVEN = True
        try:
            first = say("Morning.")
            say("Morning.")
            c("  rendered once", len(eleven_calls), 1)
            c("  and it was ElevenLabs, not Edge", (len(renders), first.content), (0, b"e" * 500))
            run.PREFER_ELEVEN = False
            c("  an Edge request for the same words is not answered with it",
              say("Morning.").content, b"m" * 1000)
        finally:
            run._eleven_tts = real_eleven
            run.PREFER_ELEVEN = False

        session.revoke_all()

    # -------------------------------------------------------- eviction
    print("\nBounded by count, least recently used first:")
    reset()
    c("  at most 64 clips", run.TTS_CACHE_MAX_ITEMS, 64)
    c("  and 4 MB", run.TTS_CACHE_MAX_BYTES, 4 * 1024 * 1024)
    real_items = run.TTS_CACHE_MAX_ITEMS
    run.TTS_CACHE_MAX_ITEMS = 3
    try:
        for k in ("a", "b", "c"):
            run._tts_cache_put(("o", k), b"123")
        run._tts_cache_get(("o", "a"))          # a is now the freshest
        run._tts_cache_put(("o", "d"), b"123")
        c("  the fourth pushes one out", len(run._tts_cache), 3)
        c("  ...the least recently used, not the oldest written",
          sorted(k[1] for k in run._tts_cache), ["a", "c", "d"])
        c("  and the bytes are kept in step", run._tts_cache_bytes, 9)
    finally:
        run.TTS_CACHE_MAX_ITEMS = real_items

    print("\nBounded by bytes too:")
    reset()
    real_bytes = run.TTS_CACHE_MAX_BYTES
    run.TTS_CACHE_MAX_BYTES = 10
    try:
        run._tts_cache_put(("o", "a"), b"1234")
        run._tts_cache_put(("o", "b"), b"1234")
        run._tts_cache_put(("o", "c"), b"1234")
        c("  twelve bytes into ten drops the oldest", sorted(k[1] for k in run._tts_cache), ["b", "c"])
        c("  and stays under the budget", run._tts_cache_bytes, 8)
        run._tts_cache_put(("o", "huge"), b"x" * 11)
        c("  a clip bigger than the whole budget is not kept",
          (("o", "huge") in run._tts_cache, sorted(k[1] for k in run._tts_cache)),
          (False, ["b", "c"]))
        run._tts_cache_put(("o", "b"), b"12")
        c("  replacing a clip counts only its new size", run._tts_cache_bytes, 6)
        c("  nothing is stored for no key", run._tts_cache_put(None, b"12"), None)
        c("  ...or for no audio", (run._tts_cache_put(("o", "z"), b""), ("o", "z") in run._tts_cache),
          (None, False))
    finally:
        run.TTS_CACHE_MAX_BYTES = real_bytes
finally:
    edge_tts.Communicate = real_comm
    run.PREFER_ELEVEN = real_prefer
    reset()

# ------------------------------------------------------------ never on disk
print("\nIt lives in memory and nowhere else:")
src = io.open(ARC / "run.py", encoding="utf-8").read()
section = src[src.index("# --- the voice cache"):src.index('@app.get("/api/voices")')]
c("  nothing in it opens a file", re.search(r"\bopen\(|write_text|write_bytes|storefile|json\.dump", section)
  is not None, False)
c.truthy("  and the reason is written down", "NEVER on disk" in section)
c.truthy("  the timing oracle is why it is per person", "timing" in section and "oracle" in section)
c.truthy("  a new connection every render, and why", "Session is closed" in src)
c("  the sentence is still never logged",
  re.search(r"print\([^\n]*\{text", section) is not None, False)

c.done()
