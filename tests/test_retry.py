# -*- coding: utf-8 -*-
"""Trying again, and knowing when not to.

A tool that failed handed the model an error and left it to decide. The model
usually decided to call the tool again -- a round -- and then again. A turn has
sixteen rounds, so a network blip three steps into a six-step job could eat the
lot without the job moving, and what the user heard at the end was that ARC had
run out of room. That sentence names none of the actual problems.

The one that MATTERS MOST here is not retrying. It is not retrying the wrong
thing. A timeout is ambiguous: the request may well have arrived and only the
answer got lost. Repeat a lookup on that basis and the cost is a wasted second.
Repeat tg_send_pending or create_event and somebody gets the message twice, or
finds two of the same meeting in their diary -- and they find out from the other
end, not from ARC. So RETRYABLE is default-deny and half of this file is about
what must never be repeated rather than what may.

The other half is the loop. Retrying inside one call was never what burned the
round budget; nothing telling the model the matter was settled is what burned
it. An error sitting in the transcript reads as an invitation to have another
go. So there is a ledger per turn, and the second time the same call fails the
answer stops being a tool error and becomes an instruction to stop asking.
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import retry     # noqa: E402
import plan      # noqa: E402
import whose     # noqa: E402

c = Check()

print("A hiccup and a refusal are different things:")
for text, want in [
        ("TimeoutError: read timed out", "transient"),
        ("HTTPError: 503 Service Unavailable", "transient"),
        ("ConnectionError: connection reset by peer", "transient"),
        ("HTTPError: 429 Too Many Requests", "transient"),
        ("RemoteDisconnected: closed connection", "transient"),
        ("PermissionError: permission denied", "permanent"),
        ("HTTPError: 404 Not Found", "permanent"),
        ("Not available on a guest account.", "permanent"),
        ("Wrong arguments for read_file: missing path", "permanent"),
        ("FileNotFoundError: no such file", "permanent")]:
    c("  %-42s %s" % (text[:42], want), retry.classify(text, True), want)
c("  and a success is neither", retry.classify("22 degrees", False), "ok")
# Being wrong towards "permanent" costs a round the model spends thinking. Being
# wrong towards "transient" repeats something that may have half-happened.
c("  an unrecognised failure is permanent, which is the safe way to be wrong",
  retry.classify("blorp", True), "permanent")
c("  ...and a 404 that mentions a timeout is still a 404",
  retry.classify("HTTPError: 404 Not Found after timeout", True), "permanent")

print("\nWHAT MAY NEVER BE REPEATED — the part that reaches other people:")
NEVER = ["tg_send_pending", "tg_draft_message", "create_event", "move_event",
         "cancel_event", "run_prepared", "prepare_command", "click",
         "type_text", "add_note", "delete_note", "add_todo", "set_alarm",
         "notify_phone", "self_repair", "plan_set", "plan_step"]
for name in NEVER:
    c("  %-18s is never retried" % name,
      retry.may_retry(name, "TimeoutError: timed out", True), False)
# Default-deny is the property, not the current membership: the list is what
# makes a tool added next year safe rather than retried by inheritance.
c("  a tool nobody has classified is not retried",
  retry.may_retry("some_future_tool", "TimeoutError: x", True), False)
c.truthy("  and the module says why in as many words",
         "sent twice" in io.open(ARC / "retry.py", encoding="utf-8").read())

print("\nWhat may:")
for name in ("weather", "stock", "news", "web_search", "list_events",
             "read_email", "read_file", "screenshot", "directions", "plan_read"):
    c("  %-12s on a hiccup" % name,
      retry.may_retry(name, "TimeoutError: timed out", True), True)
c("  ...but not on a refusal",
  retry.may_retry("read_email", "PermissionError: denied", True), False)
c("  and never when it worked", retry.may_retry("weather", "22 degrees", False), False)
# Everything retryable must be something the consent gate already calls passive.
# A tool worth a consent prompt is a tool that changes something.
#
# This check found a real bug the first time it ran: directions, find_place,
# convert_money and sun_times were missing from PASSIVE_TOOLS, so with the
# ask-first lock on, ARC asked permission to look up a drive time.
c("  everything retryable is passive too",
  retry.RETRYABLE - run.PASSIVE_TOOLS, set())
c("  ...including the four lookups that were not",
  {"directions", "find_place", "convert_money", "sun_times"} <= run.PASSIVE_TOOLS, True)
c("  so asking how long to the airport is not an action",
  run._is_acting("directions"), False)

print("\nThe backoff is short, because somebody is listening to the silence:")
c("  first retry", retry.wait_for(1), 0.4)
c("  second", retry.wait_for(2), 1.2)
c("  and it does not grow without limit", retry.wait_for(9), 1.2)
c.truthy("  the whole budget is under three seconds",
         sum(retry.wait_for(i) for i in range(1, retry.ATTEMPTS)) < 3.0)

print("\nThe ledger, which is what actually saves the rounds:")
t = retry.Turn()
c("  a first failure is just a failure", t.blocked("weather", {"q": "x"}), False)
t.record("weather", {"q": "x"})
c("  ...still, after one", t.blocked("weather", {"q": "x"}), False)
t.record("weather", {"q": "x"})
c("  after two the same call is refused", t.blocked("weather", {"q": "x"}), True)
# read_file a.txt failing says nothing about read_file b.txt.
c("  a different argument is a different call",
  t.blocked("weather", {"q": "y"}), False)
c("  and a different tool is too", t.blocked("stock", {"q": "x"}), False)
c("  argument order does not make a new call",
  retry.key_for("f", {"a": 1, "b": 2}), retry.key_for("f", {"b": 2, "a": 1}))
c.truthy("  unserialisable arguments do not raise", retry.key_for("f", {"a": object()}))

print("\nWhat the model is told, and why it ends the argument:")
msg = retry.giving_up("read_email", 3, "TimeoutError: read timed out")
print("    " + msg[:100] + "...")
c.truthy("  it names the tool", "read_email" in msg)
c.truthy("  says it is settled", "not going to work this turn" in msg)
c.truthy("  says not to call it again", "Do NOT call read_email again" in msg)
c.truthy("  gives it somewhere to put the failure", "plan_step" in msg)
c.truthy("  and tells it to carry on rather than stop", "carry on to the next step" in msg)
c.truthy("  or to tell the user when there is no plan", "tell the user" in msg)
# The one thing that must never happen after a failure.
c.truthy("  and never to claim success", "Never say it worked" in msg)
c.truthy("  the raw error is carried through", "read timed out" in msg)
c.truthy("  a repeat is answered more briefly",
         len(retry.already_said("read_email", "x")) < len(msg))
# The property is that the CAP holds: a 5,000-character error costs exactly
# what a 300-character one does, rather than being pasted into the prompt.
c("  and a huge error is trimmed rather than pasted",
  len(retry.giving_up("f", 1, "e" * 5000)), len(retry.giving_up("f", 1, "e" * 300)))


# --- through the real loop -------------------------------------------------
class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name, args, i):
        self.name, self.input, self.id = name, args, "t%d" % i


class Usage:
    input_tokens = output_tokens = 5
    cache_read_input_tokens = cache_creation_input_tokens = 0


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


# Keyed by tool_use_id, because every request re-sends the conversation so far
# and a naive collection counts the first result once per later round.
sent, results_seen, seen_ids = [], [], set()


def claude_that(fn):
    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                sent.append(kw)
                for m in kw.get("messages", []):
                    if isinstance(m.get("content"), list):
                        for b in m["content"]:
                            if (isinstance(b, dict) and b.get("type") == "tool_result"
                                    and b["tool_use_id"] not in seen_ids):
                                seen_ids.add(b["tool_use_id"])
                                results_seen.append(b)
                return fn(len(sent))

        async def close(self):
            pass
    return Fake()


calls = {"n": 0}


def flaky(name, args):
    """Fails twice with a timeout, then works."""
    calls["n"] += 1
    if calls["n"] < 3:
        return "TimeoutError: read timed out", True
    return "22 degrees and clear", False


def always_down(name, args):
    calls["n"] += 1
    return "TimeoutError: read timed out", True


def refused(name, args):
    calls["n"] += 1
    return "PermissionError: permission denied", True


with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    real_dispatch = run.dispatch_tool

    def ask(tool="weather", rounds=2):
        sent.clear(); results_seen.clear(); seen_ids.clear(); calls["n"] = 0
        n = {"i": 0}

        def brain(i):
            n["i"] += 1
            if n["i"] == 1:
                return Resp([ToolBlk(tool, {"q": "here"}, i)], stop="tool_use")
            return Resp([Blk("Done, sir.")])
        run.app.state.claude = claude_that(brain)
        return client.post("/api/chat", cookies=OWNER, json={
            "messages": [{"role": "user", "content": "go"}],
            "allow_actions": True})

    print("\nA blip is ridden out, and the model never learns it happened:")
    run.dispatch_tool = lambda name, args, **kw: flaky(name, args)
    t0 = time.time()
    r = ask("weather")
    took = time.time() - t0
    c("  the turn succeeds", r.status_code, 200)
    c("  the tool really was called three times", calls["n"], 3)
    c("  ...and the model saw ONE result", len(results_seen), 1)
    c("  which was the successful one", results_seen[0]["is_error"], False)
    c.truthy("  carrying the answer", "22 degrees" in str(results_seen[0]["content"]))
    # 0.4 + 1.2 of real waiting, and it must be awaited rather than slept
    # through -- a blocking sleep here stops the alarm poll too.
    c.truthy("  the backoff was actually waited (>1.5s)", took > 1.5)
    c.truthy("  and the wait is awaited, not blocking",
             "await asyncio.sleep(wait)" in io.open(ARC / "run.py", encoding="utf-8").read())

    print("\nSomething genuinely down is given up on, out loud:")
    run.dispatch_tool = lambda name, args, **kw: always_down(name, args)
    r = ask("weather")
    c("  the turn still answers", r.status_code, 200)
    c("  it tried the full budget", calls["n"], retry.ATTEMPTS)
    c("  and stopped there", calls["n"] <= retry.ATTEMPTS, True)
    said = str(results_seen[0]["content"])
    c("  the result is an error", results_seen[0]["is_error"], True)
    c.truthy("  telling the model to stop", "Do NOT call weather again" in said)
    c.truthy("  and what to do instead", "plan_step" in said)

    print("\nA refusal is not retried even once:")
    run.dispatch_tool = lambda name, args, **kw: refused(name, args)
    r = ask("weather")
    c("  called exactly once", calls["n"], 1)
    c.truthy("  and the error is passed through plainly",
             "permission denied" in str(results_seen[0]["content"]).lower())

    print("\nAnd a tool that must not be repeated never is:")
    run.dispatch_tool = lambda name, args, **kw: always_down(name, args)
    r = ask("tg_send_pending")
    c("  a timeout on send is called ONCE", calls["n"], 1)

    print("\nAsking twice for the same broken thing stops being answered:")
    run.dispatch_tool = lambda name, args, **kw: refused(name, args)
    sent.clear(); results_seen.clear(); seen_ids.clear(); calls["n"] = 0
    n = {"i": 0}

    def stubborn(i):
        # A model that will not take no for an answer: four identical calls.
        n["i"] += 1
        if n["i"] <= 4:
            return Resp([ToolBlk("read_file", {"path": "x"}, i)], stop="tool_use")
        return Resp([Blk("Fine, sir.")])
    run.app.state.claude = claude_that(stubborn)
    r = client.post("/api/chat", cookies=OWNER, json={
        "messages": [{"role": "user", "content": "go"}], "allow_actions": True})
    c("  the turn ends normally", r.status_code, 200)
    # Four asks, but the tool itself is only reached twice -- after that the
    # ledger answers and no work is done at all.
    c("  the tool was only actually run twice", calls["n"], 2)
    c("  the model was answered every time", len(results_seen), 4)
    c.truthy("  the second answer tells it to stop",
             "Do NOT call read_file again" in str(results_seen[1]["content"]))
    c.truthy("  the third is the refusal, not a tool call",
             "already failed this turn" in str(results_seen[2]["content"]))
    c.truthy("  and it still carries the real reason",
             "permission denied" in str(results_seen[2]["content"]).lower())

    run.dispatch_tool = real_dispatch
    session.revoke_all()

print("\nWired in where it can see every tool:")
src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the ledger is per turn, not per process", "tried = retry.Turn()" in src)
c.truthy("  the raw error is what is remembered", "last_error = out" in src)
c.truthy("  and the gate is asked before every retry", "retry.may_retry(" in src)

c.done()
