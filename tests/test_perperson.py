# -*- coding: utf-8 -*-
"""Everybody's alarms, reminders, lists, alerts and rules are their own.

On 11 Sep 2026 guests were lent the alarm clock, the reminders, the to-do list,
the watchlist and standing rules. Each of those was ONE file for the whole
instance, and the only thing that had kept that safe was a line in
test_whose.py: "every tool that touches one of these stores is already outside
GUEST_TOOLS". The loan made the line false and nothing noticed. For a day, a
guest could list and cancel the owner's alarms, a guest's 6am alarm rang in the
owner's tab and buzzed the owner's phone at urgent priority, and "send that to
my phone" from a guest reached the owner.

The owner chose "give everyone their own". What this suite holds:

  · ISOLATION: a guest neither sees nor changes the owner's, in every store,
    through the tools and through the page's own polls.
  · NOTHING STOPS RINGING. The background loop serves no request, and a loop
    that reads a per-person store without saying whose rings the default
    account's alarms and nobody else's — silently. It must ring both.
  · THE PHONE IS THE OWNER'S: only the owner's alarms, reminders, alerts and
    rules are pushed to it, and a guest cannot push at all.
  · OLD DATA STAYS FOUND: a file from before the split is the owner's, and
    stays readable by the owner whichever of the loop or a page touches it
    first — including straight after a restart, before anyone has signed in.
  · SELF-REPAIR does not mistake a split file for a damaged one.
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run        # noqa: E402
import session    # noqa: E402
import whose      # noqa: E402
import alarm      # noqa: E402
import alerts     # noqa: E402
import extras     # noqa: E402
import triggers   # noqa: E402
import push       # noqa: E402
import selfheal   # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()


def wipe():
    for p in (alarm.ALARMS_FILE, alarm.MISSED_FILE, alerts.ALERTS_FILE, triggers.RULES,
              extras.TODO_FILE, extras.REMIND_FILE):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    triggers._pending.clear()


def as_(who, fn, *a, **kw):
    with whose.acting_as(who):
        return fn(*a, **kw)


def ring(who, label, hour="7am"):
    """Set an alarm for `who` and make it due, the way time passing would."""
    with whose.acting_as(who):
        alarm.set_alarm(hour, "daily", label)
        items = alarm._load()
        for a in items:
            if a["label"] == label:
                a["next_at"] = time.time() - 5
        alarm._save(items)


print("The owner is known from the moment ARC starts, not from the first request:")
# The loop reads these stores from boot. Before this, whose.py believed there
# was no owner until somebody opened a page.
c("  owners are set at import", whose.is_owner(OWNER), True)
c("  and a guest is not one", whose.is_owner(GUEST), False)

print("\nTo-dos:")
wipe()
as_(OWNER, extras.add_todo, "owner's milk")
as_(GUEST, extras.add_todo, "guest's bread")
c.truthy("  the owner sees their own", "owner's milk" in as_(OWNER, extras.list_todos))
c("  and not the guest's", "guest's bread" in as_(OWNER, extras.list_todos), False)
c("  the guest sees none of the owner's", "owner's milk" in as_(GUEST, extras.list_todos), False)
c.truthy("  a guest ticking 'milk' finds nothing", "couldn't find" in as_(GUEST, extras.complete_todo, "milk"))
c.truthy("  and the owner's is still there", "owner's milk" in as_(OWNER, extras.list_todos))

print("\nReminders:")
wipe()
as_(OWNER, extras.set_reminder, "owner's call", 1)
as_(GUEST, extras.set_reminder, "guest's call", 1)
c("  a guest cannot list the owner's", "owner's call" in as_(GUEST, extras.list_reminders), False)
c.truthy("  or cancel it", "couldn't find" in as_(GUEST, extras.cancel_reminder, "owner"))
time.sleep(1.2)
pushed = extras.due_for_push()
c("  only the owner's reminder is pushed to the phone", [r["label"] for r in pushed], ["owner's call"])
c("  the guest's tab is told the guest's", [r["label"] for r in as_(GUEST, extras.due_reminders)], ["guest's call"])
c("  and the owner's tab the owner's", [r["label"] for r in as_(OWNER, extras.due_reminders)], ["owner's call"])
c("  neither poll ate the other's", as_(OWNER, extras.due_reminders), [])

print("\nAlarms — and nothing stops ringing:")
wipe()
ring(OWNER, "owner up")
ring(GUEST, "guest up")
alarm.evaluate()
c("  the owner's rings for the owner", [a["label"] for a in as_(OWNER, alarm.ringing)], ["owner up"])
c("  the guest's rings for the guest", [a["label"] for a in as_(GUEST, alarm.ringing)], ["guest up"])
c("  only the owner's goes to the phone", len(alarm.pending_push()), 1)
c.truthy("  ...and it is the owner's", "owner up" in "".join(
    alarm._message(a) for a in as_(OWNER, alarm._load)))
c("  a guest cannot see the owner's in the list", "owner up" in as_(GUEST, alarm.list_alarms), False)
c("  a guest's 'cancel all' leaves the owner's", as_(GUEST, alarm.cancel_alarm, "all").startswith("Cleared"), True)
c("  ...still ringing", len(as_(OWNER, alarm.ringing)), 1)
c("  a guest's Stop stops nothing of the owner's", as_(GUEST, alarm.stop), 0)
c("  the owner's Stop stops the owner's", as_(OWNER, alarm.stop), 1)
ids = [a["id"] for a in whose.flatten(json.loads(io.open(alarm.ALARMS_FILE, encoding="utf-8").read()))]
c("  ids are unique across everybody", len(ids), len(set(ids)))

print("\n  one person's broken alarm does not silence another's:")
wipe()
ring(OWNER, "owner up")
blob = json.loads(io.open(alarm.ALARMS_FILE, encoding="utf-8").read())
blob = {"aaa-broken@example.com": [{"id": "x", "enabled": True, "next_at": 1}], **blob}
io.open(alarm.ALARMS_FILE, "w", encoding="utf-8").write(json.dumps(blob))
try:
    alarm.evaluate()
    raised = False
except Exception:
    raised = True
c.truthy("  the fault is still reported to the loop", raised)
c("  and the owner's alarm rang anyway", len(as_(OWNER, alarm.ringing)), 1)

print("\n  missed alarms are told to the person whose they were:")
wipe()
with whose.acting_as(GUEST):
    alarm.set_alarm("7am", "", "guest flight")
    items = alarm._load()
    items[0]["next_at"] = time.time() - 3 * 3600
    alarm._save(items)
alarm.evaluate()
c("  not to the owner", as_(OWNER, alarm.missed), [])
c("  to the guest", [m["label"] for m in as_(GUEST, alarm.missed)], ["guest flight"])
c("  once", as_(GUEST, alarm.missed), [])

print("\nPrice alerts:")
wipe()
real_quote = extras.yahoo_quote
extras.yahoo_quote = lambda sym: {"price": 250.0, "pct": 1.0, "name": sym}
try:
    for who in (OWNER, GUEST):
        with whose.acting_as(who):
            alerts._save(alerts._load() + [{
                "id": alerts._next_id(), "symbol": "TSLA", "direction": "above", "target": 200.0,
                "note": who.split("@")[0], "triggered": False, "pushed": False, "delivered": False}])
    c("  a guest cannot list the owner's", "owner" in as_(GUEST, alerts.list_price_alerts), False)
    alerts.evaluate()
    c("  both crossed, each in their own slice",
      sorted(a["note"] for a in whose.flatten(alerts._read()) if a["triggered"]), ["guest", "owner"])
    c("  only the owner's goes to the phone", [m.split()[-1] for m in alerts.pending_push()], ["owner"])
    c("  the guest's tab hears the guest's", [m.split()[-1] for m in as_(GUEST, alerts.pending_browser)], ["guest"])
finally:
    extras.yahoo_quote = real_quote

print("\nStanding rules:")
wipe()
sent = []
real_send, real_conf = push.send, push.configured
push.send = lambda *a, **k: sent.append(a[0]) or True
push.configured = lambda: True
extras.yahoo_quote = lambda sym: {"price": 150.0, "pct": -5.0, "name": sym}
try:
    as_(OWNER, triggers.add_trigger, kind="price", symbol="TSLA", op="below", value=200,
        action="push", note="owner's rule")
    as_(GUEST, triggers.add_trigger, kind="price", symbol="TSLA", op="below", value=200,
        action="push", note="guest's rule")
    c.truthy("  a guest cannot set a rule on the owner's spending",
             "Only the owner" in as_(GUEST, triggers.add_trigger, kind="spend", value=5))
    c("  a guest cannot see the owner's rules", "owner's rule" in as_(GUEST, triggers.list_triggers), False)
    triggers.evaluate()
    c("  only the owner's rule reached the phone", [s.endswith("owner's rule") for s in sent], [True])
    # What is said is the condition ("TSLA is 150.00, below your 200.00"), the
    # same for both rules, so this counts: one each, not two in one tab.
    c("  the guest's tab hears the guest's one", len(as_(GUEST, triggers.due)), 1)
    c("  the owner's tab hears the owner's one", len(as_(OWNER, triggers.due)), 1)
    c("  and nothing is left over for either", (as_(GUEST, triggers.due), as_(OWNER, triggers.due)), ([], []))
finally:
    push.send, push.configured = real_send, real_conf
    extras.yahoo_quote = real_quote

print("\nThe phone is the owner's:")
c("  notify_phone is not lent to guests", "notify_phone" in run.guest_tools(), False)
c.truthy("  and refuses a guest even if reached", "guest account" in as_(GUEST, push.notify_phone, "hi"))

print("\nOver HTTP, each tab gets its own:")
wipe()
as_(OWNER, extras.set_reminder, "owner's thing", 0)
as_(GUEST, extras.set_reminder, "guest's thing", 0)
time.sleep(0.1)
with TestClient(run.app) as client:
    O = {run.COOKIE: session.create(OWNER, "browser")}
    G = {run.COOKIE: session.create(GUEST, "browser")}
    g = client.get("/api/reminders/due", cookies=G)
    c("  the guest's poll is answered", g.status_code, 200)
    c("  with the guest's reminder only", [r["label"] for r in g.json()["due"]], ["guest's thing"])
    o = client.get("/api/reminders/due", cookies=O).json()["due"]
    c("  and the owner's is still waiting for the owner", [r["label"] for r in o], ["owner's thing"])
    c("  a stranger is still turned away", client.get("/api/reminders/due").status_code, 401)
    session.revoke_all()

print("\nOld data stays the owner's, whoever touches it first:")
wipe()
LEGACY = [{"id": "old", "fire_at": time.time() - 1, "label": "from before the split",
           "delivered": False}]
io.open(extras.REMIND_FILE, "w", encoding="utf-8").write(json.dumps(LEGACY))
# The loop gets there first, as it would straight after a restart: nobody has
# signed in, so the only thing saying who owns the old pile is run.py's
# set_owners at import.
extras.due_for_push()
on_disk = json.loads(io.open(extras.REMIND_FILE, encoding="utf-8").read())
c("  the loop filed it under the owner's address", sorted(on_disk), [OWNER])
c.truthy("  and the owner still finds it", "from before the split" in json.dumps(as_(OWNER, extras._load_rem)))
c("  a guest inherits none of it", as_(GUEST, extras._load_rem), [])

print("\nThe private Bella, with no sign-in, is addressed as the owner:")


class _Req:
    """A request with no session at all."""
    class url:
        path = "/api/reminders/due"
    cookies = {}
    headers = {}
    query_params = {}
    client = None


saved = run.AUTH_MODE
try:
    run.AUTH_MODE = "open"
    run.apply_session_memory(_Req())
    c("  open mode reads and writes under the owner's address", whose.current(), OWNER)
    run.AUTH_MODE = "google"
    run.apply_session_memory(_Req())
    c("  with sign-in on, no session is NOT promoted to the owner", whose.current(), whose.DEFAULT)
finally:
    run.AUTH_MODE = saved
    whose.use("")

print("\nSelf-repair knows a split file is healthy:")
wipe()
as_(OWNER, extras.add_todo, "owner's milk")
as_(GUEST, extras.add_todo, "guest's bread")
bad = [f for f in selfheal._check_data() if f.get("level") == "broken"]
c("  self_check finds nothing wrong", [f.get("what") or f.get("title") for f in bad], [])
before = io.open(extras.TODO_FILE, encoding="utf-8").read()
selfheal._fix_data()
c("  and self_repair leaves it exactly as it was",
  io.open(extras.TODO_FILE, encoding="utf-8").read(), before)
io.open(extras.TODO_FILE, "w", encoding="utf-8").write(json.dumps({OWNER: "not a list"}))
c.truthy("  but a genuinely wrong one is still caught",
         any(f.get("level") == "broken" for f in selfheal._check_data()))

wipe()
c.done()
