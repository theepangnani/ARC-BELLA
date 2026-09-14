# -*- coding: utf-8 -*-
"""What the 13 Sep 2026 bug check found on the server, held so it stays found.

The worst of it first. Every personal store keeps everybody's items in one
file and saves one person's slice by reading the whole file and writing it
back. The read turned ANY failure into "empty" — and on Windows, reading a file
while another thread replaces it fails routinely. So one person's save could
write everyone else out of the file. Two threads adding reminders as two people
ended with 47 of the owner's 205. storefile.py is the fix; this proves it under
the same load, and proves the smaller findings from the same check:

  · a damaged file is never written over
  · a guest's tab is not held signed in by the OWNER's alarm
  · a standing rule is saved before it acts, so a failed save cannot repeat a push
  · self-repair does not back up, or put back, a file of the wrong shape
  · a failed voice-list refresh is not retried on every call
  · malformed bodies and query values are a 400, not a 500
  · the chart's poll does not hold a session open; tickers are ticker-shaped
"""
import io
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run         # noqa: E402
import session     # noqa: E402
import whose       # noqa: E402
import storefile   # noqa: E402
import extras      # noqa: E402
import notes       # noqa: E402
import alarm       # noqa: E402
import triggers    # noqa: E402
import selfheal    # noqa: E402
import voices      # noqa: E402
import market      # noqa: E402
import panels      # noqa: E402
import tutorial    # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()


def hammer(fn_owner, fn_guest, n=150):
    errors = []

    def go(who, fn):
        with whose.acting_as(who):
            for i in range(n):
                try:
                    fn(i)
                except Exception as e:
                    errors.append(repr(e))

    ts = [threading.Thread(target=go, args=(OWNER, fn_owner)),
          threading.Thread(target=go, args=(GUEST, fn_guest))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return errors


print("Two people saving at once lose nothing — reminders:")
for p in (extras.REMIND_FILE,):
    if p.exists():
        p.unlink()
with whose.acting_as(OWNER):
    for i in range(5):
        extras.set_reminder("owner before %d" % i, seconds=36000)
errs = hammer(lambda i: extras.set_reminder("o%d" % i, seconds=36000),
              lambda i: extras.set_reminder("g%d" % i, seconds=36000))
blob = json.loads(io.open(extras.REMIND_FILE, encoding="utf-8").read())
c("  the owner kept every one (5 + 150)", len(blob.get(OWNER, [])), 155)
c("  the guest kept every one", len(blob.get(GUEST, [])), 150)
c("  and nothing failed", errs[:2], [])

print("\n...and notes, which had the same read:")
if notes.NOTES.exists():
    notes.NOTES.unlink()
errs = hammer(lambda i: notes.add_note("owner %d" % i), lambda i: notes.add_note("guest %d" % i), n=100)
blob = json.loads(io.open(notes.NOTES, encoding="utf-8").read())
c("  both kept all 100", (len(blob.get(OWNER, [])), len(blob.get(GUEST, []))), (100, 100))
c("  nothing failed", errs[:2], [])
left = [f.name for f in DATA.iterdir() if f.name.endswith(".tmp")]
c("  and no temporary file was left behind", left, [])

print("\nA damaged file is never written over:")
io.open(extras.REMIND_FILE, "w", encoding="utf-8").write('{"owner@example.com": [{"id": "1", "fire_at": 1, "labe')
before = io.open(extras.REMIND_FILE, encoding="utf-8").read()
with whose.acting_as(GUEST):
    try:
        extras.set_reminder("guest's", seconds=60)
        raised = False
    except storefile.Unreadable:
        raised = True
c.truthy("  saving into it refuses", raised)
c("  and the damaged file is exactly as it was, for self-repair to deal with",
  io.open(extras.REMIND_FILE, encoding="utf-8").read(), before)
with whose.acting_as(OWNER):
    c("  while reading it shows nothing rather than crashing", extras._load_rem(), [])
extras.REMIND_FILE.unlink()
c("  a file that does not exist is simply empty", storefile.read(extras.REMIND_FILE), [])

print("\nA busy file is waited for, not read as empty:")
target = DATA / "busy.json"
storefile.write(target, {OWNER: [1, 2, 3]})
stop = threading.Event()


def churn():
    while not stop.is_set():
        storefile.write(target, {OWNER: [1, 2, 3]})


t = threading.Thread(target=churn)
t.start()
reads = [storefile.read(target, dict) for _ in range(300)]
stop.set()
t.join()
c("  300 reads during 300-odd replaces all saw the data", sum(1 for r in reads if r == {OWNER: [1, 2, 3]}), 300)

print("\nA file busy for longer than the retries is NOT read as empty (second check):")
with whose.acting_as(OWNER):
    for i in range(5):
        extras.set_reminder("kept %d" % i, seconds=36000)
real_read_text = type(extras.REMIND_FILE).read_text
calls = {"n": 0}


def locked_for_a_while(self, *a, **k):
    # Busy through every retry of the load, then free for the save — the exact
    # window in which the first fix still replaced the slice with one item.
    if self.name == "reminders.json" and calls["n"] < storefile.RETRIES:
        calls["n"] += 1
        raise PermissionError("held by a sync client")
    return real_read_text(self, *a, **k)


real_wait, storefile.WAIT = storefile.WAIT, 0
type(extras.REMIND_FILE).read_text = locked_for_a_while
try:
    with whose.acting_as(OWNER):
        try:
            extras.set_reminder("new", seconds=60)
            refused = False
        except storefile.Busy:
            refused = True
finally:
    type(extras.REMIND_FILE).read_text = real_read_text
    storefile.WAIT = real_wait
c.truthy("  the save is refused rather than guessed at", refused)
with whose.acting_as(OWNER):
    c("  and all five reminders are still there", len(extras._load_rem()), 5)
extras.REMIND_FILE.unlink()

print("\nA file that is not valid text is damage, not a crash:")
import plan   # noqa: E402
io.open(plan.STORE, "wb").write(b'{"owner@example.com": [{"goal": "caf\xe9"}]}')
try:
    with whose.acting_as(OWNER):
        said = plan.as_text()
    crashed = False
except Exception:
    crashed = True
c("  the plan, read every chat turn, does not crash on it", crashed, False)
io.open(alarm.MISSED_FILE, "wb").write(b'{"owner@example.com": [{"time": "7am\xff"}]}')
with whose.acting_as(OWNER):
    c("  missed alarms read as none", alarm.missed(), [])
    try:
        alarm._save_missed([])
        wrote = True
    except storefile.Unreadable:
        wrote = False
c("  and it is not written over", wrote, False)
plan.STORE.unlink()
alarm.MISSED_FILE.unlink()
run_src_now = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  and the alarm poll keeps missed alarms from silencing ringing ones",
         "gone = alarm.missed()\n        except Exception:" in run_src_now.replace("\r\n", "\n"))

print("\nSomebody else's alarm does not keep a guest signed in:")
if alarm.ALARMS_FILE.exists():
    alarm.ALARMS_FILE.unlink()
with whose.acting_as(OWNER):
    alarm.set_alarm("7am", "weekdays", "owner's")
c("  the owner's alarm is armed", alarm.armed_for(OWNER), True)
c("  a guest has none of their own", alarm.armed_for(GUEST), False)
with TestClient(run.app) as client:
    gsid = session.create(GUEST, "browser")
    key = session.key_for(gsid)
    session._sessions[key]["last_seen"] = time.time() - 600
    before = session._sessions[key]["last_seen"]
    client.post("/api/alarms/due", cookies={run.COOKIE: gsid})
    c("  the guest's alarm poll does not refresh their idle clock",
      session._sessions[key]["last_seen"], before)
    osid = session.create(OWNER, "browser")
    okey = session.key_for(osid)
    session._sessions[okey]["last_seen"] = time.time() - 600
    before = session._sessions[okey]["last_seen"]
    client.post("/api/alarms/due", cookies={run.COOKIE: osid})
    c.truthy("  the owner's does, because it is the owner's alarm",
             session._sessions[okey]["last_seen"] > before)

    print("\nMalformed input is a 400, not a 500:")
    O = {run.COOKIE: osid}
    for body in ("[1, 2]", '"x"', "5"):
        c("  POST /api/tutorial %-8s" % body,
          client.post("/api/tutorial", cookies=O, content=body,
                      headers={"Content-Type": "application/json"}).status_code, 400)
    c("  POST /api/tts with a number for text",
      client.post("/api/tts", cookies=O, json={"text": 5, "voice": 7}).status_code in (200, 400, 502), True)
    c("  GET /api/usage?days=abc", client.get("/api/usage?days=abc", cookies=O).status_code, 400)
    session.revoke_all()
alarm.ALARMS_FILE.unlink()

print("\nThe chart's five-minute poll does not hold a session open:")
c("  /api/stock-history is a background path", "/api/stock-history" in run.BACKGROUND_PATHS, True)

print("\nA standing rule is saved before it acts:")
if triggers.RULES.exists():
    triggers.RULES.unlink()
pushed = []
real_send, real_conf, real_quote, real_save = (run.push.send, run.push.configured,
                                               extras.yahoo_quote, triggers._save)
import push   # noqa: E402
push.send = lambda *a, **k: pushed.append(a[0]) or True
push.configured = lambda: True
extras.yahoo_quote = lambda sym: {"price": 150.0, "pct": 0, "name": sym}
try:
    with whose.acting_as(OWNER):
        triggers.add_trigger(kind="price", symbol="TSLA", op="below", value=200, action="push")

    def broken(items):
        raise OSError("disk said no")
    triggers._save = broken
    for _ in range(3):
        try:
            triggers.evaluate()
        except Exception:
            pass
    c("  a rule whose save keeps failing pushes nothing, three cycles running", pushed, [])
    triggers._save = real_save
    triggers.evaluate()
    c("  and once it can save, it pushes once", len(pushed), 1)
    triggers.evaluate()
    c("  and not again inside the cooldown", len(pushed), 1)

    print("\n  ...and a slow push does not hold the lock the browser's poll needs:")
    if triggers.RULES.exists():
        triggers.RULES.unlink()
    with whose.acting_as(OWNER):
        triggers.add_trigger(kind="price", symbol="TSLA", op="below", value=200, action="push")
    waited = {}

    def slow_send(*a, **k):
        t0 = time.time()
        got = triggers._lock.acquire(timeout=0.5)
        waited["free"] = got
        if got:
            triggers._lock.release()
        time.sleep(0.2)
        return True
    push.send = slow_send
    t = threading.Thread(target=triggers.evaluate)
    t.start()
    t.join()
    c("  the lock was free while the push was on its way", waited.get("free"), True)
finally:
    push.send, push.configured, extras.yahoo_quote, triggers._save = real_send, real_conf, real_quote, real_save
    triggers._pending.clear()

print("\nSelf-repair ignores a file of the wrong shape, as a copy and as a source:")
for f in selfheal.BACKUPS.glob("notes.json*") if selfheal.BACKUPS.exists() else []:
    f.unlink()
storefile.write(notes.NOTES, {OWNER: [{"id": "1", "text": "good"}]})
selfheal.snapshot(force=True)
io.open(notes.NOTES, "w", encoding="utf-8").write('"oops"')
c("  a wrong-shaped file is not backed up", "notes.json" in selfheal.snapshot(force=True), False)
io.open(notes.NOTES, "w", encoding="utf-8").write('"oops"')
snap, good = selfheal._restore("notes.json")
c("  and restore walks past to the good copy", good, {OWNER: [{"id": "1", "text": "good"}]})
c.truthy("  every per-person store is now backed up",
         {"plan.json", "panels.json", "tutorial.json", "missed_alarms.json"} <= set(selfheal.DATA_FILES))

print("\nA failed voice-list refresh is not retried on every call:")
calls = {"n": 0}


async def dead():
    calls["n"] += 1
    raise OSError("no network")


real_fetch = voices._fetch
voices._fetch = dead
voices._voices = list(voices.FALLBACK)
voices._fetched_at = time.time() - voices.CACHE_TTL - 10
real_cache = voices._read_cache
voices._read_cache = lambda: (None, 0.0)
try:
    for _ in range(10):
        voices.is_valid("en-GB-SoniaNeural")
    c("  ten lookups, one attempt at the network", calls["n"], 1)
    c.truthy("  and it tries again after a while, not a week",
             voices.RETRY_AFTER <= 15 * 60)
finally:
    voices._fetch, voices._read_cache = real_fetch, real_cache
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the voice is worked out off the event loop",
         "await asyncio.to_thread(_pick_voice, voice, lang)" in run_src)
c.truthy("  and so is the voice list", "await asyncio.to_thread(build)" in run_src)

print("\nTickers are ticker-shaped, and the price cache is bounded:")
c("  <img> is not a ticker", market.series("<IMG SRC=X ONERROR=1>"), None)
c("  nor is a path", market.series("../../v1/secret"), None)
c.truthy("  the cache has a cap", market._SERIES_MAX <= 256)

print("\nA damaged panels or tour record reads as nothing, not a 500:")
storefile.write(panels.PANELS, {OWNER: ["oops", {"title": "ok", "items": ["x", {"label": "a", "value": "b"}]}]})
with whose.acting_as(OWNER):
    got = panels.panels_for_screen()
c("  the one real panel, with its one real row", [(p["title"], len(p["items"])) for p in got], [("ok", 1)])
storefile.write(tutorial.STORE, {OWNER: ["x", {"version": "abc"}]})
with whose.acting_as(OWNER):
    c("  a tour record that is not a number reads as not seen", tutorial.status()["done"], False)

c.done()
