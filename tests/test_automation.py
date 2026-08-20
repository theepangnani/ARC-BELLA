# -*- coding: utf-8 -*-
"""Auto-clicker, held keys and macros — mostly: can you always switch it off.

A program that drives your mouse is only acceptable if stopping it is trivial,
instant, and never in doubt. So the checks that matter are the safety ones:
everything is bounded, one job at a time, stop works and works fast, a held key
always comes back up, and none of it is reachable from a phone — where the
mouse it would move is not the one in front of you.

Nothing here generates real input. The worker is driven with a counting stub,
which is the same code path with a different pen in its hand.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

import automation   # noqa: E402
import pc           # noqa: E402

c = Check()


def quiet():
    automation.stop_automation()


print("Everything is bounded, whatever it is asked for:")
r, s, n = automation._bounds(5, 30)
c("  a sane request is left alone", (r, s), (5.0, 30.0))
r, s, n = automation._bounds(10000, 10000)
c("  an absurd rate is capped", r, automation.MAX_RATE)
c("  an absurd duration is capped", s, float(automation.MAX_SECONDS))
c("  and the event count with it", n, automation.MAX_EVENTS)
r, s, n = automation._bounds(0, 0)
c.truthy("  zero becomes a sane default, not an infinite loop", r > 0 and s > 0)
r, s, n = automation._bounds("nonsense", None)
c.truthy("  so does rubbish", r > 0 and s > 0)
c.truthy("  the cap is minutes, not hours", automation.MAX_SECONDS <= 3600)

print("\nA job runs, reports, and stops on command:")
hits = {"n": 0}
err = automation._start("testing", "test", 5.0, lambda: hits.__setitem__("n", hits["n"] + 1),
                        0.01, 400)
c("  it started", err, None)
time.sleep(0.15)
c.truthy("  it is running", automation.running())
c.truthy("  status says what it is doing", "testing" in automation.automation_status())
c.truthy("  it is actually doing it (%d)" % hits["n"], hits["n"] > 3)

t0 = time.time()
msg = automation.stop_automation()
took = time.time() - t0
c.truthy("  stop reports what it got through", "Stopped" in msg)
c.truthy("  and stops within a moment (%.2fs)" % took, took < 1.0)
after = hits["n"]
time.sleep(0.15)
c("  and nothing happens afterwards", hits["n"], after)
c("  running() agrees", automation.running(), False)
c.truthy("  stopping nothing is not an error", "Nothing was running"
         in automation.stop_automation())

print("\nOnly one at a time, so 'stop' is never ambiguous:")
automation._start("first", "a", 5.0, lambda: None, 0.05, 100)
second = automation._start("second", "b", 5.0, lambda: None, 0.05, 100)
c.truthy("  the second is refused", second and "Already" in second)
c.truthy("  and says how to clear it", "stop first" in (second or ""))
quiet()

print("\nA job ends by itself even if nobody says stop:")
hits["n"] = 0
automation._start("brief", "x", 0.25, lambda: hits.__setitem__("n", hits["n"] + 1),
                  0.01, 10000)
time.sleep(0.6)
c("  it stopped on its own", automation.running(), False)
c.truthy("  after doing a bounded amount of work (%d)" % hits["n"], hits["n"] < 10000)
quiet()

print("\nArguments are checked before anything moves:")
c.truthy("unknown key refused", "don't know the key" in automation.hold_key("zzz"))
c.truthy("  and lists what it does know", "space" in automation.hold_key("zzz"))
c.truthy("empty macro refused", "Give me the keys" in automation.key_macro(""))
c.truthy("macro with an unknown key refused", "I don't know" in automation.key_macro("w q9"))
c("  nothing started as a result", automation.running(), False)

print("\nThe keys a game actually needs exist:")
for k in ["w", "a", "s", "d", "shift", "ctrl", "space", "1", "one", "f12", "esc"]:
    c.truthy("  %-6s" % k, k in pc._VK)
c.truthy("  and a-z is all there", all(chr(x) in pc._VK for x in range(ord("a"), ord("z") + 1)))

print("\nIt is not reachable from a phone:")
import run  # noqa: E402
remote = {t["name"] for t in run.all_tools(local=False)}
localt = {t["name"] for t in run.all_tools(local=True)}
for name in ["auto_click", "hold_key", "key_macro"]:
    c("  %-16s offered locally, hidden remotely" % name,
      (name in localt, name in remote), (True, False))
out, err = run.dispatch_tool("auto_click", {}, local=False)
c("  and refused if asked anyway", err, True)
c.truthy("  with a reason, not a shrug", "desktop app" in out)

print("\nStarting is gated by consent; stopping never is:")
c.truthy("  auto_click needs the user's say-so", "auto_click" not in run.PASSIVE_TOOLS)
c.truthy("  hold_key too", "hold_key" not in run.PASSIVE_TOOLS)
c.truthy("  key_macro too", "key_macro" not in run.PASSIVE_TOOLS)
# An auto-clicker you must authorise ARC to switch off is a hostage situation.
c.truthy("  but STOPPING is always allowed", "stop_automation" in run.PASSIVE_TOOLS)
c.truthy("  as is asking what is running", "automation_status" in run.PASSIVE_TOOLS)
c("  and no guest gets any of it",
  [n for n in ["auto_click", "hold_key", "key_macro", "stop_automation"]
   if n in run.GUEST_TOOLS], [])

print("\nARC is told the rules:")
page = open(HUD, encoding="utf-8").read()
c.truthy("  'stop' means stop first, talk after",
         'if they say "stop" while something is repeating' in page)
c.truthy("  one at a time, bounded", "only one runs at a time" in page)
c.truthy("  mentions the game-ban risk once, lightly",
         "online games often ban input automation" in page)
c.truthy("  and is told not to lecture about it", "don't repeat it every time" in page)

print("\nThe module says the same thing to whoever reads it:")
src = open(ARC / "automation.py", encoding="utf-8").read()
c.truthy("  bounded", "EVERYTHING IS BOUNDED" in src)
c.truthy("  stoppable", "STOPPING IS INSTANT AND ALWAYS AVAILABLE" in src)
c.truthy("  the held key is released in a finally", "finally:" in src
         and "KEYEVENTF_KEYUP" in src)

quiet()
c.done()
