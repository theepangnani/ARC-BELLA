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

# ------------------------------------------------------------- the deep brain
print("\nThere is a deeper brain, and it is never reached by accident:")
c.truthy("  the switch offers it", "deep" in run.MODEL_CHOICES)
c.truthy("  it is Opus", "opus" in run.MODEL_CHOICES["deep"])
# The whole reason Auto exists is to spend less. Auto reaching for the dearest
# model on its own would defeat it, so why() must never return "deep" at all.
picks = {router.why([{"role": "user", "content": t}])[0] for t in EASY + HARD}
c("  auto can only ever return smart or fast", picks <= {"smart", "fast"}, True)
c("  ...asked for directly, it is honoured",
  router.pick("deep", [{"role": "user", "content": "stop"}])[:1], ("deep",))
c.truthy("  and the reason is written down", "NEVER CHOSEN AUTOMATICALLY" in
         io.open(ARC / "run.py", encoding="utf-8").read())

# ---------------------------------------------------------------- what it costs
print("\nEach model is costed at its own price, not at the default's:")
c("  haiku, with its date suffix", run.prices_for("claude-haiku-4-5-20251001"), (1.0, 5.0))
c("  sonnet 5", run.prices_for("claude-sonnet-5"), (3.0, 15.0))
c("  opus 5", run.prices_for("claude-opus-5"), (5.0, 25.0))
c("  and opus 4.8 is not read as opus 5", run.prices_for("claude-opus-4-8"), (5.0, 25.0))
# A model ARC has never heard of must not be free. The daily cap is what stops
# a runaway loop draining a card, and a loop costing $0.00 is never capped.
c("  an unknown model falls back to the configured pair, never to zero",
  run.prices_for("claude-something-new-9"), (run.PRICE_IN, run.PRICE_OUT))
c("  ...and to nothing at all for a missing one", run.prices_for(None),
  (run.PRICE_IN, run.PRICE_OUT))

haiku = run.turn_cost("claude-haiku-4-5-20251001", tok_in=10000, tok_out=1000)
sonnet = run.turn_cost("claude-sonnet-5", tok_in=10000, tok_out=1000)
opus = run.turn_cost("claude-opus-5", tok_in=10000, tok_out=1000)
c("  the same turn costs 3x more on sonnet than haiku", round(sonnet / haiku, 2), 3.0)
c("  and 5x more on opus", round(opus / haiku, 2), 5.0)
c.truthy("  a cache read is a tenth of an input token",
         round(run.turn_cost("claude-sonnet-5", cache_read=1_000_000)
               / run.turn_cost("claude-sonnet-5", tok_in=1_000_000), 3) == 0.1)
c.truthy("  the flat-rate mistake is recorded", "three times what it actually cost" in
         io.open(ARC / "run.py", encoding="utf-8").read())

print("\nThe day's spend accumulates per turn, and resets with the day:")


class _Peer:
    host = "127.0.0.1"


class _Req:
    headers = {}
    client = _Peer()


# Left behind, yesterday's spend would be measured against today's cap — and
# once it had crossed once, ARC would refuse to answer every day after.
run._day.update(stamp="1999-01-01", cost=run.DAILY_COST_CAP + 1.0)
try:
    run.check_rate(_Req())
    rolled = True
except Exception:
    rolled = False
c("  yesterday's spend does not lock out today", rolled, True)
c("  ...because the rollover clears it", run._day["cost"], 0.0)
run._day.update(count=0, cost=0.0)

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
c.truthy("  a four-way brain switch", 'BRAINS = ["auto", "smart", "fast", "deep"]' in body)
c.truthy("  defaulting to auto", '__brainSaved || "auto"' in body)
c.truthy("  the readout follows what ANSWERED, not what was asked",
         "__arcLastBrain" in body)
c.truthy("  ...and says why that matters", "the reply wins over the setting" in body)
# A guest who pins Opus is answered by Sonnet. Showing them OPUS-5 anyway would
# be the one thing this readout exists to prevent.
c.truthy("  including when the server overrules a pinned choice",
         "guest who asks for Opus" in body)
c.truthy("  Forget now clears every device", "on every device" in body)
c.truthy("  memory migrates once, and only after the server confirms",
         "migrateMemory" in body and "failed request would lose" in body)

print("\nGated the same way as everything else:")
c.truthy("  reading memory needs no permission", "list_memory" in run.PASSIVE_TOOLS)
c("  forgetting does", "forget" in run.PASSIVE_TOOLS, False)
c("  exporting does", "export_everything" in run.PASSIVE_TOOLS, False)
c("  none of it is a guest's",
  [t for t in ("list_memory", "forget", "export_everything") if t in run.GUEST_TOOLS], [])

print("\nOpus never answers with thinking switched off:")
# Not a preference. With thinking disabled, Opus 5 sometimes writes a tool call
# into its VISIBLE TEXT instead of emitting a tool_use block — the turn succeeds,
# the tool never runs, and a voice assistant reads the call out loud. It can also
# leak internal tags into the reply, which also get spoken. Neither is something
# a rule in the prompt can fix, so the request is what has to change.
OPUS = run.MODEL_CHOICES["deep"]
c("  asked for thinking, Opus thinks", run.thinking_for(OPUS, True)["type"], "adaptive")
c("  NOT asked for it, Opus still thinks", run.thinking_for(OPUS, False)["type"], "adaptive")
c("  a dated opus id is caught too",
  run.thinking_for("claude-opus-4-8-20260101", False)["type"], "adaptive")
# The default exists because thinking is the biggest source of reply latency and
# the voice loop is judged on how fast it starts talking. That stays true on the
# two brains that answer nearly every turn.
c("  Sonnet keeps the fast default",
  run.thinking_for(run.MODEL_CHOICES["smart"], False)["type"], "disabled")
c("  Haiku too", run.thinking_for(run.MODEL_CHOICES["fast"], False)["type"], "disabled")
c("  and both still obey the switch when it is on",
  [run.thinking_for(run.MODEL_CHOICES[b], True)["type"] for b in ("smart", "fast")],
  ["adaptive", "adaptive"])
c.truthy("  the route asks the helper rather than deciding for itself",
         "thinking = thinking_for(model, thinking_on)"
         in io.open(ARC / "run.py", encoding="utf-8").read())

c.done()
