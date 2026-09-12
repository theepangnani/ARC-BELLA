# -*- coding: utf-8 -*-
"""The guests' extra tools are a LOAN, and this is the clock on it.

The owner opened the guest tier up to "all except my pc" and then, the next
day, said it should last a week. That is a different kind of change from the
first one: a wider tier is a decision somebody made and can see, while a wider
tier that was supposed to be temporary and never came back is a decision
nobody made at all. Nothing in the codebase expires on its own, so the risk
here is not that the loan is too generous — it is that in March somebody finds
five relatives still able to read the owner's Telegram because a Friday in
September was busy.

So the expiry is what this suite is about, and the three things worth holding:

  · WHEN THE DATE PASSES, the extra tools stop being offered AND stop being
    dispatchable. Offered-only would leave the model advertising things it
    cannot do; dispatch-only would leave a guest looking at buttons that fail.
  · IT IS DECIDED PER REQUEST, not at import. This process runs for days at a
    stretch — the guardian restarts it on failure, not on a schedule — so an
    expiry read once at boot would be an expiry that never arrives.
  · UNSET OR UNREADABLE MEANS OFF. A date nobody can parse must not read as
    "for ever": a typo in .env should cost a guest their alarms, not hand the
    owner's chats out indefinitely.

The clock is moved here rather than waited for: every check sets the deadline
either side of now and asks what the real gate does.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

import run   # noqa: E402

c = Check()

NOW = time.time()
BASE = run.GUEST_TOOLS
EXTRA = run.GUEST_EXTRA_TOOLS


def with_deadline(stamp):
    """Set the expiry and clear the one-shot 'it lapsed' notice."""
    run.GUEST_EXTRA_UNTIL = stamp
    run._extra_lapsed_said = False


print("The two sets are separate, and the loan is the smaller one:")
c("  no tool is in both", sorted(BASE & EXTRA), [])
c.truthy("  the base tier is the one that survives", len(BASE) == 23)
c.truthy("  and the loan is on top of it", len(EXTRA) == 26)
c("  together they are the widened tier", len(BASE | EXTRA), 49)

print("\nWhile the week is running:")
with_deadline(NOW + 3 * 86400)
live = run.guest_tools()
c("  the wider tier is in force", len(live), 49)
c.truthy("  an alarm may be set", "set_alarm" in live)
c.truthy("  the owner's chats may be read", "tg_read_chat" in live)
out, failed = run.dispatch_tool("set_alarm", {"time": "7am"}, local=False, guest=True)
c("  and dispatch lets it through", "guest account" in out, False)
c.truthy("  it is offered as well as allowed",
         "set_alarm" in {t["name"] for t in run.all_tools(local=False, guest=True)})

print("\nThe moment it runs out — same process, no restart:")
with_deadline(NOW - 1)
gone = run.guest_tools()
c("  the tier is back to what it was", len(gone), 23)
c("  it is exactly the base set, not something new", gone, BASE)
c.truthy("  the public lookups a guest always had are untouched",
         {"weather", "news", "web_search", "directions"} <= gone)
c.truthy("  and so is their own Google account",
         {"list_events", "search_email", "read_drive"} <= gone)
for name in ("set_alarm", "tg_read_chat", "add_todo", "notify_phone",
             "set_price_alert", "add_note", "list_memory", "add_trigger"):
    out, failed = run.dispatch_tool(name, {}, local=False, guest=True)
    c("  %-16s refused again" % name, (failed, "guest account" in out), (True, True))
offered = {t["name"] for t in run.all_tools(local=False, guest=True)}
c("  and none of them is still offered", sorted(offered & EXTRA), [])

print("\nThe owner is not touched by any of it, before or after:")
# Named tools would prove nothing here: a sandbox has no Telegram linked and
# no phone configured, so those toolkits are absent for reasons that have
# nothing to do with the loan. What must hold is that the owner's list does not
# MOVE when the deadline does.
with_deadline(NOW + 86400)
during = {t["name"] for t in run.all_tools(local=True, guest=False)}
with_deadline(NOW - 86400)
after = {t["name"] for t in run.all_tools(local=True, guest=False)}
c("  the owner's tools are the same either side of it", during, after)
c.truthy("  and include what a guest is only lent",
         {"set_alarm", "add_todo", "add_note"} <= during)
out, failed = run.dispatch_tool("set_alarm", {"time": "7am"}, local=True, guest=False)
c("  the owner is never refused as a guest", "guest account" in out, False)

print("\nAn unreadable or missing date means OFF, never for ever:")
c("  unset", run._until_stamp(""), None)
c("  nonsense", run._until_stamp("next tuesday"), None)
c("  half a date", run._until_stamp("2026-13-45"), None)
with_deadline(run._until_stamp(""))
c("  and an unset deadline hands out the base tier", run.guest_tools(), BASE)

print("\nA bare date means the END of that day, not the start:")
day = run._until_stamp("2026-09-19")
c("  it is a real moment", isinstance(day, float), True)
c("  ...late on the 19th", time.strftime("%Y-%m-%d %H", time.localtime(day)),
  "2026-09-19 23")
c("  a written time is taken as written",
  time.strftime("%Y-%m-%d %H:%M", time.localtime(run._until_stamp("2026-09-19T18:00"))),
  "2026-09-19 18:00")
c.truthy("  slashes are forgiven", run._until_stamp("2026/09/19"))

print("\nIt is asked on every request, not settled at import:")
src = open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the gate calls the function", "name not in guest_tools()" in src)
c.truthy("  ...and so does the tool list", 't["name"] in guest_tools()' in src)
c("  neither holds a copy of the set", "GUEST_TOOLS |" in src.split("def guest_tools")[0], False)
c.truthy("  and the reason is written down", "would outlive its own expiry" in src)

print("\nWhen it lapses it says so, once:")
with_deadline(NOW - 1)
import io   # noqa: E402
import contextlib   # noqa: E402
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    for _ in range(5):
        run.guest_tools()
said = buf.getvalue()
c("  said once, not five times", said.count("extra access ran out"), 1)
c.truthy("  naming when it ran out", "Sep" in said or "Jan" in said)
c.truthy("  and what a guest has now", "public lookups" in said)

with_deadline(NOW + 3 * 86400)
c.done()
