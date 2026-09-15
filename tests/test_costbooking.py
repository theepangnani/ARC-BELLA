# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Every model call that was paid for is on the meter, once.

Claude 4's cost audit, guarded here:

  · a turn that fails part-way (the API overloaded on round two, a rate limit)
    used to book nothing — the rounds before it were paid for and never
    reached DAILY_COST_CAP or Arc Watch. It books what it spent, then raises
    the same error it always did;
  · a turn books exactly once, whichever way it ends;
  · a turn that fails before any round comes back books nothing at all.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

import anthropic   # noqa: E402
import httpx       # noqa: E402
import run         # noqa: E402
import session     # noqa: E402

c = Check()
OWNER = "owner@example.com"


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name):
        self.name, self.input, self.id = name, {"location": "Toronto"}, "cb-" + name


class Usage:
    def __init__(self, i, o, cr=0, cw=0):
        self.input_tokens, self.output_tokens = i, o
        self.cache_read_input_tokens, self.cache_creation_input_tokens = cr, cw
        self.server_tool_use = None


class Resp:
    def __init__(self, content, usage, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, usage


def overloaded():
    r = httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
                       request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.APIStatusError("Overloaded", response=r, body=None)


def turn(script):
    """One owner turn whose model calls follow `script`: each item is a Resp to
    return or an exception to raise. Returns (status, records, day cost added)."""
    calls = list(script)
    records = []

    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                step = calls.pop(0)
                if isinstance(step, BaseException):
                    raise step
                return step

        async def close(self):
            pass

    real_record, real_dispatch = run.stats.record, run.dispatch_tool
    run.stats.record = lambda **kw: records.append(kw)
    run.dispatch_tool = lambda name, args, **kw: ("12 degrees and clear", False)
    run.app.state.claude = Fake()
    before = run._day["cost"]
    try:
        async def go():
            sid = session.create(OWNER, "desk")
            transport = httpx.ASGITransport(app=run.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
                return await cl.post("/api/chat", cookies={run.COOKIE: sid}, json={
                    "messages": [{"role": "user", "content": "weather in Toronto"}],
                    "allow_actions": True, "brain": "smart"})
        r = asyncio.run(go())
    finally:
        run.stats.record, run.dispatch_tool = real_record, real_dispatch
        session.revoke_all()
    return r.status_code, records, run._day["cost"] - before


print("A turn that ends normally:")
status, recs, added = turn([Resp([ToolBlk("weather")], Usage(1000, 50), stop="tool_use"),
                            Resp([Blk("It's twelve degrees.")], Usage(1200, 20))])
c("  answered", status, 200)
c("  booked once", len(recs), 1)
c.truthy("  with both rounds' tokens", recs and recs[0]["tok_in"] == 2200 and recs[0]["tok_out"] == 70)
c("  not marked as an error", bool(recs and recs[0].get("error")), False)
c("  and counted as a turn", recs[0].get("turn") if recs else None, True)
model = recs[0]["model"] if recs else ""
c.truthy("  and the day's meter moved by what it cost",
         abs(added - run.turn_cost(model, 2200, 70)) < 1e-9)

print("\nA turn that fails on its second round:")
status, recs, added = turn([Resp([ToolBlk("weather")], Usage(1000, 50, cr=4000), stop="tool_use"),
                            overloaded()])
c.truthy("  the error still reaches the page, not a reply", status >= 400)
c("  booked once", len(recs), 1)
c.truthy("  with the round that came back", recs and recs[0]["tok_in"] == 1000
         and recs[0]["tok_out"] == 50 and recs[0]["cache_read"] == 4000)
c("  marked as an error", bool(recs and recs[0].get("error")), True)
c("  ...and not counted as a turn answered", recs[0].get("turn") if recs else None, False)
c.truthy("  and it counts towards the daily cap",
         recs and abs(added - run.turn_cost(recs[0]["model"], 1000, 50, 4000)) < 1e-9 and added > 0)

print("\nA turn that fails before anything comes back:")
status, recs, added = turn([overloaded()])
c.truthy("  an error status", status >= 400)
c("  costs nothing", added, 0.0)
c("  but is still counted in Arc Watch's errors, as no turn and no spend",
  [(r.get("error"), r.get("turn"), r.get("cost", 0)) for r in recs], [(True, False, 0)])

print("\nA connection that drops, and a rate limit, book the same way:")
req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
limited = anthropic.RateLimitError("slow down", response=httpx.Response(429, request=req), body=None)
for label, exc in (("a dropped connection", anthropic.APIConnectionError(request=req)),
                   ("a rate limit", limited)):
    status, recs, added = turn([Resp([ToolBlk("weather")], Usage(500, 10), stop="tool_use"), exc])
    c("  %-21s books its first round, once" % label, (len(recs), recs[0]["tok_in"] if recs else 0), (1, 500))
# What a streamed round raises when the reply stops arriving: httpx's own error,
# not an anthropic one. The first round was still paid for.
status, recs, added = turn([Resp([ToolBlk("weather")], Usage(500, 10), stop="tool_use"),
                            httpx.ReadTimeout("stream stalled", request=req)])
c("  a stream that stalls      books its first round, once",
  (len(recs), recs[0]["tok_in"] if recs else 0, bool(recs and recs[0].get("error"))), (1, 500, True))

print("\nThe rolling note (/api/summarize) is on Arc Watch, not only on the cap:")
note_records = []


class NoteFake:
    class messages:
        @staticmethod
        async def create(**kw):
            return Resp([Blk("- planning the Lisbon trip")], Usage(3000, 120))

    async def close(self):
        pass


real_record = run.stats.record
run.stats.record = lambda **kw: note_records.append(kw)
run.app.state.claude = NoteFake()
before = run._day["cost"]
try:
    async def summarize():
        sid = session.create(OWNER, "desk")
        transport = httpx.ASGITransport(app=run.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
            return await cl.post("/api/summarize", cookies={run.COOKIE: sid}, json={
                "note": "", "messages": [{"role": "user", "content": "let's plan Lisbon"},
                                         {"role": "assistant", "content": "Happy to."}]})
    r = asyncio.run(summarize())
finally:
    run.stats.record = real_record
    session.revoke_all()
added = run._day["cost"] - before
c("  the note came back", r.status_code, 200)
c("  recorded once", len(note_records), 1)
c.truthy("  with its tokens and the same cost the cap was charged",
         note_records and note_records[0]["tok_in"] == 3000 and note_records[0]["tok_out"] == 120
         and abs(note_records[0]["cost"] - added) < 1e-12 and added > 0)
c("  and not as a turn", note_records[0].get("turn") if note_records else None, False)

print("\nPrices, per the pricing page (platform.claude.com/docs/en/about-claude/pricing, 2026-09-14):")
for mid, want in (("claude-opus-4-5-20251101", (5.0, 25.0)), ("claude-opus-4-1-20250805", (15.0, 75.0)),
                  ("claude-opus-4-20250514", (15.0, 75.0)), ("claude-sonnet-4-5-20250929", (3.0, 15.0)),
                  ("claude-sonnet-4-20250514", (3.0, 15.0)), ("claude-3-5-haiku-20241022", (0.8, 4.0)),
                  ("claude-fable-5-1", (10.0, 50.0)), ("claude-opus-4-6", (5.0, 25.0)),
                  ("claude-sonnet-4-6", (3.0, 15.0))):
    c("  %-28s" % mid, run.prices_for(mid), want)
c("  cache reads are 0.1x by default", run.cache_read_rate("claude-sonnet-5"), 0.1)
c("  ...and on Fable 5", run.cache_read_rate("claude-fable-5"), 0.1)
c("  but 0.025x on Fable 5.1", run.cache_read_rate("claude-fable-5-1"), 0.025)
c.truthy("  so a million cached reads on Fable 5.1 cost $0.25",
         abs(run.turn_cost("claude-fable-5-1", cache_read=1_000_000) - 0.25) < 1e-9)
c.truthy("  and its cache saved 0.975 of the input price on them",
         abs(run.cache_saved("claude-fable-5-1", 1_000_000, 0) - 9.75) < 1e-9)

print("\nArc Watch is given the default model's real rates, not the unknown-model fallback:")


async def usage():
    sid = session.create(OWNER, "desk")
    transport = httpx.ASGITransport(app=run.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
        return (await cl.get("/api/usage?days=7", cookies={run.COOKIE: sid})).json()


try:
    for configured in ("claude-sonnet-5", "claude-fable-5-1"):
        real_model = run.MODEL
        run.MODEL = configured
        try:
            p = asyncio.run(usage())["prices"]
        finally:
            run.MODEL = real_model
        p_in, p_out = run.prices_for(configured)
        c("  %-17s in and out" % configured, (p["in"], p["out"]), (p_in, p_out))
        c.truthy("  %-17s cache read at its own rate" % configured,
                 abs(p["cache_read"] - p_in * run.cache_read_rate(configured)) < 1e-12)
        c.truthy("  %-17s cache write at 1.25x" % configured, abs(p["cache_write"] - p_in * 1.25) < 1e-12)
finally:
    session.revoke_all()

print("\nThe booking can only happen once:")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py"), encoding="utf-8").read()
chat_src = src.split("async def chat(")[1].split("\n@app.")[0]
c("  stats.record for the turn is written in one place only", chat_src.count("tok_in=tokens_in, tok_out=tokens_out"), 1)
c.truthy("  guarded by a once-only flag", "if booked:" in chat_src and "booked.append(True)" in chat_src)

c.done()
