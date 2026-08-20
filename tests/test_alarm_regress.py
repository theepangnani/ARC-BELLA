# -*- coding: utf-8 -*-
"""Regressions for the bugs found in the bug-check pass.

Each of these was a real defect that would only ever have shown itself at 7am,
which is precisely why it gets a test rather than a careful re-read.
"""
import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

import alarm  # noqa: E402

ok = True

RUN_PY = r"c:\dev\arc-voice-assistant\arc\run.py"


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def ring_now(label=""):
    alarm._save([])
    alarm.set_alarm("7am", "daily", label)
    items = alarm._load()
    items[0]["next_at"] = time.time() - 5
    alarm._save(items)
    alarm.evaluate()


print("BUG: the phone was pushed once per ring, so one missed buzz was the end.")
ring_now("wake up")
check("pushed immediately", len(alarm.pending_push()), 1)
check("not again a moment later", len(alarm.pending_push()), 0)
a = alarm._load()[0]
a["pushed_at"] = time.time() - (alarm.REPUSH_EVERY + 1)
alarm._save([a])
check("but again after the repush interval", len(alarm.pending_push()), 1)
truthy("and keeps going for the whole ring",
       alarm.RING_FOR / alarm.REPUSH_EVERY >= 5)

print("\n  ...which also means a push that FAILED is retried, not lost:")
# push.send() swallows network errors and returns False. With a one-shot flag
# that alarm reached the phone exactly never. Now the next cycle tries again.
a = alarm._load()[0]
a["pushed_at"] = time.time() - (alarm.REPUSH_EVERY + 1)
alarm._save([a])
check("retried", len(alarm.pending_push()), 1)

print("\n  ...and a dismissed alarm stops being pushed at once:")
alarm.dismiss_alarm()
a = alarm._load()[0]
a["pushed_at"] = time.time() - 9999
alarm._save([a])
check("silent", alarm.pending_push(), [])

print("\n  ...a snoozed alarm pushes again when it comes back:")
ring_now()
alarm.pending_push()
alarm.snooze_alarm(1)
a = alarm._load()[0]
a["snooze_at"] = time.time() - 1
alarm._save([a])
alarm.evaluate()
check("push clock reset by the new ring", alarm._load()[0]["pushed_at"], None)
check("so it pushes straight away", len(alarm.pending_push()), 1)
alarm.dismiss_alarm()

print("\nNEW: setting an alarm says how it will actually reach you.")
alarm._save([])
import push  # noqa: E402
said = alarm.set_alarm("7am", "weekdays")
if push.configured():
    truthy("phone is set up -> promises the phone", "phone" in said)
else:
    truthy("phone NOT set up -> says so instead of promising it",
           "aren't set up" in said)
    truthy("and does not claim it will reach the phone",
           "ring here and on your phone" not in said)

print("\nNEW: the next alarm is visible without asking.")
alarm._save([])
check("nothing set -> nothing to show", alarm.next_up(), None)
alarm.set_alarm("7am", "weekdays", "gym")
n = alarm.next_up()
check("time", n["time"], "7am")
check("repeat", n["repeat"], "on weekdays")
check("label", n["label"], "gym")
check("count", n["count"], 1)
truthy("a countdown to show", n["in"])
alarm.set_alarm("9am", "weekends")
n = alarm.next_up()
check("counts them all", n["count"], 2)
soonest = min(alarm._load(), key=lambda x: x["next_at"])
check("and shows the SOONEST", n["time"],
      alarm._clock_words(soonest["hour"], soonest["minute"]))

print("\n  a ringing alarm still reports a next one (the repeat survives):")
ring_now()
truthy("next_up still answers while ringing", alarm.next_up() is not None)
alarm.dismiss_alarm()

print("\nNEW: run.py uses a public entry point, not a private helper.")
truthy("stop() exists", callable(getattr(alarm, "stop", None)))
ring_now()
check("stop() stops", alarm.stop(), 1)
check("and it is silent", alarm.ringing(), [])
ring_now()
check("stop(minutes) snoozes", alarm.stop(snooze_minutes=5), 1)
truthy("with the snooze set", alarm._load()[0]["snooze_at"] > time.time())
alarm._save([])
truthy("run.py no longer reaches into alarm._stop",
       "alarm._stop" not in open(RUN_PY, encoding="utf-8").read())

print("\nThe HUD fixes are actually in the page:")
page = open(HUD, encoding="utf-8").read()
truthy("alarm has its own audio context (it was silent on phones)",
       "function alarmCtx()" in page and "state.alarmAudioCtx" in page)
truthy("and no longer depends on the microphone's one",
       "const ctx = state.audioCtx;\n    if (!ctx) return;" not in page)
truthy("audio is unlocked on first interaction", "unlockAlarmAudio" in page)
truthy("phones vibrate too", "navigator.vibrate" in page)
truthy("polling starts at page load, not at boot", "\n  startAlarmPoll();" in page)
truthy("the beep interval is established before anything that can throw",
       page.index("alarmRing = setInterval") < page.index("try { speak(alarmSaid); }"))
truthy("there is an ALARM readout row", 'id="rAlarm"' in page)
truthy("fed from the poll", "showNextAlarm" in page)

if os.path.exists(alarm.ALARMS_FILE):
    os.remove(alarm.ALARMS_FILE)
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
