# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""One guest cannot spend the owner's day.

The daily caps were one counter for everybody, so a guest running long turns
in parallel could reach DAILY_COST_CAP and the owner would be refused until
midnight. What this holds:

  · each guest has their own dollars and turns a day
  · guests together stop at GUEST_SHARE of the deployment's caps, so the rest
    of the day is the owner's whatever guests do
  · a guest has GUEST_CONCURRENT turns in flight; a turn that never books stops
    counting after GUEST_PENDING_SECONDS
  · a turn that ends, or fails, is taken off the in-flight count and charged
  · the owner is never in the guest pool
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com,guest@example.com,other@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com,other@example.com"
for k in ("ARC_GUEST_DAILY_COST", "ARC_GUEST_DAILY_TURNS"):
    os.environ.pop(k, None)

import anthropic   # noqa: E402
import httpx       # noqa: E402
from starlette.testclient import TestClient   # noqa: E402
import run         # noqa: E402
import session     # noqa: E402

c = Check()
OWNER, GUEST, OTHER = "owner@example.com", "guest@example.com", "other@example.com"

print("The numbers, pinned so moving one is a decision:")
c("  a guest's dollars a day", run.GUEST_DAILY_COST, 1.0)
c("  a guest's turns a day", run.GUEST_DAILY_TURNS, 150)
c("  guests together, as a share of the deployment's caps", run.GUEST_SHARE, 0.5)
c("  turns in flight per guest", run.GUEST_CONCURRENT, 2)
c("  an unbooked turn stops counting after", run.GUEST_PENDING_SECONDS, 300)


class Blk:
    type, text = "text", "Done."


class Usage:
    def __init__(self, n):
        self.input_tokens, self.output_tokens = n, 0
        self.cache_read_input_tokens = self.cache_creation_input_tokens = 0
        self.server_tool_use = None


class Resp:
    def __init__(self, n):
        self.content, self.stop_reason, self.usage = [Blk()], "end_turn", Usage(n)


mode = {"tokens": 1000, "fail": False}


class Fake:
    class messages:
        @staticmethod
        async def create(**kw):
            if mode["fail"]:
                raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
            return Resp(mode["tokens"])

    async def close(self):
        pass


def fresh():
    run._day.update(count=0, cost=0.0)
    run._hits.clear()
    run._guest_day.update(stamp=time.strftime("%Y-%m-%d"), spend=run.defaultdict(float),
                          turns=run.defaultdict(int), pending=run.defaultdict(run.deque))
    mode.update(tokens=1000, fail=False)


BODY = {"messages": [{"role": "user", "content": "hello"}]}
real = (run.GUEST_DAILY_COST, run.GUEST_DAILY_TURNS, run.DAILY_COST_CAP, run.DAILY_CAP,
        run.RATE_PER_MIN, run.RATE_PER_HOUR)
run.RATE_PER_MIN = run.RATE_PER_HOUR = 10_000
try:
    with TestClient(run.app) as client:
        run.app.state.claude = Fake()
        O = {run.COOKIE: session.create(OWNER, "browser")}
        G = {run.COOKIE: session.create(GUEST, "phone")}
        X = {run.COOKIE: session.create(OTHER, "phone")}

        def ask(cookies):
            return client.post("/api/chat", cookies=cookies, json=BODY)

        print("\nA guest's own dollars:")
        fresh()
        one_turn = run.turn_cost(run.MODEL, 1000)
        run.GUEST_DAILY_COST = one_turn * 1.5
        c("  the first turn is answered", ask(G).status_code, 200)
        c("  ...and charged to that guest", round(run._guest_day["spend"][GUEST], 9), round(one_turn, 9))
        c("  ...and is no longer in flight", len(run._guest_day["pending"][GUEST]), 0)
        c("  the second is answered (the budget is not yet spent)", ask(G).status_code, 200)
        r = ask(G)
        c("  the third is refused as the day's allowance", (r.status_code, "allowance" in r.text), (429, True))
        c("  another guest still has theirs", ask(X).status_code, 200)
        c("  the owner is not in the guest pool", ask(O).status_code, 200)
        c("  ...and the owner's turn charged no guest", OWNER in run._guest_day["spend"], False)
        run.GUEST_DAILY_COST = real[0]

        print("\nA guest's own turns:")
        fresh()
        run.GUEST_DAILY_TURNS = 2
        codes = [ask(G).status_code for _ in range(3)]
        c("  two, then refused", codes, [200, 200, 429])
        run.GUEST_DAILY_TURNS = real[1]

        print("\nGuests together keep to their share, and the owner keeps the rest:")
        fresh()
        run.GUEST_DAILY_COST = 0          # no per-guest cap, to test the pool alone
        run.DAILY_COST_CAP = one_turn * 3  # guests may spend 1.5 turns' worth together
        c("  guest one", ask(G).status_code, 200)
        c("  guest two", ask(X).status_code, 200)
        r = ask(G)
        c("  then guests are refused, all of them", (r.status_code, "Guest accounts" in r.text), (429, True))
        c("  ...while the owner is still answered", ask(O).status_code, 200)
        run.DAILY_COST_CAP, run.GUEST_DAILY_COST = real[2], real[0]
        fresh()
        run.DAILY_CAP = 4
        ask(G), ask(X)
        c("  the same for turns: half the deployment's, then no more", ask(G).status_code, 429)
        c("  ...and the owner's turn still counts toward the full cap only", ask(O).status_code, 200)
        run.DAILY_CAP = real[3]

        print("\nTurns in flight:")
        fresh()
        now = time.time()
        run._guest_day["pending"][GUEST].extend([now, now])
        r = ask(G)
        c("  a third at once is refused", (r.status_code, "still working" in r.text), (429, True))
        c("  ...and the refused one was never counted", run._guest_day["turns"][GUEST], 0)
        fresh()
        old = time.time() - run.GUEST_PENDING_SECONDS - 1
        run._guest_day["pending"][GUEST].extend([old, old])
        c("  a turn that never booked stops counting in time", ask(G).status_code, 200)

        print("\nTurns still running count at their reserve:")
        c("  the reserve per turn in flight", run.GUEST_TURN_RESERVE, 0.10)
        fresh()
        run.GUEST_DAILY_COST = 0
        run.DAILY_COST_CAP = 0.30                # guests together: $0.15
        now = time.time()
        run._guest_day["pending"][OTHER].extend([now, now])   # $0.20 held, nothing booked
        r = ask(G)
        c("  another guest's two running turns fill the guests' share",
          (r.status_code, "Guest accounts" in r.text), (429, True))
        c("  ...while the owner is still answered", ask(O).status_code, 200)
        fresh()
        run.GUEST_DAILY_COST = 0.15
        run._guest_day["spend"][GUEST] = 0.06
        run._guest_day["pending"][GUEST].append(time.time())  # + $0.10 held = $0.16
        r = ask(G)
        c("  a guest's own running turn counts against their own dollars",
          (r.status_code, "allowance" in r.text), (429, True))
        fresh()
        run._guest_day["spend"][GUEST] = 0.06
        stale = time.time() - run.GUEST_PENDING_SECONDS - 1
        run._guest_day["pending"][GUEST].append(stale)
        c("  ...but one that stopped counting holds nothing back", ask(G).status_code, 200)
        run.GUEST_DAILY_COST, run.DAILY_COST_CAP = real[0], real[2]

        print("\nA turn that fails still leaves flight:")
        fresh()
        mode["fail"] = True
        c("  the model unreachable", ask(G).status_code, 502)
        c("  nothing charged", run._guest_day["spend"][GUEST], 0.0)
        c("  and not in flight", len(run._guest_day["pending"][GUEST]), 0)

        print("\nA request that stops before the model holds no slot:")
        fresh()
        r = client.post("/api/chat", cookies=G, json=dict(BODY, system="x" * (run.MAX_SYSTEM_CHARS + 1)))
        c("  a refused payload (system too long)", r.status_code, 400)
        c("  ...is not left in flight", len(run._guest_day["pending"][GUEST]), 0)
        r = client.post("/api/summarize", cookies=G, json={"note": "", "messages": []})
        c("  a note with nothing to fold in", r.status_code, 200)
        c("  ...is not left in flight", len(run._guest_day["pending"][GUEST]), 0)
        mode["fail"] = True
        r = client.post("/api/summarize", cookies=G, json={
            "note": "", "messages": [{"role": "user", "content": "hi"}]})
        c("  a note whose model call fails", r.status_code, 502)
        c("  ...is not left in flight either", len(run._guest_day["pending"][GUEST]), 0)
        mode["fail"] = False
        r = client.post("/api/summarize", cookies=G, json={
            "note": "", "messages": [{"role": "user", "content": "hi"}]})
        c("  a note that is written is charged to the guest",
          (r.status_code, run._guest_day["spend"][GUEST] > 0, len(run._guest_day["pending"][GUEST])),
          (200, True, 0))

        print("\nA new day:")
        fresh()
        run._guest_day["spend"][GUEST] = 99.0
        run._guest_day["stamp"] = "1999-01-01"
        c("  yesterday's guest spend does not lock out today", ask(G).status_code, 200)
        session.revoke_all()
finally:
    (run.GUEST_DAILY_COST, run.GUEST_DAILY_TURNS, run.DAILY_COST_CAP, run.DAILY_CAP,
     run.RATE_PER_MIN, run.RATE_PER_HOUR) = real

c.done()
