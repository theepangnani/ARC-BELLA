# -*- coding: utf-8 -*-
"""Memory that follows you, a brain chosen per question, and a copy that can leave.

Three changes, one theme: things that were quietly per-device, per-person or
per-disk, made to be none of those.

MEMORY was localStorage, so it was never one memory — it was one per device.
Told at the desk, unknown on the phone; corrected on the phone, still wrong at
the desk. That reads as an assistant being scatty rather than as a bug, which
is what made it worth moving.

THE BRAIN was a switch the user had to be smart about. Now the server picks per
question, biased towards the expensive model, because a hard question answered
cheaply is a bad answer and an easy one answered expensively costs a fraction
of a penny.

THE EXPORT closes the one hole self-repair cannot: backups/ is on the same disk
as everything it protects.
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check, prompt_text   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import memory    # noqa: E402
import router    # noqa: E402
import selfheal  # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

# --------------------------------------------------------------------- memory
print("Memory is one memory, not one per device:")
msrc = io.open(ARC / "memory.py", encoding="utf-8").read()
c.truthy("  the bug it fixes is written down", "one per device" in msrc)
c.truthy("  ...and why it reads as scattiness", "scatty" in msrc)
c("  it is not in the page any more", "localStorage.setItem(MEM_KEY" in body, False)
c.truthy("  the page reads it from the server", '"/api/memory"' in body)
c.truthy("  and the prompt block is added server-side", "extra += memory.block()" in
         io.open(ARC / "run.py", encoding="utf-8").read())

memory.use("owner@example.com")
memory.forget("all")
print("\nIt supersedes a restatement, and only a restatement:")
c.truthy("  a rewording replaces",
         memory._supersedes("His sister is Maya", "His sister is called Maya"))
# Both of these would be folded together by a looser threshold, and one true
# fact would be gone for good.
c("  an UPDATE does not — dates settle that instead",
  memory._supersedes("Theepan finished the Python course",
                     "Theepan is learning Python"), False)
c("  and two true facts never eat each other",
  memory._supersedes("Theepan works in Toronto",
                     "Theepan lives in Toronto"), False)
c.truthy("  the reasoning is recorded", "unrecoverable" in msrc)

print("\nFacts carry dates, which is how staleness is settled:")
memory.remember("Theepan is learning Python")
memory.remember("Theepan finished the Python course")
c("  both survive", memory.count(), 2)
blk = memory.block()
c.truthy("  and the prompt is told to weigh them", "the newer one is what is true now" in blk)
c.truthy("  each one dated", "(just now)" in blk)
c.truthy("  an exact repeat is not stored twice",
         "already knew" in memory.remember("Theepan is learning Python"))

print("\nSearch and correction, neither of which was possible before:")
memory.remember("His sister is called Maya")
c.truthy("  search finds by subject", "Maya" in memory.list_memory("sister"))
c.truthy("  a miss says so", "Nothing I know matches" in memory.list_memory("badgers"))
c.truthy("  one wrong fact can go", "Forgotten 1." in memory.forget("maya"))
c("  without taking the rest", memory.count(), 2)
c.truthy("  and all of it can go", "Forgotten all 2." in memory.forget("all"))

print("\nOne person's memory is not another's:")
memory.use("owner@example.com")
memory.remember("The owner's passport number is in the safe")
memory.use("guest@example.com")
c("  a guest sees none of it", memory.count(), 0)
c.truthy("  and cannot search it", "don't know anything" in memory.list_memory())
memory.use("owner@example.com")
c("  the owner still has theirs", memory.count(), 1)

# --------------------------------------------------------------------- router
print("\nThe brain is chosen per question:")
rsrc = io.open(ARC / "router.py", encoding="utf-8").read()
c.truthy("  and it is biased towards the expensive one",
         "BIASED TOWARDS" in rsrc and "not comparable" in rsrc)
c.truthy("  ...rather than asking a model which model to use",
         "costs a call and adds" in rsrc)

EASY = ["what's the time", "stop", "thanks", "play something", "what's the weather",
        "set a timer for 10 minutes", "open spotify", "goodnight"]
HARD = ["why is my code failing", "should I buy nvidia", "compare tesla and apple",
        "explain how caching works", "write me a python script that renames files",
        "what's the weather and then remind me to leave",
        "plan my week around the three deadlines I have coming up"]
for t in EASY:
    got, _ = router.why([{"role": "user", "content": t}])
    c("  cheap  %-46s" % t, got, "fast")
for t in HARD:
    got, _ = router.why([{"role": "user", "content": t}])
    c("  better %-46s" % t, got, "smart")

print("\nAnd it never overrules a person who has chosen:")
for asked in ("smart", "fast"):
    got, _, auto = router.pick(asked, [{"role": "user", "content": "stop"}])
    c("  asked for %-5s, got %-5s" % (asked, got), (got, auto), (asked, False))
c("  an image always gets the better one",
  router.why([{"role": "user", "content": "?"}], has_image=True)[0], "smart")
c("  and nothing to judge is not treated as easy",
  router.why([])[0], "smart")

# --------------------------------------------------------------------- export
print("\nA copy that can leave the machine:")
ssrc = io.open(ARC / "selfheal.py", encoding="utf-8").read()
c.truthy("  the hole it closes is named",
         "same disk as the thing they protect" in ssrc)
(DATA / "notes.json").write_text(json.dumps([{"text": "buy milk"}]), encoding="utf-8")
(DATA / ".env").write_text("ANTHROPIC_API_KEY=sk-must-never-be-exported", encoding="utf-8")
(DATA / "sessions.json").write_text(json.dumps({"sid": {"email": "o@e.com"}}), encoding="utf-8")
(DATA / "token.json").write_text('{"refresh_token": "must-never-be-exported"}', encoding="utf-8")
out = DATA / "exports" / "everything.json"
said = selfheal.export_all(str(out))
c.truthy("  it writes where told", out.exists())
c.truthy("  and says where", "everything.json" in said)
raw = out.read_text(encoding="utf-8")
# The whole reason it is safe to copy to a phone or a stick.
for secret in ("sk-must-never-be-exported", "must-never-be-exported", '"sid"'):
    c("  %-34s is NOT in it" % secret[:34], secret in raw, False)
got = json.loads(raw)
c.truthy("  the notes are", got["files"]["notes.json"] == [{"text": "buy milk"}])
c.truthy("  memory is", "memory.json" in got["files"])
c.truthy("  and it carries what self-repair has done", "repairs" in got)

print("\nA damaged file is reported, not silently dropped:")
(DATA / "notes.json").write_text("]]] wrecked", encoding="utf-8")
said = selfheal.export_all(str(DATA / "exports" / "second.json"))
c.truthy("  it says what it could not take", "Left out, unreadable" in said)
c.truthy("  naming the file", "notes.json" in said)

# ----------------------------------------------------------------- over HTTP
print("\nAll three, through the real app:")
with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "phone")}

    r = client.post("/api/memory/import", cookies=GUEST,
                    json={"facts": ["the guest likes tea"]})
    c("  a guest can import their own", r.json()["imported"], 1)
    c("  and it lands in THEIR memory, not the owner's",
      client.get("/api/memory", cookies=GUEST).json()["count"], 1)
    owner_facts = [f["text"] for f in client.get("/api/memory", cookies=OWNER).json()["facts"]]
    c("  the owner's is untouched by it",
      [f for f in owner_facts if "tea" in f], [])

    r = client.post("/api/memory/import", cookies=GUEST, json={"facts": ["stale"]})
    c("  a second import is refused", r.json()["imported"], 0)
    c.truthy("  saying why", "already has memory" in r.json()["why"])

    c("  export is the owner's alone",
      client.post("/api/export", cookies=GUEST, json={}).status_code, 403)
    c("  and a stranger gets nothing",
      client.post("/api/export", json={}).status_code, 401)

    session.revoke_all()

print("\nThe page offers all three:")
c.truthy("  an export button", 'id="exportBtn"' in page)
c.truthy("  ...that posts to the route", '"/api/export"' in body)
c.truthy("  a three-way brain switch", 'BRAINS = ["auto", "smart", "fast"]' in body)
c.truthy("  defaulting to auto", '__brainSaved || "auto"' in body)
c.truthy("  the readout follows what ANSWERED, not what was asked",
         "__arcLastBrain" in body)
c.truthy("  ...and says why that matters", "a fixed label would be a lie" in body)
c.truthy("  Forget now clears every device", "on every device" in body)
c.truthy("  memory migrates once, and only after the server confirms",
         "migrateMemory" in body and "failed request would lose" in body)

print("\nGated the same way as everything else:")
c.truthy("  reading memory needs no permission", "list_memory" in run.PASSIVE_TOOLS)
c("  forgetting does", "forget" in run.PASSIVE_TOOLS, False)
c("  exporting does", "export_everything" in run.PASSIVE_TOOLS, False)
c("  none of it is a guest's",
  [t for t in ("list_memory", "forget", "export_everything") if t in run.GUEST_TOOLS], [])

c.done()
