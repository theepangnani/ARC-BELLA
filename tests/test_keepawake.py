# -*- coding: utf-8 -*-
"""An alarm the computer is awake for, and one it was not.

Windows' own power log showed this PC idle-sleeping several times a day, and
every "background loop died" line in repairs.log landed within twenty seconds
of a wake. The loop was fine. The alarms were not: one that came due more than
ten minutes into a sleep was written off SILENTLY on wake, and a one-off was
deleted, so nobody ever found out it hadn't gone off.

Three changes, and a fourth found on the way:

  · KEEP-AWAKE. While an alarm is armed, the background loop resets the
    system's idle timer every cycle. Done by THAT loop, not a thread of its own:
    if it stops, the pinging stops and the PC may sleep. Holding a machine
    awake for a loop that can no longer ring anything is the worst of both.
  · MISSED ALARMS ARE REPORTED. Still not rung late — being woken at 09:40 by
    the 07:00 alarm is worse than not being woken — but recorded, and handed to
    the page once.
  · The notice says what ARC knows and no more: it wasn't running its checks.
    It does not claim to know the machine was asleep.
  · FOUND ON THE WAY: price alerts, rules and alarms shared ONE try in the loop.
    alerts.evaluate has no error handling of its own, so a malformed price
    alert skipped alarm evaluation every cycle — and the heartbeat, outside the
    try, went on reporting a healthy loop. So alarms would silently never ring.
    Each job has its own guard now, and alarms go first.
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ.pop("ARC_ALARM_KEEPS_AWAKE", None)

import alarm    # noqa: E402
import awake    # noqa: E402

c = Check()

# Never ask the real Windows to change its power state from a test run.
pings = []
awake._ping = lambda: pings.append(time.time()) or True


def reset():
    alarm._save([])
    alarm.missed()                      # consume anything left over
    awake._state.update(holding=False, since=0.0, pings=0, error="")
    pings.clear()


print("Keep-awake follows whether an alarm is armed:")
reset()
c("  on by default", awake.ENABLED, True)
c("  no alarm: nothing held", awake.hold(alarm.armed()), False)
c("  ...and Windows is not asked", pings, [])
alarm.set_alarm("7am", "weekdays", "gym")
c("  an alarm is armed", alarm.armed(), True)
c("  so the PC is held", awake.hold(alarm.armed()), True)
c("  by resetting the idle timer", len(pings), 1)
awake.hold(alarm.armed())
c("  ...again every cycle, because the ping form does not last", len(pings), 2)
c.truthy("  and it says since when", awake.status()["since"] > 0)
alarm._save([])
c("  alarm gone: released", awake.hold(alarm.armed()), False)
c("  ...and Windows is left alone", len(pings), 2)
c("  status agrees", awake.status()["holding"], False)

print("\nIt never raises, because it runs in the loop that rings alarms:")
def boom():
    raise OSError("kernel32 unavailable")
real = awake._ping
awake._ping = boom
c("  a failing ping is reported, not raised", awake.hold(True), False)
c.truthy("  ...and the reason kept", "kernel32" in awake.status()["error"])
awake._ping = real

print("\nIt can be turned off, for anyone who would rather the PC slept:")
awake.ENABLED = False
pings.clear()
c("  disabled: not held even with an alarm", awake.hold(True), False)
c("  ...and nothing pinged", pings, [])
awake.ENABLED = True
src = io.open(ARC / "awake.py", encoding="utf-8").read()
c.truthy("  by one variable", "ARC_ALARM_KEEPS_AWAKE" in src)
# ES_CONTINUOUS would set a lasting state that outlives the loop that set it.
c("  the ping form, never a lasting state", "ES_CONTINUOUS" in src.split('"""', 2)[2], False)
c.truthy("  and it keeps the SYSTEM awake, not the screen",
         "ES_SYSTEM_REQUIRED = 0x00000001" in src and "ES_DISPLAY_REQUIRED" not in src)

print("\nAn alarm missed while asleep is not rung late, and IS reported:")
reset()
alarm.set_alarm("7am", None, "dentist")            # a one-off
items = alarm._load()
items[0]["next_at"] = time.time() - 2 * 3600       # due two hours into a sleep
alarm._save(items)
alarm.evaluate()
c("  it did not ring two hours late", alarm.ringing(), [])
c("  the one-off is spent and gone, as before", alarm._load(), [])
gone = alarm.missed()
c("  but the miss was recorded", len(gone), 1)
c("  ...with its time", gone[0]["time"], "7am")
c("  ...and its label", gone[0]["label"], "dentist")
c("  handed over ONCE", alarm.missed(), [])
said = alarm.missed_message(gone)
print("    " + said)
c.truthy("  it says it didn't go off", "didn't go off" in said)
c.truthy("  names it", "7am" in said and "dentist" in said)
# ARC knows its checks weren't running. It does not know WHY.
c.truthy("  and does not claim to know the reason for certain",
         "asleep or I wasn't running" in said)
c("  nothing missed, nothing said", alarm.missed_message([]), "")

print("\nA repeating one is rescheduled, and still reported:")
reset()
alarm.set_alarm("6:30am", "daily")
items = alarm._load()
items[0]["next_at"] = time.time() - 3600
alarm._save(items)
alarm.evaluate()
c("  still set", len(alarm._load()), 1)
c.truthy("  for the future", alarm._load()[0]["next_at"] > time.time())
c("  and reported", len(alarm.missed()), 1)

print("\nA few minutes late still rings, as it always did:")
reset()
alarm.set_alarm("7am")
items = alarm._load()
items[0]["next_at"] = time.time() - 120            # inside STALE_AFTER
alarm._save(items)
alarm.evaluate()
c("  it rings", len(alarm.ringing()), 1)
c("  and is not reported as missed", alarm.missed(), [])

print("\nSeveral missed at once read as one sentence:")
for h in ("6am", "7am", "8am", "9am"):
    reset() if h == "6am" else None
    alarm.set_alarm(h)
items = alarm._load()
for a in items:
    a["next_at"] = time.time() - 3 * 3600
alarm._save(items)
alarm.evaluate()
said = alarm.missed_message(alarm.missed())
print("    " + said)
c.truthy("  counted", said.startswith("4 alarms didn't go off"))
c.truthy("  three named, the rest summed", "and 1 more" in said)

print("\nThe loop: alarms first, and nothing can starve them:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
loop = run_src[run_src.index("async def _monitor_loop"):run_src.index("home = asyncio.get_running_loop()")]
c.truthy("  each job has its own guard", "async def job(name, fn):" in loop)
c.truthy("  alarms run before price alerts",
         loop.index('job("alarms"') < loop.index('job("price alerts"'))
c.truthy("  ...and before standing rules",
         loop.index('job("alarms"') < loop.index('job("standing rules"'))
c.truthy("  keep-awake is asked every cycle", 'job("keep-awake", lambda: awake.hold(alarm.armed()))' in loop)
c.truthy("  a persistent fault is said once, not every thirty seconds",
         "if failing.get(name) != msg:" in loop)

# And actually through the real loop, with price alerts broken.
reset()
import run    # noqa: E402
from starlette.testclient import TestClient   # noqa: E402
calls = {"alarms": 0, "hold": []}
run.alerts.evaluate = lambda: (_ for _ in ()).throw(KeyError("symbol"))
real_eval = run.alarm.evaluate
run.alarm.evaluate = lambda: calls.__setitem__("alarms", calls["alarms"] + 1)
real_hold = run.awake.hold
run.awake.hold = lambda needed: calls["hold"].append(needed) or False
with TestClient(run.app):
    deadline = time.time() + 8
    while time.time() < deadline and not calls["hold"]:
        time.sleep(0.1)
run.alarm.evaluate, run.awake.hold = real_eval, real_hold
c("  with price alerts raising every cycle, alarms still evaluated",
  calls["alarms"] >= 1, True)
c("  and keep-awake still asked", len(calls["hold"]) >= 1, True)

print("\nThe page is told, once:")
page = io.open(HUD, encoding="utf-8").read()
c.truthy("  the poll reads the notice", "d.missed_said" in page)
c.truthy("  puts it in the transcript", 'addEntry("sys", "SYSTEM", d.missed_said)' in page)
c.truthy("  and speaks it only when Bella is free",
         'if (state.mode === "standby" && !chatMode) speak(d.missed_said);' in page)
c.truthy("  the route hands over missed alarms", '"missed_said": alarm.missed_message(gone)' in run_src)

c.done()
