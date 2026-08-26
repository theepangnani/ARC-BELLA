# -*- coding: utf-8 -*-
"""Can ARC repair itself, and does it stop where it should?

Half of this suite is about the repairs working. The other half is about them
NOT working — on the source code, on the secrets, on a file with no backup
behind it. A self-repair that overreaches is worse than none at all, because
its failures arrive wearing the word "fixed".

The most important check in here is the one about a corrupt file with no
snapshot: the honest outcome is an empty file, the damaged one kept, and a
sentence saying so. Silently starting fresh and reporting success would be
data loss with a green tick over it.
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
DATA = sandbox()

import selfheal   # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

NOTES = DATA / "notes.json"
REM = DATA / "reminders.json"
BACKUPS = DATA / "backups"


def write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")


print("It never repairs anything it cannot first copy:")
src = io.open(ARC / "selfheal.py", encoding="utf-8").read()
c.truthy("  a snapshot is taken before a repair can need one", "def snapshot(" in src)
c.truthy("  and the order is stated as the point of the design",
         "snapshots come first and the repairs second" in src)
# The list is deliberately small. Everything not named cannot be restored,
# which is the same default-deny as PASSIVE_TOOLS and GUEST_TOOLS.
c("  it knows exactly which files are its business",
  sorted(selfheal.DATA_FILES),
  ["alarms.json", "notes.json", "price_alerts.json", "reminders.json", "todos.json"])
for secret in ("credentials.json", "credentials_web.json", "token.json", ".env"):
    c.truthy("  %-22s is never copied or rewritten" % secret,
             secret in selfheal.KEEP_OUT and secret not in selfheal.DATA_FILES)
# A live sign-in store restored from an old copy would resurrect sessions
# somebody deliberately revoked. Emptying it is a nuisance; rolling it back is
# a security hole.
c.truthy("  sessions.json is not rolled back, and the reason is recorded",
         "sessions.json" in selfheal.KEEP_OUT
         and "somebody deliberately revoked" in src)

print("\nIt does not touch its own code, and says so:")
c.truthy("  the refusal is written down", "does not edit its own source" in src)
c.truthy("  ...with the reason, not just the rule", "cannot be reviewed" in src)
c.truthy("  no repair writes a .py file",
         not re.search(r"""\.py['"]\s*\)?\s*\.write""", src))
c.truthy("  nothing installs packages", "pip install" not in
         src.split("OPTIONAL = ")[0])
c.truthy("  and that refusal is explained too", "is a supply chain, not a repair" in src)

print("\nA damaged file is restored from the newest copy that reads:")
write(NOTES, [{"text": "buy milk"}, {"text": "ring mum"}])
c("  first snapshot is taken", selfheal.snapshot(), ["notes.json"])
c("  an unchanged file is not copied again", selfheal.snapshot(), [])
NOTES.write_text('[{"text": "buy mi', encoding="utf-8")   # a power cut, in effect
found = [f for f in selfheal.check() if f["id"] == "data"]
c("  the damage is noticed", [f["level"] for f in found], ["broken"])
c.truthy("  and named in English", "damaged" in found[0]["what"])
c.truthy("  it says a copy exists", "good copy" in found[0]["detail"])
done = selfheal.repair(["data"])
c("  the notes come back", json.loads(NOTES.read_text(encoding="utf-8")),
  [{"text": "buy milk"}, {"text": "ring mum"}])
c.truthy("  it reports what it did, with the count", "2 items" in " ".join(done))
c.truthy("  the damaged file is kept, not deleted",
         any("damaged" in p.name for p in BACKUPS.iterdir()))
c("  and nothing is broken afterwards",
  [f["level"] for f in selfheal.check() if f["id"] == "data"], ["ok"])

print("\nTwo saves in the same second are two copies, not one:")
# Second-resolution names collided, and the collision silently replaced the
# older copy — losing one at the only moment copies are worth having.
for i in range(6):
    write(NOTES, [{"text": "note %d" % i}])
    selfheal.snapshot()
c("  six saves leave six copies", len(list(BACKUPS.glob("notes.json.*.bak"))),
  min(6, selfheal.KEEP_SNAPSHOTS))
c.truthy("  and they still sort oldest-last",
         selfheal._restore("notes.json")[1] == [{"text": "note 5"}])

print("\nThe do-not-touch list is a guard, not a comment:")
selfheal.DATA_FILES["sessions.json"] = ([], "sign-ins")     # the mistake, made deliberately
(DATA / "sessions.json").write_text("{}", encoding="utf-8")
try:
    c("  a KEEP_OUT file is refused even from inside DATA_FILES",
      "sessions.json" in selfheal.snapshot(force=True), False)
    c("  and no copy of it exists", list(BACKUPS.glob("sessions.json*")), [])
finally:
    del selfheal.DATA_FILES["sessions.json"]
    (DATA / "sessions.json").unlink()

print("\nA corrupt snapshot is stepped over, not copied back:")
previous = selfheal._restore("notes.json")[1]           # the newest good copy now
write(NOTES, [{"text": "one"}])
selfheal.snapshot()
newest = sorted(BACKUPS.glob("notes.json.*.bak"))[-1]
newest.write_text("{{{ not json", encoding="utf-8")     # the copy is bad too
NOTES.write_text("also not json", encoding="utf-8")
selfheal.repair(["data"])
c("  it walks back to one that parses",
  json.loads(NOTES.read_text(encoding="utf-8")), previous)

print("\nWith no copy at all it starts empty AND says so:")
write(REM, [{"label": "dentist"}])
REM.write_text("]]] broken", encoding="utf-8")           # never snapshotted
done = selfheal.repair(["data"])
c("  the file is valid again", json.loads(REM.read_text(encoding="utf-8")), [])
said = " ".join(done)
c.truthy("  it admits there was no earlier copy", "no earlier copy" in said)
c.truthy("  and points at where the damaged one went", "backups/" in said)
c.truthy("  the damaged reminders file is really there",
         any(p.name.startswith("reminders.json.damaged") for p in BACKUPS.iterdir()))
REM.unlink()

print("\nA file that is merely absent is not a fault:")
# Someone who has never set an alarm has no alarms.json, and being told their
# alarms are broken every morning would be worse than useless.
c("  no alarms file, no complaint",
  [f for f in selfheal.check() if f["id"] == "data" and not f["ok"]], [])

print("\nThe heartbeat is what makes a stopped loop visible:")
c.truthy("  a loop that never ticked reads as starting up",
         selfheal._check_heartbeat()["ok"])
selfheal.beat()
c("  a fresh beat is healthy", selfheal._check_heartbeat()["level"], "ok")
selfheal._beat["at"] = time.time() - (selfheal.STALL_SECONDS + 10)
stopped = selfheal._check_heartbeat()
c("  a stale one is broken, not a warning", stopped["level"], "broken")
c.truthy("  and it says what that costs the user",
         "will not ring" in stopped["detail"])
c("  the repair for it is a restart", stopped["fix"], "heartbeat")

print("\nIt cannot restart a loop it was never given:")
# No hook registered here, so this is the standalone case — and the answer has
# to be "restart ARC", not a claim of success.
out = " ".join(selfheal._fix_heartbeat())
c.truthy("  it says so plainly", "no way to restart" in out)
c.truthy("  and offers what does work", "restarting ARC" in out)

print("\nWith the hook in place it actually restarts:")
called = {"n": 0}
selfheal.register("restart_monitor", lambda: called.__setitem__("n", called["n"] + 1) or True)
out = " ".join(selfheal._fix_heartbeat())
c("  the hook ran", called["n"], 1)
c.truthy("  and it says what is working again", "alarms and reminders" in out)
c("  the stall clock is reset, so it isn't restarted twice",
  selfheal._check_heartbeat()["level"], "ok")

print("\nThe watchdog restarts a stalled loop by itself:")
# This is the one repair that happens with nobody asking, because the thing it
# protects — an alarm at 7am — is a promise, and a promise that fails silently
# is worse than one that fails loudly.
selfheal._beat["at"] = time.time() - (selfheal.STALL_SECONDS + 10)
acted = selfheal.watch()
c("  it acted", called["n"], 2)
c.truthy("  and wrote down that it did", any("watchdog" in l for l in selfheal.history()))
c.truthy("  a healthy loop makes it do nothing at all", selfheal.watch() == [])

print("\nA restart that never takes is given up on — once, not for ever:")
# Otherwise a loop wedged by something a restart cannot reach writes the same
# line to repairs.log every two and a half minutes, hundreds a day, burying the
# one line that says what actually happened.
selfheal._futile[0], selfheal._futile[1] = 0, -1
before, said = called["n"], []
for _ in range(6):
    selfheal._beat["at"] = time.time() - (selfheal.STALL_SECONDS + 10)
    said.append(" ".join(selfheal.watch()))
c("  it tries a bounded number of times", called["n"] - before, selfheal.GIVE_UP_AFTER)
c.truthy("  then says the restart is not the answer",
         any("needs restarting" in s for s in said))
c("  and then stops repeating itself", [s for s in said[4:] if s], [])
selfheal._futile[0], selfheal._futile[1] = 0, -1

print("\nEvery repair is written down:")
c.truthy("  there is a log", selfheal.REPAIR_LOG.exists())
c.truthy("  with the restore in it",
         any("restored your notes" in l for l in selfheal.history(50)))
c.truthy("  and the reason a log exists at all",
         "what happened to my notes?" in src)

print("\nOnly named repairs run — a new one has to be let in deliberately:")
c("  an invented repair does nothing", selfheal.repair(["rm -rf"]),
  ["there is nothing I know how to do about 'rm -rf'"])
c.truthy("  and the default-deny is stated", "default-deny idiom" in src)
c("  the whole list is known", sorted(selfheal.REPAIRS),
  ["automation", "backups", "data", "heartbeat", "logs", "sessions", "voices"])

print("\nIt is honest about what it cannot fix:")
os.environ.pop("ANTHROPIC_API_KEY", None)
no_key = [f for f in selfheal.check() if f["id"] == "claude"][0]
c("  a missing API key is broken", no_key["level"], "broken")
c("  ...and offers no repair, because there isn't one", no_key["fix"], "")
c.truthy("  it says what the user must do", "ANTHROPIC_API_KEY in" in no_key["detail"])
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-a-real-one"
c.truthy("  a present key is not called working", "only an actual request can say"
         in [f for f in selfheal.check() if f["id"] == "claude"][0]["detail"])
c.truthy("  a missing library is reported, never installed",
         "pip install" in src and "No auto-install" in src)

print("\nOne broken probe does not take the health report with it:")
# The whole point of a health check is to answer when things are going wrong,
# which is exactly when a probe is most likely to throw. Letting one failure
# end the report turns "what is wrong with you" into a 500 and no answer at
# all, rather than the nine findings that were perfectly readable.
def _boom():
    raise OSError("disk probe exploded")
_boom.__name__ = "_check_disk"
real = selfheal.CHECKS
selfheal.CHECKS = tuple(_boom if f is selfheal._check_disk else f for f in real)
try:
    out = selfheal.check()
    c.truthy("  the other checks still run", len(out) >= 8)
    own = [f for f in out if f["id"] == "check"]
    c("  the failure is reported as a finding of its own", len(own), 1)
    c.truthy("  naming which probe it was", "_check_disk" in own[0]["detail"])
    c.truthy("  and saying the rest survived", "still ran" in own[0]["detail"])
    c("  it offers no repair, because a broken probe is not a broken ARC",
      own[0]["fix"], "")
finally:
    selfheal.CHECKS = real
c.truthy("  a probe returning nothing is simply skipped",
         selfheal._probe(lambda: None) == [])
c.truthy("  and the reason it is isolated is on record",
         "take the others with it" in src)

print("\nThe spoken report is a sentence, not a table:")
selfheal.beat()
words = selfheal.describe()
c.truthy("  it says something", len(words) > 20)
c.truthy("  no dict punctuation leaks into speech",
         "{" not in words and "'id':" not in words)
write(NOTES, [{"text": "fine"}])
selfheal.snapshot(force=True)
NOTES.write_text("broken again", encoding="utf-8")
words = selfheal.describe()
c.truthy("  a broken thing leads", words.startswith("Broken:"))
c.truthy("  and it offers rather than acts", "if you want" in words or "myself" in words)
selfheal.repair(["data"])

print("\nThe tool surface behaves like every other toolkit:")
c.truthy("  connected()", selfheal.connected())
c("  two tools", sorted(t["name"] for t in selfheal.TOOLS),
  ["self_check", "self_repair"])
for t in selfheal.TOOLS:
    c.truthy("  %-12s has a schema" % t["name"], "input_schema" in t)
out, failed = selfheal.run_tool("self_check", {})
c("  self_check succeeds", failed, False)
c.truthy("  and returns prose", len(out) > 20 and "{" not in out)
out, failed = selfheal.run_tool("self_repair", {"what": "delete everything"})
c("  an unrecognised repair is refused, not guessed", failed, False)
c.truthy("  ...and it says it did nothing", "done nothing" in out)
out, failed = selfheal.run_tool("nonsense", {})
c("  an unknown tool fails", failed, True)

print("\nThe server wires it in, and gates it the same way as everything else:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  selfheal is a toolkit", "import selfheal" in run_src
         and "automation, selfheal)" in run_src)
c.truthy("  self_check needs no permission", '"self_check",' in run_src)
c("  self_repair DOES — it rewrites files", '"self_repair"' in
  run_src.split("PASSIVE_TOOLS = {")[1].split("}")[0], False)
c.truthy("  ...and that distinction is explained", "quietly rewrites your notes" in run_src)
c.truthy("  neither is offered to guests", "self_repair" not in
         run_src.split("GUEST_TOOLS = {")[1].split("}")[0])
c.truthy("  the loop reports its own heartbeat", "selfheal.beat()" in run_src)
c.truthy("  ...and the reason it must", "indistinguishable from a quiet morning" in run_src)
c.truthy("  a watchdog watches it from outside", "_watchdog_loop" in run_src)
c.truthy("  the restart is scheduled onto the loop that owns the task",
         "call_soon_threadsafe(swap)" in run_src)
c.truthy("  ...because creating it inline failed silently off the loop",
         "reported success and did nothing" in run_src)
c.truthy("  ...separate from the loop it supervises, deliberately",
         "living inside the loop that stopped beating" in run_src)
c.truthy("  the boot check runs before anything reads the files",
         "selfheal.startup()" in run_src)
c.truthy("  ...and why boot is the right moment", "nothing is halfway through a write" in run_src)
c.truthy("  there are routes for it",
         '@app.get("/api/selfcheck")' in run_src and '@app.post("/api/selfrepair")' in run_src)
c.truthy("  guests are refused at the route too",
         run_src.split('@app.get("/api/selfcheck")')[1][:1200].count("deny_guest") == 1)
# Both routes ask about Google, and Google is per-session. Without this the
# report describes whichever account the process last touched.
for route in ('@app.get("/api/selfcheck")', '@app.post("/api/selfrepair")'):
    c.truthy("  %s points Google at this browser" % route.split('"')[1],
             "apply_session_google(request)" in run_src.split(route)[1][:1200])
# Refetching the voice list waits up to twenty seconds on a dead network.
# Inline, that is twenty seconds in which the server serves nobody at all.
for route in ('@app.get("/api/selfcheck")', '@app.post("/api/selfrepair")'):
    c.truthy("  %s does not block the event loop" % route.split('"')[1],
             "asyncio.to_thread" in run_src.split(route)[1][:1400])
c.truthy("  ...and the reason is recorded", "A repair must not become an" in run_src)
# The whole reason for a route rather than only a tool.
c.truthy("  and a route exists because the model may BE the fault",
         "exactly what is wrong" in body)

print("\nThe button looks before it touches anything:")
c.truthy("  there is one", 'id="selfHealBtn"' in page)
c.truthy("  it takes two presses", "Two presses, never one" in body)
c.truthy("  the first only reads", '"/api/selfcheck"' in body)
c.truthy("  the second repairs what the first named", '"/api/selfrepair"' in body)
c.truthy("  findings are rendered as text, never as HTML",
         "d.textContent = text;" in body and "never innerHTML" in body)
c.truthy("  an expired session sends you to sign in rather than failing oddly",
         "r.status === 401" in body)
# The button promises the second press does what the first press printed. A
# blank POST asks the server to decide again, which is a different promise:
# anything that appeared between the two presses would be repaired unseen.
c.truthy("  the repair names what was shown, rather than posting a blank",
         "what: asked.join" in body)
c.truthy("  ...and that reasoning is recorded", "never on screen" in body)
# Two in flight can land in either order, and the older reply would paint a
# fault back onto the panel after it had been fixed.
c.truthy("  a second press mid-request is ignored", "if (healBusy) return;" in body)
c.truthy("  ...and the flag is always cleared", "healBusy = false;" in body)
c.truthy("  the model is told to check before it offers a restart",
         "YOU CAN CHECK AND REPAIR YOURSELF" in page)
c.truthy("  ...and told what it must not claim to fix",
         "WHAT SELF-REPAIR CANNOT TOUCH" in page)
c.truthy("  including its own code", "you never edit it, and never offer to" in page)

print("\nThe writes that caused all this are atomic now:")
# notes/todos/reminders truncated first and filled second, so an interruption
# left half a file — which every loader reads as "empty", and the next save
# then overwrote. The three atomic writers were already doing it correctly.
for mod in ("notes.py", "extras.py", "alerts.py", "alarm.py"):
    s = io.open(ARC / mod, encoding="utf-8").read()
    c.truthy("  %-11s writes to one side and moves it" % mod, "os.replace(" in s)
c.truthy("  and the failure it prevents is on record",
         "leaves half a file" in io.open(ARC / "extras.py", encoding="utf-8").read())

print("\nHealth checks do not become the fault they look for:")
c.truthy("  the voice check never goes to the network",
         "voices.loaded()" in src and "voices.catalogue()" not in
         src.split("def _check_voices")[1].split("def ")[0])
c.truthy("  ...and voices.py offers a way to ask without fetching",
         "def loaded(" in io.open(ARC / "voices.py", encoding="utf-8").read())

print("\nAnd all of that through the real app, over HTTP:")
# Everything above proves the functions. This proves the WIRING — that the
# owner's actual request reaches the repair and the guest's identical one does
# not. They are different claims, and only this one is about what ships.
write(NOTES, [{"text": "buy milk"}, {"text": "ring mum"}])
from starlette.testclient import TestClient   # noqa: E402
import run                                    # noqa: E402
import session                                # noqa: E402

with TestClient(run.app) as client:           # the context manager runs lifespan
    OWNER = {run.COOKIE: session.create("owner@example.com", "t")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "t")}

    c.truthy("  boot took a copy before serving anything",
             any("startup: backed up" in l for l in selfheal.history(50)))
    c.truthy("  the loop is beating in the real app", selfheal.beat_age() >= 0)

    r = client.get("/api/selfcheck", cookies=OWNER)
    c("  the owner gets a report", r.status_code, 200)
    c("  with nothing broken on a healthy install", r.json()["broken"], 0)
    c.truthy("  and a spoken summary", len(r.json()["summary"]) > 20)

    c("  a guest cannot look", client.get("/api/selfcheck", cookies=GUEST).status_code, 403)
    c("  a guest certainly cannot repair",
      client.post("/api/selfrepair", json={}, cookies=GUEST).status_code, 403)
    c("  and neither can a stranger", client.get("/api/selfcheck").status_code, 401)

    NOTES.write_text('[{"text": "buy mi', encoding="utf-8")     # the power cut again
    j = client.get("/api/selfcheck", cookies=OWNER).json()
    c("  damage shows up over HTTP",
      [f["what"] for f in j["findings"] if f["level"] == "broken"],
      ["Your notes file is damaged"])
    c("  and is offered as fixable", j["fixable"], ["data"])

    r = client.post("/api/selfrepair", json={"what": "data"}, cookies=OWNER)
    c("  the repair goes through", r.status_code, 200)
    c.truthy("  it says what it did", "restored your notes" in " ".join(r.json()["done"]))
    c("  the notes are actually back",
      json.loads(NOTES.read_text(encoding="utf-8")),
      [{"text": "buy milk"}, {"text": "ring mum"}])
    c("  and nothing is left broken", r.json()["still_broken"], [])

    # What the button actually sends: the ids it printed, comma-joined.
    NOTES.write_text("wrecked again", encoding="utf-8")
    r = client.post("/api/selfrepair", json={"what": "data,backups"}, cookies=OWNER)
    c("  a comma-joined list is understood", r.status_code, 200)
    c.truthy("  and both named repairs ran",
             any("restored your notes" in l for l in r.json()["done"]))
    c("  the notes are back again",
      json.loads(NOTES.read_text(encoding="utf-8")),
      [{"text": "buy milk"}, {"text": "ring mum"}])

    # A name that matches no repair must change nothing at all, rather than
    # falling through to "repair everything".
    before = NOTES.read_text(encoding="utf-8")
    r = client.post("/api/selfrepair", json={"what": "reinstall windows"}, cookies=OWNER)
    c("  an unrecognised name repairs nothing", r.json()["done"], [])
    c("  and touches nothing", NOTES.read_text(encoding="utf-8"), before)

    session.revoke_all()

c.done()
