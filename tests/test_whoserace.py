# -*- coding: utf-8 -*-
"""Two people talking to ARC at once each keep their own data.

whose.py and memory.py decide whose notes, plan, panels and remembered facts a
request reads and writes. They held the address in a threading.local(), and
/api/chat is async — every request runs on the event loop's one thread — so
there was ONE slot for everybody. The owner's turn set "owner", awaited Claude,
and a guest's request arriving in that gap set the guest. When the owner's turn
came back to run its tool, current() said the guest: the owner's note filed in
the guest's store, or the guest reading the owner's.

Found on the laptop while planning streaming, which would have made the gap
longer; reproduced on the desktop with two overlapping requests. The fix is a
ContextVar, which belongs to the request's task and survives its awaits.

This suite reproduces the original failure THROUGH /api/chat — two real
overlapping requests, a model that makes the owner's turn wait until the
guest's has set its address — rather than only testing the primitive, because
the primitive was never the thing in doubt; the gap was.
"""
import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

import httpx     # noqa: E402
import memory    # noqa: E402
import whose     # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
OWNER, GUEST = "owner@example.com", "guest@example.com"

print("The primitive: each task keeps its own address across an await:")


async def two_tasks():
    seen = {}
    gate = asyncio.Event()

    async def owner():
        whose.use(OWNER); memory.use(OWNER)
        await gate.wait()                       # the guest runs in this gap
        seen["owner"] = (whose.current(), memory.current())

    async def guest():
        whose.use(GUEST); memory.use(GUEST)
        gate.set()
        seen["guest"] = (whose.current(), memory.current())

    await asyncio.gather(owner(), guest())
    return seen


seen = asyncio.run(two_tasks())
c("  the owner's task still sees the owner", seen["owner"], (OWNER, OWNER))
c("  the guest's task sees the guest", seen["guest"], (GUEST, GUEST))


async def handed_to_a_thread():
    whose.use(OWNER); memory.use(OWNER)
    return await asyncio.to_thread(lambda: (whose.current(), memory.current()))

c("  work handed to a thread still knows who asked",
  asyncio.run(handed_to_a_thread()), (OWNER, OWNER))

outside = {}
t = threading.Thread(target=lambda: outside.update(v=(whose.current(), memory.current())))
t.start(); t.join()
c("  a thread nobody's request started sees the default, as the loops need",
  outside["v"], (whose.DEFAULT, "owner"))


# --- through the real route ------------------------------------------------
class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name, i):
        self.name, self.input, self.id = name, {}, "race%d" % i


class Usage:
    input_tokens = output_tokens = 5
    cache_read_input_tokens = cache_creation_input_tokens = 0


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


def who_is_asking(kw):
    text = str(kw.get("messages", [])[:1])
    return "guest" if "i am the guest" in text else "owner"


async def overlap():
    guest_in = asyncio.Event()
    rounds = {"owner": 0, "guest": 0}
    at_dispatch = []

    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                who = who_is_asking(kw)
                rounds[who] += 1
                if who == "guest":
                    # By now the guest's request has set ITS address — the
                    # moment the old code let it overwrite the owner's.
                    guest_in.set()
                    return Resp([Blk("Hello, guest.")])
                if rounds["owner"] == 1:
                    await asyncio.wait_for(guest_in.wait(), 10)
                    return Resp([ToolBlk("list_notes", 1)], stop="tool_use")
                return Resp([Blk("Done.")])

        async def close(self):
            pass

    run.app.state.claude = Fake()
    real = run.dispatch_tool

    def spy(name, args, **kw):
        at_dispatch.append((name, whose.current(), memory.current()))
        return "no notes", False

    run.dispatch_tool = spy
    try:
        o = session.create(OWNER, "desk")
        g = session.create(GUEST, "phone")
        transport = httpx.ASGITransport(app=run.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cl:
            def ask(sid, text):
                return cl.post("/api/chat", cookies={run.COOKIE: sid}, json={
                    "messages": [{"role": "user", "content": text}],
                    "allow_actions": True})
            owner_task = asyncio.create_task(ask(o, "list my notes"))
            await asyncio.sleep(0.05)            # the owner is inside its await
            guest_r = await ask(g, "hello, i am the guest")
            owner_r = await owner_task
    finally:
        run.dispatch_tool = real
        session.revoke_all()
    return owner_r, guest_r, at_dispatch, rounds


owner_r, guest_r, at_dispatch, rounds = asyncio.run(overlap())
print("\nTwo overlapping requests through /api/chat:")
c("  the owner's turn succeeds", owner_r.status_code, 200)
c("  the guest's turn succeeds", guest_r.status_code, 200)
c("  and they really did overlap (the guest answered inside the owner's turn)",
  (rounds["guest"] >= 1, rounds["owner"] >= 2), (True, True))
c("  the owner's tool ran exactly once", len(at_dispatch), 1)
if at_dispatch:
    name, w, m = at_dispatch[0]
    c("  whose.current() at the owner's tool is the OWNER", w, OWNER)
    c("  memory.current() at the owner's tool is the OWNER", m, OWNER)

print("\nAnd it cannot quietly come back:")
for mod in ("whose.py", "memory.py", "plan.py", "panels.py", "notes.py"):
    p = ARC / mod
    if p.exists():
        # An assignment, not a mention: the modules explain in prose what
        # threading.local() got wrong, and that explanation must be allowed
        # to stay.
        import re
        src = p.read_text(encoding="utf-8")
        c("  %-10s holds no per-request state in threading.local()" % mod,
          bool(re.search(r"^\s*\w+\s*=\s*threading\.local\(", src, re.M)), False)

c.done()
