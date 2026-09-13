# -*- coding: utf-8 -*-
"""Cards the user invents — and the reason they are a form and not code.

The obvious way to build this is to let the model write HTML. It would work
first time and be a hole in the app for ever: everything ARC reads can reach
the model — an email, a Telegram message, a web page, somebody else's calendar
invite — and a model that can put markup in this page can be talked into
putting a script tag in it by anything it reads. The page holds a live session
to the owner's mail, calendar and machine.

So a panel is DATA: a title and up to eight label/value rows, drawn with
createElement and textContent like the plan card. This suite is mostly about
that boundary holding, from three directions:

  · the SERVER strips what should never arrive (control characters, angle
    brackets, over-long strings) and caps how much can arrive;
  · `live` is an ALLOW-LIST — a ticker, a countdown, the clock — checked on the
    way out AND again in the page, because two cheap checks either side of a
    network hop is the right amount of paranoia for a string a model wrote;
  · the PAGE never calls innerHTML in the painter, which is the check that
    actually matters, since everything else is defence in depth behind it.

And panels are per account, like notes and the plan: a guest's card is their
own and the owner's is invisible to them.
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import panels    # noqa: E402
import whose     # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

whose.set_owners({"owner@example.com"})
whose.use("owner@example.com")
panels._save([])

print("Making one from a sentence's worth of detail:")
said = panels.make_panel("Tokyo trip", [
    {"label": "Flight", "value": "BA5 · 11:40"},
    {"label": "Hotel", "value": "Park Hyatt"},
    {"label": "Leaves in", "value": "", "live": "countdown:2026-11-03"},
])
c.truthy("  it says what it did", said.startswith("Put 'Tokyo trip'"))
c.truthy("  ...and that it can be moved", "dragged and resized" in said)
mine = panels._load()
c("  one panel", len(mine), 1)
c("  three rows", len(mine[0]["items"]), 3)
c("  the countdown kept its binding", mine[0]["items"][2].get("live"),
  "countdown:2026-11-03")

print("\n  said a different way, because people do not talk in JSON:")
panels.make_panel("Gym", ["Monday: legs", "Wednesday: back", "Friday: rest"])
rows = [p for p in panels._load() if p["title"] == "Gym"][0]["items"]
c("  'Monday: legs' splits into a pair", (rows[0]["label"], rows[0]["value"]),
  ("Monday", "legs"))
panels.make_panel("Bins", {"Black": "Tuesday", "Green": "Friday"})
rows = [p for p in panels._load() if p["title"] == "Bins"][0]["items"]
c("  a plain object works too", (rows[0]["label"], rows[0]["value"]),
  ("Black", "Tuesday"))
c.truthy("  a line with no colon stays one line",
         panels._tidy(["just a note"])[0]["value"] == "just a note")

print("\nSame title means the SAME panel, updated:")
panels.make_panel("Tokyo trip", [{"label": "Hotel", "value": "Okura"}])
titles = [p["title"] for p in panels._load()]
c("  still one Tokyo trip", titles.count("Tokyo trip"), 1)
c("  with the new contents",
  [p for p in panels._load() if p["title"] == "Tokyo trip"][0]["items"][0]["value"], "Okura")
c.truthy("  and it says it updated rather than made",
         panels.make_panel("Tokyo trip", [{"label": "Hotel", "value": "Okura"}])
         .startswith("Updated"))

print("\nWhat must never get through:")
nasty = panels.make_panel("<script>alert(1)</script>", [
    {"label": "x", "value": "<img src=x onerror=alert(1)>"},
    {"label": "y\nz", "value": "line\nbreak"},
])
made = [p for p in panels._load() if "script" in p["title"]][0]
c("  angle brackets are not angle brackets any more", "<" in made["title"], False)
c("  ...in values either", any("<" in r["value"] for r in made["items"]), False)
c("  a newline cannot draw a second row", "\n" in made["items"][1]["value"], False)
c("  and one row stays one row", len(made["items"]), 2)
long_title = panels.make_panel("T" * 200, [{"label": "L" * 200, "value": "V" * 400}])
made = [p for p in panels._load() if p["title"].startswith("TTT")][0]
c("  titles are capped", len(made["title"]), panels.MAX_TITLE)
c("  labels are capped", len(made["items"][0]["label"]), panels.MAX_LABEL)
c("  values are capped", len(made["items"][0]["value"]), panels.MAX_VALUE)
many = panels._tidy([{"label": str(i), "value": "x"} for i in range(50)])
c("  and a panel cannot be fifty rows long", len(many), panels.MAX_ITEMS)

print("\n'live' is an allow-list, not a suggestion:")
for good in ("ticker:NVDA", "ticker:BTC-USD", "countdown:2026-11-03", "clock"):
    c("  %-22s kept" % good, panels._tidy([{"label": "a", "value": "b",
                                            "live": good}])[0].get("live"), good)
for bad in ("fetch:https://evil.example/x", "eval:1+1", "ticker:" + "A" * 40,
            "countdown:not-a-date", "clock()", "javascript:alert(1)"):
    got = panels._tidy([{"label": "a", "value": "b", "live": bad}])[0]
    c("  %-22s dropped" % bad[:22], got.get("live"), None)
    c("  ...and the row survives as text", got["value"], "b")

print("\nToo many panels is a sentence, not a silent drop:")
panels._save([])
for i in range(panels.MAX_PANELS):
    panels.make_panel("Panel %d" % i, ["a: b"])
over = panels.make_panel("One too many", ["a: b"])
c.truthy("  it says so", "as many as fit" in over)
c("  and nothing was pushed out", len(panels._load()), panels.MAX_PANELS)
c.truthy("  but replacing an existing one still works",
         panels.make_panel("Panel 0", ["c: d"]).startswith("Updated"))

print("\nTaking them down:")
c.truthy("  by name", "Taken down" in panels.remove_panel("Panel 1"))
c.truthy("  a miss says what IS there", "Nothing matched" in panels.remove_panel("zzz"))
c.truthy("  'all' clears the lot", "Cleared" in panels.remove_panel("all"))
c("  and they are gone", panels._load(), [])
c.truthy("  removing nothing is not an error",
         "no panels" in panels.remove_panel("anything").lower())

print("\nOne person's panels are their own:")
panels.make_panel("Owner's card", ["a: b"])
whose.use("guest@example.com")
c("  a guest sees none of the owner's", panels._load(), [])
panels.make_panel("Guest's card", ["c: d"])
c("  and keeps their own", [p["title"] for p in panels._load()], ["Guest's card"])
whose.use("owner@example.com")
c("  the owner's is untouched", [p["title"] for p in panels._load()], ["Owner's card"])

print("\nOver HTTP:")
with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "phone")}
    r = client.get("/api/panels", cookies=OWNER)
    c("  the owner's panels load", r.status_code, 200)
    c("  ...and are theirs", [p["title"] for p in r.json()["panels"]], ["Owner's card"])
    g = client.get("/api/panels", cookies=GUEST)
    c("  a guest gets their own, not a 403", g.status_code, 200)
    c("  ...which is a different card", [p["title"] for p in g.json()["panels"]],
      ["Guest's card"])
    c("  a stranger gets nothing", client.get("/api/panels").status_code, 401)
    c("  and the poll does not hold a session open",
      "/api/panels" in run.BACKGROUND_PATHS, True)
    session.revoke_all()

print("\nWired in, and gated the way its siblings are:")
c.truthy("  it is a toolkit", panels in run.TOOLKITS)
for t in ("make_panel", "list_panels", "remove_panel"):
    c("  %-12s routes to it" % t, run.TOOL_OWNER.get(t), panels)
    c("  %-12s needs no permission prompt" % t, t in run.PASSIVE_TOOLS, True)
    c("  %-12s is a guest's too" % t, t in run.guest_tools(), True)
c.truthy("  ...and the reason it is passive is written down",
         "consent prompt at its most pointless" in
         io.open(ARC / "run.py", encoding="utf-8").read())
c.truthy("  the data file is not committed",
         "panels.json" in io.open(ARC / ".gitignore", encoding="utf-8").read())

print("\nThe page draws them as TEXT, which is the check that matters:")
up = body[body.index("(function userPanels()"):]
up = up[:up.index("/* ---------- draggable HUD panels ----------")]
# The USE, not the word: the comment above this module explains why innerHTML
# is never called here, and a plain substring check reads its own explanation
# as the thing it forbids.
c("  no innerHTML assignment in the painter", len(re.findall(r"\.innerHTML\s*=", up)), 0)
c("  ...and none read back either", len(re.findall(r"\.innerHTML\b(?!\s*=)", up)), 0)
c("  nor insertAdjacentHTML, which is the same door",
  "insertAdjacentHTML" in up or "outerHTML" in up, False)
c.truthy("  rows are built as elements", "document.createElement(tag)" in up)
c.truthy("  ...and filled with textContent", "n.textContent = text" in up)
c.truthy("  the live kinds are checked again here", "const LIVE_OK" in up)
c.truthy("  ...and an unknown one leaves plain text",
         'r.live && LIVE_OK.test(r.live) ? r.live : ""' in up)
c.truthy("  and the reason for all of it is on the page",
         "EVERY STRING HERE WAS WRITTEN BY THE MODEL" in body)

print("\nAnd they behave like the cards that were built by hand:")
c.truthy("  same chrome", ".plan, .chart, .userpanel {" in page)
c.truthy("  dragged by the same machinery, not a second copy",
         "window.arcMakeDraggable = makeDraggable;" in body)
c.truthy("  ...which the painter uses", "window.arcMakeDraggable(box)" in up)
c.truthy("  a panel taken down takes its card with it", "removeChild(gone)" in up)
c.truthy("  a dropped poll keeps what is on screen", "keep what is on screen" in up)
c.truthy("  and one asked for mid-turn appears when the reply does",
         "window.arcPanels.refresh()" in body)

print("\nThe model is told how to fill the form in:")
rules = io.open(ARC / "prompts" / "main.md", encoding="utf-8").read()
c.truthy("  that the tool exists", "make_panel" in rules)
c.truthy("  what live can be", "countdown:2026-11-03" in rules)
c.truthy("  to write the rows itself rather than interrogating the user",
         "WRITE THE ROWS YOURSELF" in rules)
c.truthy("  and that the same title replaces", "means make_panel again" in rules)

panels._save([])
c.done()
