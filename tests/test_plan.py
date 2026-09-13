# -*- coding: utf-8 -*-
"""The plan: what ARC is part-way through, and what that fixes.

A hard job is several steps and there are only so many tool rounds in a turn.
When they ran out ARC said the right thing -- "I got through six steps, say
carry on" -- and then carried nothing. "Carry on" arrived as an ordinary
sentence, and she had to work out again from the conversation what she had been
doing. Sometimes she did. Sometimes she started over, and on a job that sends
messages or moves calendar entries, starting over is not a slow answer, it is
the job done twice.

Four things here are worth a test rather than a reading:

  · ONE STEP IN PROGRESS is enforced by the store, not requested in the prompt.
    A rule a model is asked to keep is mostly kept.
  · A BLOCKED STEP MUST CARRY A REASON. The entire value of being able to say
    "I couldn't" is the "because"; without it, it is a step that silently is
    not going to happen.
  · THE PLAN IS IN THE SECOND SYSTEM BLOCK. The first block is byte-identical
    across requests and cached at a tenth of the rate. Putting something that
    changes every step into it would not break anything visibly -- it would
    quietly stop the cache hitting and roughly triple the bill.
  · RUNNING OUT OF ROOM NAMES THE NEXT STEP. That is the whole point; the count
    was never the useful part.

And one that is a fix rather than a feature: whose.use() was never called.
whose.py was written, notes.py was converted to read it, and nothing ever told
it who was asking -- so whose.current() answered "owner" for everyone. Nothing
leaked, because the guest tool gate refuses notes outright, but the store
underneath was a single pile and had been since the day it was split.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check, system_text   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com,guest@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import plan      # noqa: E402
import prompt    # noqa: E402
import whose     # noqa: E402

c = Check()
# The rulebook now contains the words "THE PLAN" too, in the section telling ARC
# to write one. Only the injected plan's own header distinguishes the two, and a
# test that cannot tell them apart reports the prompt as a plan.
PLAN_HEAD = "THE PLAN you are part-way through"
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

whose.use("owner@example.com")

print("A plan is written before the work, not after:")
c.truthy("  three steps is a plan", "Plan set, 3 steps" in
         plan.set_plan("book the flight", ["find flights", "pick one", "tell Sam"]))
# One step is a task. Writing it down costs a tool round to tell the user what
# they already knew from asking.
c.truthy("  one step is not", "just do it" in plan.set_plan("x", ["only thing"]))
c.truthy("  and no steps at all is refused", "needs steps" in plan.set_plan("x", []))
c("  the plan survived the one-step attempt", plan.counts()[0], 3)

print("\nEvery write hands back the whole plan, so nothing has to be remembered:")
out = plan.step(1, "doing")
c.truthy("  the step is acknowledged", "Step 1 doing" in out)
c.truthy("  ...and the plan comes with it", "THE PLAN you are part-way through" in out)
c.truthy("  with the goal on it", "book the flight" in out)
c.truthy("  and it says where to carry on from", "You are on step 1" in out)

print("\nOne step in progress at a time — enforced here, not asked for:")
plan.step(2, "doing")
states = [s["state"] for s in plan.current()["steps"]]
c("  starting step 2 stood step 1 down", states, ["todo", "doing", "todo"])
c("  and exactly one is in progress", states.count("doing"), 1)

print("\nA blocked step must say why:")
c.truthy("  no reason is refused", "Say why it's blocked" in plan.step(3, "blocked"))
c("  ...and the step is untouched by the refusal",
  plan.current()["steps"][2]["state"], "todo")
c.truthy("  with a reason it takes",
         "Step 3 blocked" in plan.step(3, "blocked", "no Google sign-in"))
c("  and the reason is kept", plan.current()["steps"][2]["note"], "no Google sign-in")
c.truthy("  the plan shows it", "[!] 3." in plan.as_text())

print("\nWhere to carry on from:")
c("  the in-progress step wins", plan.next_step()["n"], 2)
plan.step(2, "done")
plan.step(1, "done")
# A step that failed for want of a Google sign-in will fail the same way next
# round. Retrying it for ever is how a plan never reaches step five.
c("  a blocked step is not offered again", plan.next_step(), {})
c("  counts are right", plan.counts(), (3, 2, 1))
c.truthy("  and it says so out loud",
         plan.describe().startswith("3 steps, 2 done, 1 I couldn't manage"))
c.truthy("  ...as two proper sentences, not one lowercased one",
         ". That's everything." in plan.describe())
c.truthy("  the model is told to stop rather than to continue",
         "Report what happened and stop" in plan.as_text())

print("\nBad input is answered, never raised:")
c.truthy("  an unknown state", "A step is todo, doing" in plan.step(1, "sideways"))
c.truthy("  a step that isn't there", "no step 9" in plan.step(9, "done"))
c.truthy("  and a step number that isn't one", "Which step" in plan.step("two", "done"))

print("\nOne plan at a time, and it can be thrown away:")
plan.set_plan("something else", ["a", "b"])
c("  a new plan replaces the old", plan.counts()[0], 2)
c.truthy("  clearing says so", "Plan cleared" in plan.clear())
c("  and it is gone", plan.current(), {})
c.truthy("  clearing nothing is not an error", "no plan to clear" in plan.clear())
c.truthy("  reading nothing is not either", "no plan open" in plan.read())

print("\nEach account's plan is their own:")
plan.set_plan("the owner's job", ["one", "two"])
whose.use("guest@example.com")
c("  a guest sees none of it", plan.current(), {})
plan.set_plan("the guest's job", ["x", "y", "z"])
c("  and their own is their own", plan.counts()[0], 3)
whose.use("owner@example.com")
c("  the owner's is untouched", plan.counts()[0], 2)
c.truthy("  ...and still theirs", "the owner's job" in plan.as_text())
plan.clear()
whose.use("guest@example.com")
plan.clear()
whose.use("owner@example.com")

print("\nThe fix underneath: run.py actually says who is asking now:")
src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  whose.use is called per request", "whose.use(who)" in src)
c.truthy("  ...next to the one that already was",
         src.index("memory.use(who)") < src.index("whose.use(who)"))
c.truthy("  and the owner list is handed over too", "whose.set_owners(OWNER_EMAILS)" in src)
c.truthy("  which is what notes.py has been reading all along", "whose.mine" in
         io.open(ARC / "notes.py", encoding="utf-8").read())

print("\nWired in, and a guest may keep one:")
c.truthy("  registered as a toolkit", plan in run.TOOLKITS)
c("  all four tools are known",
  {"plan_set", "plan_step", "plan_read", "plan_clear"} <= set(run.TOOL_OWNER), True)
for t in ("plan_set", "plan_step", "plan_read", "plan_clear"):
    c("  a guest may %s" % t, t in run.guest_tools(), True)
    # Gating these would be perverse: the consent prompt would arrive before the
    # work, to ask permission to write down what the work is going to be.
    c("  ...without a consent prompt for %s" % t, t in run.PASSIVE_TOOLS, True)

print("\nThe model is told to use it:")
rule = prompt.base("main")
c.truthy("  the rulebook has a section", "A PLAN, FOR ANYTHING THAT TAKES MORE" in rule)
c.truthy("  saying write it FIRST", "WRITE THE PLAN FIRST" in rule)
c.truthy("  and mark steps as you go", "MARK STEPS AS YOU GO" in rule)
c.truthy("  and what blocked is for", "mark it 'blocked'" in rule)
c.truthy("  and not to redo finished steps", "do not redo finished steps" in rule)
# The one confusion that would put ARC's scratch paper on the user's real list.
c.truthy("  and that it is not the user's to-do list", "not the user's to-do list" in rule)


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name, args, i):
        self.name, self.input, self.id = name, args, "t%d" % i


class Usage:
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


sent = []


def claude_that(fn):
    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                sent.append(kw)
                return fn(len(sent))

        async def close(self):
            pass
    return Fake()


with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "browser")}

    def ask(cookies=OWNER, **extra):
        sent.clear()
        payload = {"messages": [{"role": "user", "content": "hello"}],
                   "allow_actions": False}
        payload.update(extra)
        r = client.post("/api/chat", cookies=cookies, json=payload)
        return r, (sent[-1] if sent else {})

    print("\nThe plan reaches the model — in the SECOND block:")
    run.app.state.claude = claude_that(lambda n: Resp([Blk("Yes, sir.")]))
    r, kw = ask()
    c.truthy("  with no plan open, nothing is added",
             PLAN_HEAD not in system_text(kw))

    client.get("/api/plan", cookies=OWNER)      # sets the address
    whose.use("owner@example.com")
    plan.set_plan("the long job", ["one", "two", "three"])
    plan.step(1, "done")

    r, kw = ask(system="the date is Tuesday")
    c("  the turn is served", r.status_code, 200)
    c.truthy("  the plan is in the prompt", "THE PLAN you are part-way through" in system_text(kw))
    c.truthy("  ...with the goal", "the long job" in system_text(kw))
    # THE cost check. Block 0 is byte-identical across requests and cached at a
    # tenth; something that changes on every step does not belong in it.
    c("  and NOT in the cached block", PLAN_HEAD in kw["system"][0]["text"], False)
    c("  the cached block is still exactly the rulebook",
      kw["system"][0]["text"], prompt.base("main"))
    c.truthy("  it is the server's text, ahead of the page's",
             kw["system"][1]["text"].index(PLAN_HEAD)
             < kw["system"][1]["text"].index("the date is Tuesday"))
    # A client that sends no system text at all must still see the plan.
    r, kw = ask(system="")
    c.truthy("  an empty client prompt still gets it", PLAN_HEAD in system_text(kw))

    print("\nBut not on the background glance, which is a different job:")
    r, kw = ask(prompt="watch", system="", no_tools=True)
    c("  the watch brief carries no plan", PLAN_HEAD in system_text(kw), False)

    print("\nRunning out of room names the next step:")
    # Always ask for a tool, so the loop can only end by exhausting its rounds.
    run.app.state.claude = claude_that(
        lambda n: Resp([ToolBlk("plan_read", {}, n)], stop="tool_use"))
    keep = run.MAX_TOOL_ROUNDS
    run.MAX_TOOL_ROUNDS = 2
    try:
        r, _ = ask()
        said = r.json()["reply"]
        print("    " + said)
        c("  the turn still answers", r.status_code, 200)
        c.truthy("  it says how far it got", said.startswith("1 of 3 done"))
        c.truthy("  and names the next step by number and name",
                 "Next is step 2, two." in said)
        c.truthy("  and how to resume", "Say carry on" in said)
        c("  not the old round count", "ran out of room" in said and "steps and" in said, False)

        # With no plan there is nothing to name, and the old wording is right.
        plan.clear()
        r, _ = ask()
        old = r.json()["reply"]
        print("    " + old)
        c.truthy("  with no plan, the old message still stands",
                 "got through 2 steps" in old)
    finally:
        run.MAX_TOOL_ROUNDS = keep

    print("\nThe routes, and whose plan they answer with:")
    run.app.state.claude = claude_that(lambda n: Resp([Blk("ok")]))
    whose.use("owner@example.com")
    plan.set_plan("owner job", ["a", "b"])
    d = client.get("/api/plan", cookies=OWNER).json()
    c("  the owner sees theirs", d["goal"], "owner job")
    c("  with the counts", (d["total"], d["done"]), (2, 0))
    c.truthy("  and something to say", d["said"])
    g = client.get("/api/plan", cookies=GUEST).json()
    c("  a guest sees their own, which is empty", g["steps"], [])
    c("  and is NOT refused outright", g.get("total"), 0)
    c("  clearing is allowed", client.post("/api/plan/clear",
                                           cookies=OWNER).status_code, 200)
    c("  ...and it worked", client.get("/api/plan", cookies=OWNER).json()["steps"], [])
    session.revoke_all()

print("\nThe card on screen:")
c.truthy("  it exists", 'id="plan"' in page)
c.truthy("  it starts hidden", re.search(r'class="plan" id="plan"[^>]*hidden', page))
c.truthy("  and joins the family that can be dragged and resized",
         '"forecast", "stocks", "agenda", "nowplaying", "plan"' in body)
c.truthy("  sharing their chrome",
         ".forecast, .stocks, .agenda, .nowplaying, .plan, .chart {" in page)
c.truthy("  it hides itself when there is no plan",
         re.search(r"if \(!steps\.length\) \{\s*\n\s*box\.hidden = true", body))
# The model writes these strings.
c("  no innerHTML anywhere in the painter", "innerHTML" in
  body[body.index("function paintPlan"):body.index("async function pollPlan")], False)
c.truthy("  state is in the mark, not only the colour", "PL_MARK = {" in body)
c.truthy("  a blocked step shows its reason", 's.state === "blocked" && s.note' in body)
# Polling it between turns would spend a request to be told nothing, for ever.
c.truthy("  it is followed only while a turn is in flight", "watchPlan(true)" in body)
c.truthy("  ...and settled once when the turn ends", "watchPlan(false)" in body)
c.truthy("  and read once on load, since a plan outlives its page",
         re.search(r"setInterval\(refreshSession, 60000\);\s*\n\s*\n\s*//[^\n]*\n\s*pollPlan\(\);", body))
c.truthy("  /plan is a command", 'plan:    { hint:' in body)

c.done()
