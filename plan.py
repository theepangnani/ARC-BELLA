#!/usr/bin/env python3
"""ARC's working plan for the job in front of her.

THE PROBLEM THIS EXISTS FOR. A hard request is several steps, and ARC gets a
fixed number of tool rounds to finish them. When she runs out, she has always
said the right thing — "I got through six steps and ran out of room, say carry
on" — and then carried nothing. "Carry on" arrived as an ordinary sentence and
she had to work out again, from the conversation, what she had been doing and
what was left. Sometimes she did. Sometimes she started over, which on a job
that sends messages or moves calendar entries is not a slow answer, it is a
job done twice.

So the plan is written down. Three things follow from that, and only the first
is the obvious one:

  · She can resume. The next step has a number and a name, and neither the
    round limit nor a page reload nor /compact takes it away.
  · YOU can see it. A long task used to be a spinning ring for two minutes
    with no way to tell working from wedged. Now it is four steps with the
    third one lit.
  · She can say she FAILED at something. This is the part worth arguing for.
    A step that can only be done or not-done leaves an assistant that couldn't
    finish step five with two options, and both are bad: pretend, or go quiet
    about it. A blocked step with a reason on it — "no Google connection" — is
    an honest report of a partial job, and partial is what most long jobs
    actually are.

WHAT IT IS NOT. Not the to-do list (todos.json, extras.py) — that is yours, it
outlives the conversation, and nothing here should ever appear on it. Not
memory (memory.py) — that is facts about you. This is scratch paper for one
job, and the next job throws it away.

ONE PLAN AT A TIME, per account. "What are you doing?" has to have one answer.
A stack of half-finished plans is a filing system, and nobody asked for one.

ONE STEP IN PROGRESS AT A TIME, enforced here rather than asked for in the
prompt. Marking a step started quietly stands the previous one down. A model
told to keep a rule mostly keeps it; a rule the store enforces is kept.
"""

import io
import json
import os
import threading
import time
from pathlib import Path

import whose

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
STORE = DATA_DIR / "plan.json"

_lock = threading.RLock()

MAX_STEPS = 20           # more than this is not a plan, it is a wish
MAX_WHAT = 120           # one step, in characters — it gets read aloud
MAX_GOAL = 200

# todo    — not started
# doing   — in progress, and at most one step is ever in this state
# done    — finished
# blocked — tried and could not, with a reason. See the module docstring.
STATES = ("todo", "doing", "done", "blocked")
OPEN = ("todo", "doing")


def connected() -> bool:
    return True


def _raw() -> object:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mine() -> dict:
    """This account's plan, or {} for none.

    Stored as a list of at most one plan rather than as a bare object, because
    that is the shape whose.mine and whose.replace already handle — every other
    store in ARC is a list per account and this one borrows the machinery
    instead of growing a second notion of who is asking.
    """
    got = whose.mine(_raw())
    return got[0] if got and isinstance(got[0], dict) else {}


def _save(p: dict) -> None:
    with _lock:
        try:
            blob = whose.replace(_raw(), [p] if p else [])
            tmp = STORE.with_name(STORE.name + ".tmp")
            tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, STORE)     # cannot land halfway; a plain write can
        except Exception:
            pass


# --- reading ---------------------------------------------------------------

def current() -> dict:
    """The open plan as a plain dict, for the HUD and the prompt."""
    return _mine()


def next_step() -> dict:
    """The step ARC should be on: the one in progress, else the first not done.

    Blocked steps are skipped rather than retried for ever. Something that
    could not be done because Google is disconnected will not go differently
    on the next round, and a plan that keeps re-trying it never reaches step
    five.
    """
    p = _mine()
    for s in p.get("steps") or []:
        if s.get("state") == "doing":
            return s
    for s in p.get("steps") or []:
        if s.get("state") == "todo":
            return s
    return {}


def counts() -> tuple:
    steps = _mine().get("steps") or []
    done = sum(1 for s in steps if s.get("state") == "done")
    blocked = sum(1 for s in steps if s.get("state") == "blocked")
    return len(steps), done, blocked


def _line(s: dict) -> str:
    mark = {"todo": "[ ]", "doing": "[>]", "done": "[x]", "blocked": "[!]"}
    out = "%s %d. %s" % (mark.get(s.get("state"), "[ ]"), s.get("n", 0),
                         s.get("what", ""))
    if s.get("note"):
        out += "  — " + s["note"]
    return out


def as_text() -> str:
    """The plan for the model to read. Empty string when there is none, so it
    can be concatenated into the prompt without a conditional at the call site."""
    p = _mine()
    steps = p.get("steps") or []
    if not steps:
        return ""
    head = "THE PLAN you are part-way through"
    if p.get("goal"):
        head += " — " + p["goal"]
    body = "\n".join(_line(s) for s in steps)
    nxt = next_step()
    tail = ("\nYou are on step %d. Continue from there; do not start again."
            % nxt["n"]) if nxt else \
           "\nEvery step is finished or blocked. Report what happened and stop."
    return head + ":\n" + body + tail


def describe() -> str:
    """The plan SPOKEN. Short — this is read out, not displayed."""
    total, done, blocked = counts()
    if not total:
        return "I'm not part-way through anything, sir."
    nxt = next_step()
    # Built as two finished sentences rather than joined fragments. A single
    # .capitalize() over the lot lowercases everything after the first letter,
    # which turned "On step 2" into "on step 2" halfway through a sentence.
    where = "%d steps, %d done" % (total, done)
    if blocked:
        where += ", %d I couldn't manage" % blocked
    what = ("On step %d: %s" % (nxt["n"], nxt["what"])) if nxt \
        else "That's everything"
    return where + ". " + what + "."


# --- writing ---------------------------------------------------------------

def _result(said: str) -> str:
    """Every write answers with the whole plan, so the model never has to
    remember what it just did or ask to see it again."""
    body = as_text()
    return said + ("\n\n" + body if body else "")


def set_plan(goal: str = "", steps=None) -> str:
    """Start a plan. Replaces any plan already open — see 'one plan at a time'."""
    items = [str(s).strip()[:MAX_WHAT] for s in (steps or []) if str(s).strip()]
    if not items:
        return "A plan needs steps. Give me the list."
    if len(items) < 2:
        # A one-step plan is a task. Writing one down costs a tool round and
        # tells the user nothing they did not already know from asking.
        return ("That's one step, not a plan — just do it. Use a plan when a "
                "job is three or more steps, or when it will take a while.")
    items = items[:MAX_STEPS]
    p = {"goal": str(goal or "").strip()[:MAX_GOAL],
         "started": time.time(),
         "steps": [{"n": i + 1, "what": w, "state": "todo", "note": ""}
                   for i, w in enumerate(items)]}
    _save(p)
    return _result("Plan set, %d steps." % len(items))


def step(step: int = 0, state: str = "", note: str = "") -> str:
    """Move one step along. `state` is doing, done or blocked."""
    st = (state or "").strip().lower()
    if st not in STATES:
        return "A step is %s. Not %r." % (", ".join(STATES), state)
    p = _mine()
    steps = p.get("steps") or []
    if not steps:
        return "There's no plan open. Set one first."
    try:
        n = int(step)
    except (TypeError, ValueError):
        return "Which step number?"
    hit = next((s for s in steps if s.get("n") == n), None)
    if not hit:
        return "There's no step %d — the plan has %d." % (n, len(steps))
    if st == "blocked" and not str(note or "").strip():
        # The whole value of a blocked step is the reason on it. Without one it
        # is a step that silently is not going to happen.
        return "Say why it's blocked, so I can tell the user what stopped."
    # One in progress at a time. Enforced, not requested.
    if st == "doing":
        for s in steps:
            if s.get("state") == "doing" and s is not hit:
                s["state"] = "todo"
    hit["state"] = st
    hit["note"] = str(note or "").strip()[:MAX_WHAT]
    p["steps"] = steps
    _save(p)
    total, done, blocked = len(steps), \
        sum(1 for s in steps if s.get("state") == "done"), \
        sum(1 for s in steps if s.get("state") == "blocked")
    if done + blocked == total:
        said = ("That's the last of it — %d done%s."
                % (done, ", %d blocked" % blocked if blocked else ""))
    else:
        said = "Step %d %s." % (n, st)
    return _result(said)


def clear() -> str:
    """Throw the plan away. The next set_plan does this anyway; this is for
    abandoning a job without starting another."""
    if not _mine():
        return "There's no plan to clear."
    _save({})
    return "Plan cleared."


def read() -> str:
    body = as_text()
    return body or "There's no plan open, sir."


# --- the toolkit interface -------------------------------------------------

TOOLS = [
    {"name": "plan_set",
     "description": (
         "Write down a plan before starting a job that takes several steps — "
         "three or more, or anything that will run for a while. Do this FIRST, "
         "before the work, not after. It is what lets you carry on if you run "
         "out of tool rounds part-way, and it is what the user sees while they "
         "wait. `goal` is the job in a few words. `steps` is the list, in "
         "order, each one short enough to read aloud. Setting a plan replaces "
         "any plan already open. Do NOT use this for the user's own to-do list "
         "— that is add_todo."),
     "input_schema": {"type": "object", "properties": {
         "goal": {"type": "string"},
         "steps": {"type": "array", "items": {"type": "string"}}},
         "required": ["steps"]}},

    {"name": "plan_step",
     "description": (
         "Move the plan along. Mark a step 'doing' when you start it and "
         "'done' when it is finished, as you go — not all at the end. Mark it "
         "'blocked' with a `note` saying why when you tried and could not: a "
         "blocked step with a reason is how the user finds out a long job only "
         "partly worked, and it is better than going quiet. Only one step is "
         "ever in progress; starting one stands the previous one down."),
     "input_schema": {"type": "object", "properties": {
         "step": {"type": "integer", "description": "the step number"},
         "state": {"type": "string",
                   "description": "doing, done or blocked"},
         "note": {"type": "string",
                  "description": "required when blocked: what stopped you"}},
         "required": ["step", "state"]}},

    {"name": "plan_read",
     "description": (
         "Read back the plan you are part-way through. Use when the user asks "
         "what you are doing or how far you have got, or when you are resuming "
         "and need to see where you stopped."),
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "plan_clear",
     "description": (
         "Throw away the current plan, when a job is abandoned or the user "
         "changes their mind. Not needed before starting a new plan."),
     "input_schema": {"type": "object", "properties": {}}},
]

_DISPATCH = {"plan_set": set_plan, "plan_step": step,
             "plan_read": read, "plan_clear": clear}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
