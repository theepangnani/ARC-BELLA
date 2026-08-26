# -*- coding: utf-8 -*-
"""The alarm as the browser actually meets it: over HTTP, with a session.

Two things here are worth more than the rest. First, polling must NOT consume a
ringing alarm — the whole design rests on that, and a unit test on the module
can't prove the route didn't add a consuming wrapper. Second, an armed alarm
has to hold the session open, or the tab is signed out hours before it is
supposed to make a noise and the feature is decorative.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"
os.environ["ARC_SESSION_MAX_HOURS"] = "0"
os.environ["ARC_SESSION_IDLE_MINUTES"] = "30"

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import alarm     # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


client = TestClient(run.app)


def owner():
    sid = session.create("owner@example.com", "browser")
    return sid, {run.COOKIE: sid}


def guest():
    sid = session.create("guest@example.com", "browser")
    return sid, {run.COOKIE: sid}


def ring_now():
    """Put an alarm into the ringing state the way the monitor loop would."""
    alarm._save([])
    alarm.set_alarm("7am", "daily", "wake up")
    items = alarm._load()
    items[0]["next_at"] = time.time() - 5
    alarm._save(items)
    alarm.evaluate()


# --------------------------------------------------------------------------
print("The module is actually plugged in:")
truthy("alarm is a toolkit", alarm in run.TOOLKITS)
for name in ["set_alarm", "list_alarms", "cancel_alarm", "snooze_alarm", "dismiss_alarm"]:
    check("%-14s routes to it" % name, run.TOOL_OWNER.get(name), alarm)
truthy("alarm tools are offered to the owner",
       {"set_alarm", "snooze_alarm"} <= {t["name"] for t in run.all_tools(local=True)})

print("\nThe consent gate splits them the right way:")
check("setting one needs a yes", run._is_acting("set_alarm"), True)
check("cancelling one needs a yes", run._is_acting("cancel_alarm"), True)
check("reading them back does not", run._is_acting("list_alarms"), False)
check("silencing a ringing one does not", run._is_acting("dismiss_alarm"), False)
check("nor does snoozing it", run._is_acting("snooze_alarm"), False)

print("\nAlarms belong to the owner, not to guests:")
for name in ["set_alarm", "list_alarms", "cancel_alarm", "snooze_alarm", "dismiss_alarm"]:
    check("%-14s withheld from guests" % name, name in run.GUEST_TOOLS, False)
    out, err = run.dispatch_tool(name, {}, local=False, guest=True)
    check("%-14s refused at dispatch too" % name, (err, "guest account" in out), (True, True))
truthy("and not in a guest's tool list",
       not any(t["name"].endswith("_alarm") or t["name"] == "list_alarms"
               for t in run.all_tools(local=False, guest=True)))

# --------------------------------------------------------------------------
print("\nOver HTTP, signed in as the owner:")
alarm._save([])
sid, C = owner()
r = client.get("/api/alarms/due", cookies=C)
check("poll works when nothing is set", (r.status_code, r.json()),
      (200, {"ringing": [], "next": None}))

ring_now()
r = client.get("/api/alarms/due", cookies=C)
body = r.json()
check("a ringing alarm is reported", len(body["ringing"]), 1)
truthy("with words to say", "wake up" in body["ringing"][0]["message"])
truthy("and the time on it", body["ringing"][0]["time"] == "7am")

print("\n  ...and polling it does NOT switch it off:")
for i in range(4):
    check("  poll %d still ringing" % (i + 1),
          len(client.get("/api/alarms/due", cookies=C).json()["ringing"]), 1)
check("a reload is not a dismissal",
      len(client.get("/api/alarms/due", cookies={run.COOKIE: sid}).json()["ringing"]), 1)

print("\n  ...and the same poll tells the HUD what is set, so an armed alarm is"
      "\n     visible without asking (it was invisible until it fired):")
alarm._save([])
check("nothing set -> nothing to show",
      client.get("/api/alarms/due", cookies=C).json()["next"], None)
alarm.set_alarm("6:30am", "weekdays", "gym")
nxt = client.get("/api/alarms/due", cookies=C).json()["next"]
check("time", nxt["time"], "6:30am")
check("repeat", nxt["repeat"], "on weekdays")
check("label", nxt["label"], "gym")
truthy("countdown", nxt["in"])
ring_now()

print("\nThe Stop button:")
r = client.post("/api/alarms/dismiss", cookies=C)
check("reports what it stopped", (r.status_code, r.json()["stopped"]), (200, 1))
check("silence", client.get("/api/alarms/due", cookies=C).json()["ringing"], [])
check("pressing it again stops nothing", client.post("/api/alarms/dismiss", cookies=C).json()["stopped"], 0)
check("but tomorrow's alarm is still set", alarm._load()[0]["enabled"], True)

print("\nThe Snooze button:")
ring_now()
r = client.post("/api/alarms/snooze", cookies=C, json={"minutes": 9})
check("reports what it stopped", r.json()["stopped"], 1)
check("silent for now", client.get("/api/alarms/due", cookies=C).json()["ringing"], [])
a = alarm._load()[0]
truthy("due back in ~9 min", 8 * 60 < a["snooze_at"] - time.time() <= 9 * 60)
a["snooze_at"] = time.time() - 1
alarm._save([a])
alarm.evaluate()
check("and it comes back", len(client.get("/api/alarms/due", cookies=C).json()["ringing"]), 1)
client.post("/api/alarms/dismiss", cookies=C)

print("\n  a snooze with no number, and a silly one:")
ring_now()
check("no body at all is fine",
      client.post("/api/alarms/snooze", cookies=C).json()["stopped"], 1)
ring_now()
client.post("/api/alarms/snooze", cookies=C, json={"minutes": 99999})
truthy("clamped to two hours", alarm._load()[0]["snooze_at"] - time.time() <= 120 * 60)
ring_now()
client.post("/api/alarms/snooze", cookies=C, json={"minutes": "nonsense"})
truthy("junk falls back to the default, not a crash",
       alarm._load()[0]["snooze_at"] - time.time() > 8 * 60)
client.post("/api/alarms/dismiss", cookies=C)

# --------------------------------------------------------------------------
print("\nA guest can't see or touch the owner's alarms:")
ring_now()
_, G = guest()
check("cannot poll", client.get("/api/alarms/due", cookies=G).status_code, 403)
check("cannot snooze", client.post("/api/alarms/snooze", cookies=G).status_code, 403)
check("cannot dismiss", client.post("/api/alarms/dismiss", cookies=G).status_code, 403)
check("and it is still ringing for the owner",
      len(client.get("/api/alarms/due", cookies=C).json()["ringing"]), 1)

print("\nNor can a stranger:")
check("no cookie -> 401", client.get("/api/alarms/due").status_code, 401)
check("junk cookie -> 401",
      client.get("/api/alarms/due", cookies={run.COOKIE: "nope"}).status_code, 401)
check("cannot dismiss without one",
      client.post("/api/alarms/dismiss", cookies={run.COOKIE: "nope"}).status_code, 401)
check("still ringing", len(client.get("/api/alarms/due", cookies=C).json()["ringing"]), 1)
client.post("/api/alarms/dismiss", cookies=C)

# --------------------------------------------------------------------------
print("\nAn armed alarm keeps the tab signed in — otherwise it can't ring:")
truthy("the setting is on by default", run.ALARM_KEEPS_SESSION)
alarm._save([])
check("nothing armed", alarm.armed(), False)

sid, C = owner()


def last_seen(s):
    return session._sessions[session.key_for(s)]["last_seen"]


def age(s):
    session._sessions[session.key_for(s)]["last_seen"] = time.time() - 600


age(sid)
before = last_seen(sid)
client.get("/api/alarms/due", cookies=C)
check("with NO alarm set, polling does not hold the session open",
      last_seen(sid), before)

alarm.set_alarm("7am", "daily")
check("now something is armed", alarm.armed(), True)
age(sid)
before = last_seen(sid)
client.get("/api/alarms/due", cookies=C)
truthy("with one set, polling does hold it open", last_seen(sid) > before)

print("\n  ...and only that one poll — the rest still let it time out:")
for path in ["/api/health", "/api/session", "/api/reminders/due", "/api/alerts/due"]:
    age(sid)
    before = last_seen(sid)
    client.get(path, cookies=C)
    check("  %-22s still background" % path, last_seen(sid), before)

print("\n  ...and the hold ends when the alarm does:")
alarm.cancel_alarm("all")
check("nothing armed now", alarm.armed(), False)
age(sid)
before = last_seen(sid)
client.get("/api/alarms/due", cookies=C)
check("session goes back to timing out", last_seen(sid), before)

print("\n  a snoozed alarm still counts as armed (it is about to go off):")
ring_now()
client.post("/api/alarms/snooze", cookies=C)
check("armed", alarm.armed(), True)

# --------------------------------------------------------------------------
print("\nThe HUD is served with the alarm in it:")
alarm._save([])
r = client.get("/", cookies=C)
page = r.text
check("page loads", r.status_code, 200)
for token in ['id="alarmBar"', 'id="alarmSnooze"', 'id="alarmStop"',
              "/api/alarms/due", "/api/alarms/snooze", "/api/alarms/dismiss",
              # was "set_alarm": that word only appeared in the prompt, which
              # the page no longer carries. This proves the same thing — the
              # real alarm client is here, not a stub.
              "startAlarmPoll", "alarmBeep"]:
    truthy("  contains %s" % token, token in page)

session.revoke_all()
alarm._save([])
if os.path.exists(alarm.ALARMS_FILE):
    os.remove(alarm.ALARMS_FILE)
print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
