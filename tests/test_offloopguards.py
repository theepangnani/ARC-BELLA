# -*- coding: utf-8 -*-
"""Off the loop, a tool still serves the right person, and a crash is still a reply.

test_offloop shows an OWNER's web tool leaving the event loop. Two things it
does not hold, asked for by Claude 1 after dispatch_off_loop landed:

  · a GUEST's tool on the worker thread still runs as that guest — whose and
    memory point at them, and the real dispatch_tool's guest gate still runs
    there, not only on the loop;
  · a linked-account kit that RAISES on the worker thread does not turn the
    whole turn into a 500. asyncio.to_thread hands the exception straight back
    to chat(), so whatever caught a crash on the loop has to catch it here too.

The real dispatch_tool runs in both; only the kit's own run_tool is swapped,
so the gates in between are the ones that ship.
"""
import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com,guest@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

import httpx     # noqa: E402
import memory    # noqa: E402
import whose     # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
OWNER, GUEST = "owner@example.com", "guest@example.com"


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name):
        self.name, self.input, self.id = name, {}, "guard-" + name


class Usage:
    input_tokens = output_tokens = 5
    cache_read_input_tokens = cache_creation_input_tokens = 0
    server_tool_use = None


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


async def turn(who, tool, kit_run):
    """One turn by `who` in which the model calls `tool` once; `kit_run`
    stands in for that tool's kit.run_tool. Returns what the tool saw and
    what came back to the page."""
    seen = {"results": []}
    loop_thread = threading.get_ident()

    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                if not seen.get("asked"):
                    seen["asked"] = True
                    return Resp([ToolBlk(tool)], stop="tool_use")
                for m in kw.get("messages", []):
                    if isinstance(m.get("content"), list):
                        seen["results"] += [b for b in m["content"]
                                            if isinstance(b, dict) and b.get("type") == "tool_result"]
                return Resp([Blk("Done.")])

        async def close(self):
            pass

    def spy(name, args, *a, **kw):
        seen["on_loop"] = threading.get_ident() == loop_thread
        seen["who"] = (whose.current(), memory.current())
        return kit_run(name, args)

    kit = run.TOOL_OWNER[tool]
    real = kit.run_tool
    kit.run_tool = spy
    run.app.state.claude = Fake()
    try:
        sid = session.create(who, "guard")
        transport = httpx.ASGITransport(app=run.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
            r = await cl.post("/api/chat", cookies={run.COOKIE: sid}, json={
                "messages": [{"role": "user", "content": "check it"}], "allow_actions": True})
        seen["status"] = r.status_code
        try:
            seen["reply"] = r.json().get("reply", "")
        except ValueError:
            seen["reply"] = None
    finally:
        kit.run_tool = real
        session.revoke_all()
    return seen


print("A guest's tool on the worker thread:")
c.truthy("  market_outlook is a guest tool", "market_outlook" in run.GUEST_TOOLS)
c.truthy("  ...and leaves the loop", run.TOOL_OWNER["market_outlook"] in run.OFF_LOOP_KITS)
s = asyncio.run(turn(GUEST, "market_outlook", lambda n, a: ("fine", False)))
c("  it ran off the loop", s.get("on_loop"), False)
c("  whose and memory are the guest's, not the owner's", s.get("who"), (GUEST, GUEST))
c("  the turn finished", s.get("status"), 200)

print("\nThe guest gate still runs on the worker thread:")
owner_only = next(t for t, kit in run.TOOL_OWNER.items()
                  if (kit in run.OFF_LOOP_KITS or kit in run.LINK_KITS.values())
                  and t not in run.guest_tools())
s = asyncio.run(turn(GUEST, owner_only, lambda n, a: ("SHOULD NOT RUN", False)))
c("  a guest's call to %s never reached the kit" % owner_only, "who" in s, False)
c.truthy("  ...and was refused as not on a guest account",
         any("guest account" in str(b.get("content")) for b in s["results"]))
c("  the turn still finished", s.get("status"), 200)

print("\nA linked-account tool that crashes on the worker thread:")
c.truthy("  slack_search is a linked-account kit",
         run.TOOL_OWNER["slack_search"] in run.LINK_KITS.values())


def boom(name, args):
    raise RuntimeError("socket closed mid-read")


s = asyncio.run(turn(OWNER, "slack_search", boom))
c("  it crashed off the loop, where the guard is aimed", s.get("on_loop"), False)
c("  the turn is not a 500", s.get("status"), 200)
c.truthy("  the model was handed an error result, not silence",
         any(b.get("is_error") for b in s["results"]))
c.truthy("  and the page got a reply", bool(s.get("reply")))

c.done()
