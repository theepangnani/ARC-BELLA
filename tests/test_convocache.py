# -*- coding: utf-8 -*-
"""A job at the screen pays for each round once, not once per round after it.

Only the system prompt was cached. Every tool round re-sent every earlier round
at the full input rate: the calls, their results, the screenshots. On 11
September 2026 that was 7.5 million uncached input tokens, about $7.50 of a
$10.62 day. run.cache_mark puts a breakpoint on the newest block, so the next
round reads the rest back at a tenth of the price.

The trap, found before it shipped: fading old check pictures rewrites earlier
rounds, and a rewritten prefix is a miss. Faded every round, the cache would be
written every round and never read, costing MORE than not caching. So they fade
in batches, and this checks the prefix really does hold between them.
"""
import copy
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import run   # noqa: E402
import pc    # noqa: E402

c = Check()
MARK = {"type": "ephemeral"}


def marks(msgs) -> int:
    return sum(1 for m in msgs if isinstance(m.get("content"), list)
               for b in m["content"] if isinstance(b, dict) and "cache_control" in b)


print("A round of results is marked, on a copy:")
convo = [{"role": "user", "content": "open notepad and type hello"}]
# NOT the person's own message. Most turns are one round, and the next turn does
# not share the prefix (the clock, alarms and plan are in the second system
# block), so a mark there was a 1.25x write nothing ever read.
c.truthy("  the person's own message is not marked", run.cache_mark(convo) is convo)
c.truthy("  nor as a block list",
         run.cache_mark([{"role": "user", "content": [{"type": "text", "text": "hi"}]}])[-1]
         ["content"][-1].get("cache_control") is None)

results = [{"type": "tool_result", "tool_use_id": "a", "content": "Opened Notepad."},
           {"type": "tool_result", "tool_use_id": "b", "content": "Typed."}]
convo = convo + [{"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
                 {"role": "user", "content": results}]
sent = run.cache_mark(convo)
c("  a round of results is marked on its last result", sent[-1]["content"][-1].get("cache_control"), MARK)
c("  and only there", marks(sent), 1)
c("  the earlier results are left as they were", "cache_control" in results[-1], False)
c("  so marks never pile up across rounds",
  marks(run.cache_mark(run.cache_mark(convo)[:-1] + [convo[-1]])), 1)

print("\nWhat it leaves alone:")
c("  an empty conversation", run.cache_mark([]), [])
paused = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [object()]}]
c.truthy("  a pause_turn, which ends on the model's own blocks", run.cache_mark(paused) is paused)
blank = [{"role": "user", "content": "   "}]
c.truthy("  a blank message, which has nothing to mark", run.cache_mark(blank) is blank)

src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the tool loop sends the marked copy", "messages=cache_mark(convo)," in src)
# The system prompt holds one breakpoint and this is the second. Four is the
# API's limit, and a third place marking would be the start of hitting it.
c("  and the loop builds no other marks of its own", src.count('cache_control"] = {"type": "ephemeral"}'), 1)


print("\nFading in batches keeps the cached prefix whole between them:")
IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x"}}


def check_result(i):
    return {"type": "tool_result", "tool_use_id": "t%d" % i,
            "content": [{"type": "text", "text": "Typed. CHECK YOUR WORK %d" % i}, dict(IMG)]}


def parallel_round(i):
    """A click, a keypress and a typing call in one round: three pictures."""
    return [check_result(i * 10 + k) for k in range(3)]


def plain(msgs):
    """What the API compares: the content, without the marks."""
    out = json.loads(json.dumps(msgs, default=str))
    for m in out:
        # A string is the same content as one text block holding it.
        if isinstance(m.get("content"), str):
            m["content"] = [{"type": "text", "text": m["content"]}]
        if isinstance(m.get("content"), list):
            for b in m["content"]:
                if isinstance(b, dict):
                    b.pop("cache_control", None)
    return out


convo = [{"role": "user", "content": "fill in the form"}]
held = missed = 0
previous = None
for i in range(1, 17):
    sent = run.cache_mark(convo)
    if previous is not None:
        # Last round's breakpoint covered all of `previous`. A hit needs this
        # round to begin with exactly that.
        if plain(sent[:len(previous)]) == plain(previous):
            held += 1
        else:
            missed += 1
    previous = copy.deepcopy(sent)
    # The same step run.py takes after each round.
    convo = (pc.fade_old_checks(convo) if pc.count_check_rounds(convo) >= run.FADE_CHECKS_OVER
             else convo) + [
        {"role": "assistant", "content": [{"type": "text", "text": "step %d" % i}]},
        {"role": "user", "content": parallel_round(i)},
    ]
# Three pictures a round. Counted by picture, the batch filled every other round.
c.truthy("  most rounds read the whole of the last one back", held >= 12)
c.truthy("  a miss only on the rounds a batch faded", missed <= 16 // run.FADE_CHECKS_OVER)
c.truthy("  and the rounds carrying pictures stay bounded",
         pc.count_check_rounds(convo) <= run.FADE_CHECKS_OVER)
c("  a round of three pictures is one round", pc.count_check_rounds(
    [{"role": "user", "content": parallel_round(1)}]), 1)

# The old way, for the record: fading every round.
convo = [{"role": "user", "content": "fill in the form"}]
old_held, previous = 0, None
for i in range(1, 17):
    sent = run.cache_mark(convo)
    if previous is not None and plain(sent[:len(previous)]) == plain(previous):
        old_held += 1
    previous = copy.deepcopy(sent)
    convo = pc.fade_old_checks(convo) + [
        {"role": "assistant", "content": [{"type": "text", "text": "step %d" % i}]},
        {"role": "user", "content": [check_result(i)]},
    ]
c.truthy("  whereas fading every round would have held almost never", old_held <= 2)

c.done()
