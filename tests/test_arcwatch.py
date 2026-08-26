# -*- coding: utf-8 -*-
"""Arc Watch, standing rules, and the account tiers.

Three features, and the through-line is the same in all of them: what the thing
REFUSES to do is the part worth testing.

Arc Watch keeps counts and costs, and must never quietly become a transcript
log — that would be a far more sensitive file than anyone decided to create,
sitting in the same folder with the same protection.

Standing rules notify. They cannot buy, sell, or top up an account, and a rule
that half-did would be worse than none, because you would believe a sale had
happened. "If Tesla hits 200, sell" has to come back as an alert and an honest
sentence, not as a confirmation.

And the tier check exists so "what can this account do" has one answer in one
place, rather than three that drift.
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

PROMPT = prompt_text()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run        # noqa: E402
import session    # noqa: E402
import stats      # noqa: E402
import triggers   # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
watch_page = io.open(ARC / "static" / "watch.html", encoding="utf-8").read()
c = Check()

# ---------------------------------------------------------------- Arc Watch
print("The usage record keeps figures, and only figures:")
src = io.open(ARC / "stats.py", encoding="utf-8").read()
c.truthy("  the refusal is written down", "No prompts, no replies" in src)
c.truthy("  ...with the reason", "conversation log" in src)
c("  no field could hold what was said",
  [k for k in stats._blank() if k in ("text", "reply", "prompt", "messages",
                                      "transcript", "content")], [])
c.truthy("  and the page says so to the user", "No prompts, replies or transcripts" in watch_page)

print("\nIt survives a restart, which the old meter did not:")
stats._days.clear()
stats.record(tok_in=1000, tok_out=100, cache_read=17000, cost=0.02,
             saved=0.0459, tools=["weather", "list_events"],
             model="claude-sonnet-5")
stats.record(tok_in=500, tok_out=60, cost=0.01, saved=0.0, tools=["weather"],
             model="claude-haiku-4-5", error=True)
stats.flush()
c.truthy("  a file is written", (DATA / "usage.json").exists())
stats._days.clear()
stats._loaded = False                      # as if the process had restarted
back = stats.day()
c("  and read back", back["turns"], 2)
c("  with the costs intact", round(back["cost"], 4), 0.03)
c("  and the tools counted", back["tools"]["weather"], 2)
c("  cached tokens kept apart from ordinary input",
  (back["tok_cache_read"], back["tok_in"]), (17000, 1500))
c("  errors counted", back["errors"], 1)

# Turns per model and money per model are different questions, and the answer
# to one does not give you the other: an equal number of turns here, and twice
# the money on one of them.
c("  turns counted per model", back["models"]["claude-sonnet-5"], 1)
c("  and money counted per model too",
  (round(back["spend"]["claude-sonnet-5"], 4),
   round(back["spend"]["claude-haiku-4-5"], 4)), (0.02, 0.01))
c("  what caching saved is recorded, not reconstructed later at a flat rate",
  round(back["saved"], 4), 0.0459)

print("\nEmpty days are present, not skipped:")
# A chart that silently drops the days ARC was off makes a fortnight look like
# a week and a quiet spell look like nothing happened.
s7 = stats.series(7)
c("  seven days for seven days", len(s7), 7)
c("  oldest first", s7[0]["date"] < s7[-1]["date"], True)
c("  the quiet ones are zeros, not gaps", s7[0]["turns"], 0)
c("  today is the last one", s7[-1]["turns"], 2)

print("\nAnd it can say it out loud:")
said = stats.summary(7)
c.truthy("  as a sentence", "turn" in said and "$" in said)
c.truthy("  naming what caching saved", "Caching saved" in said)
c.truthy("  no dict punctuation leaks in", "{" not in said)
c.truthy("  a period with nothing in it says so, rather than $0.00",
         "Nothing recorded" in stats.summary.__doc__ or
         "Nothing recorded" in src)

# ------------------------------------------------------------ standing rules
print("\nA standing rule notifies. It cannot trade:")
tsrc = io.open(ARC / "triggers.py", encoding="utf-8").read()
c.truthy("  the refusal is stated first", "IT CANNOT BUY OR SELL ANYTHING" in tsrc)
c.truthy("  ...with the reason that matters",
         "you would believe a sale had happened" in tsrc)
c.truthy("  and that it is regulated, not merely unbuilt",
         "regulated activity" in tsrc)
c("  the actions are an allow-list", sorted(triggers.ACTIONS),
  ["notify", "push", "remind", "say"])
for banned in ("sell", "buy", "trade", "order", "topup", "run"):
    out = triggers.add_trigger(kind="price", symbol="TSLA", op="below",
                               value=200, action=banned)
    c.truthy("  action %-6s is refused" % banned, "cannot buy or sell" in out)
    c.truthy("  ...and it says what it CAN do", "tell you" in out)

print("\nIt cannot top up the Anthropic account either:")
c.truthy("  said plainly in the module", "IT CANNOT TOP UP AN ANTHROPIC BALANCE" in tsrc)
c.truthy("  and the real answer is named", "Auto-reload" in tsrc)
c.truthy("  the model is told the same", "YOU CANNOT TOP UP THE ANTHROPIC ACCOUNT" in PROMPT)
c.truthy("  and told never to imply an order was placed",
         "NEVER imply an order was placed" in PROMPT)

print("\nSetting, reading back and clearing:")
triggers._save([])
c.truthy("  a price rule sets", "Set." in triggers.add_trigger(
    kind="price", symbol="TSLA", op="below", value=200, note="think about it"))
c.truthy("  a spend rule sets", "Set." in triggers.add_trigger(
    kind="spend", value=5, op="above"))
c.truthy("  a nonsense kind does not",
         "price, a percentage move, or the daily spend" in
         triggers.add_trigger(kind="teleport", value=1))
c.truthy("  a missing symbol is caught",
         "Which stock" in triggers.add_trigger(kind="price", value=200))
c.truthy("  so is a missing number",
         "above zero" in triggers.add_trigger(kind="price", symbol="TSLA", value=0))
listed = triggers.list_triggers()
c.truthy("  both come back", "TSLA" in listed and "spend" in listed)
c.truthy("  with what they will do", "notify" in listed)
c("  clearing by ticker works", "Cleared 1." in triggers.clear_trigger("tsla"), True)
c.truthy("  clearing nothing says so", "Nothing matched" in triggers.clear_trigger("zzz"))
triggers.clear_trigger("all")

print("\nA fired rule does not fire again all afternoon:")
# Without a cooldown, "Tesla below 200" pages the phone every thirty seconds
# until the phone gets switched off — which costs the alert that mattered.
c.truthy("  there is a cooldown", triggers.COOLDOWN >= 600)
c.truthy("  and the reason is on record", "something you switch off" in tsrc)
triggers._save([{"id": "r1", "kind": "spend", "op": "above", "value": 0.0001,
                 "action": "notify", "on": True, "fired_at": 0, "fires": 0}])
stats._days.clear(); stats._loaded = True
stats.record(cost=1.0, tok_in=1, tok_out=1)
triggers.evaluate()
first = triggers.due()
c("  it fires once", len(first), 1)
c.truthy("  saying the actual numbers", "$" in first[0])
c.truthy("  and admitting it cannot fix it", "cannot" in first[0].lower())
triggers.evaluate()
c("  and not again straight away", triggers.due(), [])
c("  the browser is only told once", triggers.due(), [])
triggers._save([])

print("\nA rule is written to one side and moved, like every other data file:")
c.truthy("  atomic write", "os.replace(" in tsrc)
c.truthy("  and it never holds the lock across a network call",
         "OUTSIDE the lock" in tsrc)

# -------------------------------------------------------------------- tiers
print("\nThe owner account is the top of the tree, and says so:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  there is one place that decides", "def tier(request" in run_src)
c.truthy("  the owner has no ceiling", "no ceiling of any kind" in run_src)
c.truthy("  the one gate that remains is named as deliberate",
         "not an oversight" in run_src)
c.truthy("  ...and the threat it is for is stated",
         "stolen session cookie" in run_src)
c.truthy("  with room for a paid tier later", "paid tier will" in run_src)

with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "phone")}

    c("  the owner is reported as owner",
      client.get("/api/session", cookies=OWNER).json().get("tier"), "owner")
    c("  the guest as guest",
      client.get("/api/session", cookies=GUEST).json().get("tier"), "guest")

    print("\nArc Watch is the owner's, over HTTP:")
    c("  the page loads", client.get("/watch", cookies=OWNER).status_code, 200)
    r = client.get("/api/usage?days=7", cookies=OWNER)
    c("  the figures load", r.status_code, 200)
    j = r.json()
    c.truthy("  with a series to draw", len(j["series"]) == 7)
    c.truthy("  and the prices it was costed at", j["prices"]["in"] > 0)
    c("  cached input is priced at a tenth",
      round(j["prices"]["cache_read"] / j["prices"]["in"], 3), 0.1)
    c.truthy("  and every model's own rate is published",
             j["prices"]["per_model"]["claude-opus-5"]["in"] >
             j["prices"]["per_model"]["claude-haiku-4-5"]["in"])

    print("\n  and a guest sees none of it:")
    for path in ("/watch", "/api/usage", "/api/triggers", "/api/triggers/due"):
        c("    %-22s guest" % path, client.get(path, cookies=GUEST).status_code, 403)
        c("    %-22s stranger" % path, client.get(path).status_code, 401)

    print("\nNeither poll holds a session open by itself:")
    # Arc Watch left on a second screen refreshes every minute for ever. If
    # that counted as somebody being present, "signed in until you stop using
    # it" would stop meaning anything.
    for path in ("/api/usage", "/api/triggers/due"):
        c("  %-20s is a background poll" % path, path in run.BACKGROUND_PATHS, True)

    session.revoke_all()

print("\nThe HUD offers it without becoming it:")
c.truthy("  there is a button", 'id="watchStatsBtn"' in page)
c.truthy("  it opens its own tab", 'window.open("/watch"' in body)
c.truthy("  ...and the reason that is right", "leave open on a second" in body)
c.truthy("  standing rules are polled beside the alerts", "pollTriggers();" in body)
c.truthy("  ...on the same timer", "pollReminders(); pollAlerts(); pollTriggers();" in body)

print("\nThe model knows what it may and may not promise:")
c.truthy("  it is told about standing rules", "STANDING RULES" in PROMPT)
c.truthy("  and about the usage report", "usage_report tells you" in PROMPT)
c.truthy("  reading usage needs no permission", "usage_report" in run.PASSIVE_TOOLS)
c.truthy("  reading rules back neither", "list_triggers" in run.PASSIVE_TOOLS)
c("  but SETTING one does", "add_trigger" in run.PASSIVE_TOOLS, False)
c("  and clearing one does", "clear_trigger" in run.PASSIVE_TOOLS, False)
c("  none of it is offered to guests",
  [t for t in ("usage_report", "add_trigger", "list_triggers", "clear_trigger")
   if t in run.GUEST_TOOLS], [])

print("\nSelf-repair protects the new files too:")
# The bug this catches: two new personal data files were added AFTER selfheal
# existed, and it knew about neither — so neither was backed up, checked, or
# repairable. Exactly what selfheal's own default-deny comment warns about.
import selfheal   # noqa: E402
for f in ("triggers.json", "usage.json"):
    c.truthy("  %-14s is watched" % f, f in selfheal.DATA_FILES)
c("  the usage record is checked as a dict, not a list",
  type(selfheal.DATA_FILES["usage.json"][0]), dict)

print("\nNothing waiting for the browser grows without limit:")
# ARC can run for weeks with no tab open. Forty rules firing hourly would
# otherwise build a list nobody ever collects.
c.truthy("  the pending list is capped", triggers.MAX_PENDING <= 100)
triggers._pending[:] = ["x"] * 300
triggers._save([{"id": "r%d" % i, "kind": "spend", "op": "above", "value": 0.0001,
                 "action": "notify", "on": True, "fired_at": 0, "fires": 0}
                for i in range(3)])
triggers.evaluate()
c.truthy("  and stays capped after firing", len(triggers._pending) <= triggers.MAX_PENDING)
c.truthy("  keeping the NEWEST, not the oldest", triggers._pending[-1] != "x")
triggers._save([]); triggers.due()

c.done()
