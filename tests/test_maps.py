# -*- coding: utf-8 -*-
"""Getting places: how long, how far, and a map to open.

No API key, deliberately — Google's Directions API wants a billing account and a
card, which is a strange thing to require of somebody asking whether to leave
now. Nominatim finds the place, OSRM works out the route, and the navigating is
handed to Google Maps by link.

The network is NOT exercised here. Three public good-will services, called on
every test run, from every machine that ever clones this, is a bill somebody
else pays and a suite that fails when a stranger's server is busy. What is
tested is everything around the call: the shape of the answer, the fallbacks,
and the two decisions that are easy to get wrong — reading a duration ALOUD, and
what to do when the router is unreachable.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

import maps   # noqa: E402
import run    # noqa: E402

c = Check()

print("A duration is READ, not displayed:")
# This is spoken aloud. "1:35" is a time of day; "95 minutes" is arithmetic
# somebody has to do while driving.
for mins, want in [(0.4, "under a minute"), (1, "1 minute"), (7, "7 minutes"),
                   (59, "59 minutes"), (60, "1 hour"), (61, "1 hour 1 minute"),
                   (95, "1 hour 35 minutes"), (319, "5 hours 19 minutes")]:
    c("  %-5s -> %s" % (mins, want), maps._spoken_time(mins), want)

print("\nAn address is trimmed to what a person would have said:")
# Nominatim answers with the whole postal address. Spoken, the full version is
# not an answer, it is a punishment.
c("  the airport", maps._tidy(
    "Toronto Pearson International Airport, 6301, Silver Dart Drive, Malton, "
    "Mississauga, Peel Region, Ontario, L5P 1B2, Canada"),
  "Toronto Pearson International Airport, Ontario, Canada")
c("  something already short", maps._tidy("Ottawa, Canada"), "Ottawa, Canada")
c("  and one bare name", maps._tidy("Ottawa"), "Ottawa")

print("\nThe map link needs no key and no SDK:")
link = maps._link("Markham, Ontario", "Toronto Pearson Airport", "driving")
c.truthy("  it is a plain Google Maps URL", link.startswith("https://www.google.com/maps/dir/?"))
c.truthy("  carrying both ends", "Markham" in link and "Pearson" in link)
c.truthy("  and the travel mode", "travelmode=driving" in link)
c.truthy("  walking asks for walking",
         "travelmode=walking" in maps._link("a", "b", "foot"))
c.truthy("  cycling for bicycling",
         "travelmode=bicycling" in maps._link("a", "b", "bike"))
# No key anywhere: that is the whole reason this works without a billing account.
src = io.open(ARC / "maps.py", encoding="utf-8").read()
c("  no API key is read from anywhere", "API_KEY" in src, False)

print("\nWhat a person says maps to what the router calls it:")
for said, want in [("driving", "driving"), ("car", "driving"), ("drive", "driving"),
                   ("walking", "foot"), ("walk", "foot"), ("on foot", "foot"),
                   ("cycling", "bike"), ("bike", "bike"), ("bicycle", "bike")]:
    c("  %-9s -> %s" % (said, want), maps.MODES[said], want)
c("  and anything unrecognised drives",
  maps.MODES.get("teleport", "driving"), "driving")

print("\nIt asks rather than guessing:")
c.truthy("  no destination is a question", "Where would you like to go" in maps.directions(""))
_home = maps.HOME
maps.HOME = ""
out = maps.directions("Ottawa")
c.truthy("  no origin and no home is a question too", "Where are you starting from" in out)
# Guessing at the middle of the country and confidently reporting six hours is
# worse than asking.
c.truthy("  ...and it says how to stop being asked", "ARC_HOME_PLACE" in out)
maps.HOME = _home

print("\nIt degrades rather than failing:")
# The router is a public service with no promises attached. "I can't reach the
# router" is a worse answer than "about 40 km away, here's the map".
c.truthy("  there is a straight-line fallback", "def _crow" in src)
c.truthy("  ...and it says it is a straight line", "as the crow flies" in src)
c.truthy("  the link is built BEFORE the route is attempted",
         src.index("link = _link(") < src.index("routes"))
# Toronto to Ottawa, near enough.
km = maps._crow((43.65, -79.38), (45.42, -75.70))
c.truthy("  and the arithmetic is right", 330 < km < 380)

print("\nWhat it deliberately does not do:")
# A voice assistant reciting "in four hundred metres, keep left" while somebody
# is driving is worse than the phone built for it, and dangerous to get wrong.
c.truthy("  it does not navigate turn by turn", "does not navigate" in src)
c.truthy("  ...and the tool description says so, to the model",
         any("does NOT navigate" in t["description"] for t in maps.TOOLS))

print("\nIt is wired in, and a guest may ask:")
c.truthy("  registered as a toolkit", maps in run.TOOLKITS)
c("  both tools are known", {"directions", "find_place"} <= set(run.TOOL_OWNER), True)
# Roads are public. Asking how long to the airport is not asking about anybody's
# diary, so it sits with weather and news rather than with mail.
c("  a guest may ask for directions", "directions" in run.GUEST_TOOLS, True)
c("  and where a place is", "find_place" in run.GUEST_TOOLS, True)

print("\nAnd it identifies itself to the services it borrows:")
# Both are public good-will servers with usage policies. Turning up anonymously
# at volume is how a free service stops being free for everybody.
c.truthy("  a real User-Agent", "ARC-voice-assistant" in maps.UA.get("User-Agent", ""))
c.truthy("  ...and the courtesy is written down", "good-will" in src)




# --------------------------------------------------------- currency and sun
# Two gaps against what a Google Assistant answers, and one of them is a
# WRONG-ANSWER gap rather than a missing one.
import extras   # noqa: E402

print("\nAn exchange rate is looked up, never remembered:")
# Asked "what is fifty dollars in euros", a model answers confidently from a
# rate it learned months ago and is quietly wrong — which is the one failure
# the rulebook says never to commit. A rate is a fact about TODAY.
c.truthy("  there is a tool for it", "convert_money" in extras._DISPATCH)
c.truthy("  ...and the description tells the model not to do it itself",
         any("rather than working it out yourself" in t["description"]
             for t in extras.TOOLS if t["name"] == "convert_money"))
c.truthy("  it needs no API key",
         "frankfurter" in io.open(ARC / "extras.py", encoding="utf-8").read())
c.truthy("  and it admits what kind of rate it is",
         "reference rates" in io.open(ARC / "extras.py", encoding="utf-8").read())
# Bad input must ask, not guess.
c.truthy("  two-letter codes are refused", "three-letter" in extras.convert_money(1, "US", "EU"))
c.truthy("  the same currency twice is noticed", "same currency" in extras.convert_money(1, "USD", "USD"))
c.truthy("  and a non-number asks", "How much" in extras.convert_money("lots", "USD", "EUR"))

print("\nTimes are SPOKEN, which is a rule this broke twice before it worked:")
# "1 05 in the afternoon" is exactly what the rulebook forbids, and what a
# synthesiser makes a hash of. And the quarter checks have to know which side
# of the hour they are on.
for hhmm, want in [("00:00", "midnight"), ("12:00", "midday"),
                   ("07:15", "quarter past seven"),
                   # This one was announced as "quarter PAST six" — half an
                   # hour out, in the sort of answer somebody sets an alarm by.
                   ("17:45", "quarter to six"),
                   ("07:30", "half past seven"),
                   ("13:05", "five minutes past one"),
                   ("19:42", "eighteen minutes to eight"),
                   ("23:59", "one minute to twelve")]:
    c.truthy("  %-6s -> %s" % (hhmm, want), extras._clock(hhmm).startswith(want))
c("  and nothing digit-shaped survives",
  any(ch.isdigit() for ch in extras._clock("13:05")), False)
c("  rubbish is handed back untouched", extras._clock("bad"), "bad")

print("\nBoth are public facts, so a guest may ask:")
c("  currency", "convert_money" in run.GUEST_TOOLS, True)
c("  sunset", "sun_times" in run.GUEST_TOOLS, True)
# 23 since the plan arrived: a guest keeps their own scratch plan for a long
# job, which contains only what they themselves asked for. This number is a
# guard, not a fact -- it changes only when somebody decides it should.
c("  the guest tier is 23 now, deliberately", len(run.GUEST_TOOLS), 23)

c.done()
