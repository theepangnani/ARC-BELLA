# -*- coding: utf-8 -*-
"""A slow web tool no longer freezes everybody else.

The tool loop in chat() called dispatch_tool straight on the event loop, and
every tool is synchronous. A Slack read retrying against a dead network held
the one thread for minutes: other people's turns, /api/health, and the poll
that rings alarms all waited behind it. Tools from kits that only talk to a
web API now run on a worker thread (dispatch_off_loop). pc and automation stay
on the loop, because COM and the desktop expect the thread they started on,
and any kit not on the allowlist stays there too.
"""
import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

import httpx     # noqa: E402
import memory    # noqa: E402
import whose     # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
OWNER = "owner@example.com"

print("Which kits leave the loop:")
c("  a market outlook goes to a thread", run.TOOL_OWNER["market_outlook"] in run.OFF_LOOP_KITS, True)
c("  a linked account (Slack) goes to a thread",
  run.TOOL_OWNER["slack_search"] in run.LINK_KITS.values(), True)
for kit in (run.pc, run.automation):
    c("  %s stays on the loop" % kit.__name__, kit in run.OFF_LOOP_KITS or kit in run.LINK_KITS.values(), False)


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name):
        self.name, self.input, self.id = name, {}, "offloop-" + name


class Usage:
    input_tokens = output_tokens = 5
    cache_read_input_tokens = cache_creation_input_tokens = 0


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


async def turn_with(tool, blocking):
    """One owner turn that calls `tool`, whose dispatch blocks (time.sleep, as a
    hung HTTP call would) until /api/health has been asked mid-turn."""
    release = threading.Event()
    seen = {}
    loop_thread = threading.get_ident()

    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                if not seen.get("asked"):
                    seen["asked"] = True
                    return Resp([ToolBlk(tool)], stop="tool_use")
                return Resp([Blk("Done.")])

        async def close(self):
            pass

    def slow(name, args, **kw):
        seen["on_loop"] = threading.get_ident() == loop_thread
        seen["who"] = (whose.current(), memory.current())
        seen["started"] = time.monotonic()
        if blocking:
            release.wait(5)
        return "fine", False

    run.app.state.claude = Fake()
    real = run.dispatch_tool
    run.dispatch_tool = slow
    try:
        sid = session.create(OWNER, "desk")
        transport = httpx.ASGITransport(app=run.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
            chat = asyncio.create_task(cl.post("/api/chat", cookies={run.COOKIE: sid}, json={
                "messages": [{"role": "user", "content": "check it"}], "allow_actions": True}))
            for _ in range(200):
                if "started" in seen or chat.done():
                    break
                await asyncio.sleep(0.01)
            t0 = time.monotonic()
            try:
                health = await asyncio.wait_for(cl.get("/api/health", cookies={run.COOKIE: sid}), 3)
                seen["health"] = (health.status_code, time.monotonic() - t0, chat.done())
            except asyncio.TimeoutError:
                seen["health"] = None
            release.set()
            seen["chat"] = (await chat).status_code
    finally:
        run.dispatch_tool = real
        session.revoke_all()
    return seen


print("\nA web tool that hangs mid-turn:")
# Only on_loop is decided by the allowlist; the rest shows what that buys.
# With the tool on the loop, the health request cannot even start until the
# tool returns, so wait_for gives up and "health" is None.
s = asyncio.run(turn_with("market_outlook", blocking=True))
c("  the tool ran on a worker thread", s.get("on_loop"), False)
c("  ...still knowing who asked", s.get("who"), (OWNER, OWNER))
c.truthy("  /api/health answered while the tool was still running",
         bool(s.get("health")) and s["health"][0] == 200 and s["health"][2] is False)
c("  the turn itself still finished", s.get("chat"), 200)

print("\nA desktop tool:")
s = asyncio.run(turn_with("screenshot", blocking=False))
c("  ran on the event loop's own thread, as COM needs", s.get("on_loop"), True)
c("  the turn finished", s.get("chat"), 200)

c.done()
