# -*- coding: utf-8 -*-
"""An alarm that fails is worse than no alarm, so the schedule maths is tested
before anything else. The cases that matter are the ones you only discover on
the morning they go wrong: the weekly wrap, the alarm set for a time that has
already passed today, the one that must NOT go off because the server was down
overnight, and a snooze that must not reschedule the alarm itself.
"""
import os
import sys
import time
import datetime as dt


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

import alarm  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def wipe():
    alarm._save([])


def when(ts):
    return dt.datetime.fromtimestamp(ts)


# --------------------------------------------------------------------------
print("Reading a clock time the way people say it:")
for text, want in [
    ("7am", (7, 0)), ("7", (7, 0)), ("07:00", (7, 0)), ("7:30pm", (19, 30)),
    ("19:00", (19, 0)), ("6.45am", (6, 45)), ("12am", (0, 0)), ("12pm", (12, 0)),
    ("midnight", (0, 0)), ("noon", (12, 0)), ("11:59pm", (23, 59)),
    ("2026-08-19T06:30", (6, 30)), ("7 am", (7, 0)), ("7:05 PM", (19, 5)),
]:
    check("%-18s" % ("'" + text + "'"), alarm._parse_clock(text), want)

print("\n...and refusing what isn't one:")
for text in ["", "soon", "25:00", "7:99", "tomorrow", "quarter past"]:
    check("%-18s rejected" % ("'" + text + "'"), alarm._parse_clock(text), None)

# --------------------------------------------------------------------------
print("\nReading which days:")
check("(omitted) -> one-off", alarm._parse_repeat(""), None)
check("'once' -> one-off", alarm._parse_repeat("once"), None)
check("'daily'", alarm._parse_repeat("daily"), alarm._ALL_DAYS)
check("'every day'", alarm._parse_repeat("every day"), alarm._ALL_DAYS)
check("'weekdays'", alarm._parse_repeat("weekdays"), alarm._WEEKDAYS)
check("'every weekday'", alarm._parse_repeat("every weekday"), alarm._WEEKDAYS)
check("'weekends'", alarm._parse_repeat("weekends"), alarm._WEEKENDS)
check("'mon,wed,fri'", alarm._parse_repeat("mon,wed,fri"), ["mon", "wed", "fri"])
check("'Monday and Thursday'", alarm._parse_repeat("Monday and Thursday"), ["mon", "thu"])
check("'tues/thurs'", alarm._parse_repeat("tues/thurs"), ["tue", "thu"])
check("out of order sorts", alarm._parse_repeat("fri, mon"), ["mon", "fri"])
check("nonsense is refused, not guessed", alarm._parse_repeat("bananas"), False)

print("\nSaying it back:")
check("one-off", alarm._repeat_words(None), "once")
check("all seven", alarm._repeat_words(alarm._ALL_DAYS), "every day")
check("weekdays", alarm._repeat_words(alarm._WEEKDAYS), "on weekdays")
check("weekends", alarm._repeat_words(alarm._WEEKENDS), "at weekends")
check("one day", alarm._repeat_words(["wed"]), "every Wednesday")
check("three days", alarm._repeat_words(["mon", "wed", "fri"]),
      "every Monday, Wednesday and Friday")
check("7am", alarm._clock_words(7, 0), "7am")
check("6:30am", alarm._clock_words(6, 30), "6:30am")
check("7:05pm", alarm._clock_words(19, 5), "7:05pm")
check("midnight", alarm._clock_words(0, 0), "12am")
check("noon", alarm._clock_words(12, 0), "12pm")

# --------------------------------------------------------------------------
print("\nWorking out the next occurrence:")
# A fixed Wednesday so the weekday maths is checked against a known day, not
# against whenever the suite happens to run.
WED = dt.datetime(2026, 8, 19, 10, 0, 0).timestamp()
check("that reference day really is a Wednesday", when(WED).weekday(), 2)

nxt = alarm._next_at(7, 0, None, WED)
check("7am already gone today -> tomorrow 7am", when(nxt), dt.datetime(2026, 8, 20, 7, 0))
nxt = alarm._next_at(18, 0, None, WED)
check("6pm still to come -> today", when(nxt), dt.datetime(2026, 8, 19, 18, 0))

nxt = alarm._next_at(7, 0, alarm._WEEKDAYS, WED)
check("weekday 7am from Wed -> Thu", when(nxt), dt.datetime(2026, 8, 20, 7, 0))
FRI_NOON = dt.datetime(2026, 8, 21, 12, 0).timestamp()
nxt = alarm._next_at(7, 0, alarm._WEEKDAYS, FRI_NOON)
check("weekday 7am from Fri noon -> skips the weekend to Mon",
      when(nxt), dt.datetime(2026, 8, 24, 7, 0))
nxt = alarm._next_at(9, 0, alarm._WEEKENDS, WED)
check("weekend 9am from Wed -> Sat", when(nxt), dt.datetime(2026, 8, 22, 9, 0))
nxt = alarm._next_at(9, 0, ["wed"], WED)
check("Wednesdays, this one's gone -> next week",
      when(nxt), dt.datetime(2026, 8, 26, 9, 0))
nxt = alarm._next_at(9, 0, ["wed"], dt.datetime(2026, 8, 19, 8, 0).timestamp())
check("Wednesdays, still to come -> today", when(nxt), dt.datetime(2026, 8, 19, 9, 0))
nxt = alarm._next_at(10, 0, None, WED)
check("exactly now does not count as next", when(nxt), dt.datetime(2026, 8, 20, 10, 0))

# --------------------------------------------------------------------------
print("\nSetting one:")
wipe()
r = alarm.set_alarm("7am", "weekdays", "gym")
truthy("confirms in words", "7am" in r and "weekdays" in r and "gym" in r)
items = alarm._load()
check("one alarm stored", len(items), 1)
check("hour", items[0]["hour"], 7)
check("days", items[0]["days"], alarm._WEEKDAYS)
check("not ringing yet", items[0]["ringing"], False)
truthy("next_at is in the future", items[0]["next_at"] > time.time())

r = alarm.set_alarm("7am", "weekdays")
check("setting the same one again does not duplicate", len(alarm._load()), 1)
truthy("and says so", "already set" in r)
check("label survives a re-set with none given", alarm._load()[0]["label"], "gym")

alarm.set_alarm("7am", "weekends")
check("same time, different days IS a different alarm", len(alarm._load()), 2)

truthy("list reads both back", "2 alarm" in alarm.list_alarms())
truthy("summary line for the model", "ALARMS SET" in alarm.summary_line())

print("\nRefusing what it can't honour:")
wipe()
truthy("no time -> asks", "What time" in alarm.set_alarm(""))
truthy("junk time -> asks", "What time" in alarm.set_alarm("whenever"))
truthy("junk days -> says so", "didn't understand" in alarm.set_alarm("7am", "bananas"))
check("nothing was stored on any of those", alarm._load(), [])

# --------------------------------------------------------------------------
print("\nGoing off:")
wipe()
alarm.set_alarm("7am", "daily", "wake up")
a = alarm._load()[0]
due_was = a["next_at"]
a["next_at"] = time.time() - 5          # its moment just came
alarm._save([a])
alarm.evaluate()
a = alarm._load()[0]
check("it is ringing", a["ringing"], True)
check("and not yet pushed", a["pushed_at"], None)
truthy("rescheduled to the future", a["next_at"] > time.time())
check("rescheduled from the occurrence, not from now",
      a["next_at"], alarm._next_at(7, 0, alarm._ALL_DAYS, (time.time() - 5) + 60))
check("browser poll sees it", len(alarm.ringing()), 1)
truthy("with something to say", "wake up" in alarm.ringing()[0]["message"])

check("phone push offered", len(alarm.pending_push()), 1)
check("not again immediately (REPUSH_EVERY apart, not every cycle)",
      len(alarm.pending_push()), 0)
check("but it is STILL ringing after the push", len(alarm.ringing()), 1)
check("and still ringing after the browser polls, twice", len(alarm.ringing()), 1)

print("\nA daily alarm lands on tomorrow, not today again:")
# The trap this guards: evaluate() runs on a 30s loop, so it always sees the
# alarm a little LATE. Rescheduling from `now` would find today's 7am already
# behind it and be right by luck — but a few seconds EARLY (a clock nudge, a
# fast loop) it would land on today again and ring twice. Rescheduling from
# just past the occurrence itself cannot do that, whichever side of it we are.
OCC = dt.datetime(2026, 8, 19, 7, 0).timestamp()          # a genuine 7am
check("a day later, to the second",
      alarm._next_at(7, 0, alarm._ALL_DAYS, OCC + 60) - OCC, 86400.0)
check("still a day later when seen 20s late",
      alarm._next_at(7, 0, alarm._ALL_DAYS, OCC + 20 + 60) - OCC, 86400.0)
FRI_7AM = dt.datetime(2026, 8, 21, 7, 0).timestamp()
check("a weekday alarm ringing on Friday goes to Monday",
      alarm._next_at(7, 0, alarm._WEEKDAYS, FRI_7AM + 60) - FRI_7AM, 86400.0 * 3)

# --------------------------------------------------------------------------
print("\nSnoozing:")
wipe()
alarm.set_alarm("7am", "weekdays")
a = alarm._load()[0]
scheduled = a["next_at"]
a["next_at"] = time.time() - 5
alarm._save([a])
alarm.evaluate()
after_ring = alarm._load()[0]["next_at"]
truthy("snooze says how long", "9 minutes" in alarm.snooze_alarm())
a = alarm._load()[0]
check("stops ringing", a["ringing"], False)
check("nothing for the browser now", alarm.ringing(), [])
truthy("comes back in ~9 min", 8 * 60 < a["snooze_at"] - time.time() <= 9 * 60)
check("the alarm itself is untouched — still 7am on weekdays",
      (a["next_at"], a["days"]), (after_ring, alarm._WEEKDAYS))

a["snooze_at"] = time.time() - 1
alarm._save([a])
alarm.evaluate()
a = alarm._load()[0]
check("and it does come back", a["ringing"], True)
check("snooze cleared", a["snooze_at"], None)
check("phone is pushed again on the second ring", len(alarm.pending_push()), 1)
check("the push clock was reset by the new ring, not left stale",
      alarm._load()[0]["pushed_at"] is not None, True)

truthy("custom snooze length", "20 minutes" in alarm.snooze_alarm(20))
items = alarm._load()
items[0]["ringing"] = True
alarm._save(items)
truthy("silly lengths clamped", "120 minutes" in alarm.snooze_alarm(9999))

print("\nDismissing:")
wipe()
alarm.set_alarm("6am", "daily")
a = alarm._load()[0]
a["next_at"] = time.time() - 5
alarm._save([a])
alarm.evaluate()
truthy("dismiss confirms", "off" in alarm.dismiss_alarm().lower())
check("silent", alarm.ringing(), [])
check("no snooze pending", alarm._load()[0]["snooze_at"], None)
check("but tomorrow's alarm survives", alarm._load()[0]["enabled"], True)
truthy("nothing ringing -> says so", "Nothing is ringing" in alarm.dismiss_alarm())
truthy("same for snooze", "Nothing is ringing" in alarm.snooze_alarm())

# --------------------------------------------------------------------------
print("\nMissed while ARC was switched off — must NOT ring late:")
wipe()
alarm.set_alarm("7am", "daily")
a = alarm._load()[0]
a["next_at"] = time.time() - (3 * 3600)      # 3 hours ago; ARC was down
alarm._save([a])
alarm.evaluate()
a = alarm._load()[0]
check("does not ring at 10am for a 7am alarm", a["ringing"], False)
check("still set for tomorrow", a["enabled"], True)
truthy("rescheduled forward", a["next_at"] > time.time())

wipe()
alarm.set_alarm("7am")                        # one-off
a = alarm._load()[0]
a["next_at"] = time.time() - (3 * 3600)
alarm._save([a])
alarm.evaluate()
check("a missed one-off is spent, not left lurking", alarm._load(), [])

print("\nJust barely late still rings (the 30s loop, a slow restart):")
wipe()
alarm.set_alarm("7am", "daily")
a = alarm._load()[0]
a["next_at"] = time.time() - 60
alarm._save([a])
alarm.evaluate()
check("a minute late is fine", alarm._load()[0]["ringing"], True)

# --------------------------------------------------------------------------
print("\nRinging into an empty room stops by itself:")
wipe()
alarm.set_alarm("7am", "daily")
a = alarm._load()[0]
a["next_at"] = time.time() - 5
alarm._save([a])
alarm.evaluate()
a = alarm._load()[0]
truthy("stop_at set at ring time", a["stop_at"] and a["stop_at"] > time.time())
a["stop_at"] = time.time() - 1
alarm._save([a])
alarm.evaluate()
check("gives up after RING_FOR", alarm._load()[0]["ringing"], False)
check("and tomorrow's is still set", alarm._load()[0]["enabled"], True)

# --------------------------------------------------------------------------
print("\nCancelling:")
wipe()
alarm.set_alarm("7am", "weekdays", "gym")
alarm.set_alarm("9am", "weekends", "long run")
truthy("by clock time", "7am" in alarm.cancel_alarm("7am"))
check("the other one is untouched", len(alarm._load()), 1)
truthy("by label", "long run" in alarm.cancel_alarm("run"))
check("none left", alarm._load(), [])
truthy("nothing to cancel -> says so", "no alarms" in alarm.cancel_alarm("7am").lower())

alarm.set_alarm("7am", "daily")
alarm.set_alarm("8am", "daily")
truthy("no match -> says so, deletes nothing", "couldn't find" in alarm.cancel_alarm("3pm"))
check("both still there", len(alarm._load()), 2)
truthy("'all' clears", "2 alarm" in alarm.cancel_alarm("all"))
check("empty", alarm._load(), [])

# --------------------------------------------------------------------------
print("\nThe wire format matches how the model will call it:")
names = {t["name"] for t in alarm.TOOLS}
check("five tools", names, {"set_alarm", "list_alarms", "cancel_alarm",
                            "snooze_alarm", "dismiss_alarm"})
check("every tool dispatches", names - set(alarm._DISPATCH), set())
for t in alarm.TOOLS:
    truthy("%-14s has a schema" % t["name"], t["input_schema"]["type"] == "object")
wipe()
out, err = alarm.run_tool("set_alarm", {"time_of_day": "6:30am", "repeat": "weekdays"})
check("run_tool works", err, False)
truthy("and answers in words", "6:30am" in out)
out, err = alarm.run_tool("set_alarm", {"nonsense": 1})
check("bad args are an error, not a crash", err, True)
out, err = alarm.run_tool("no_such_tool", {})
check("unknown tool is an error", err, True)

wipe()
if os.path.exists(alarm.ALARMS_FILE):
    os.remove(alarm.ALARMS_FILE)
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
