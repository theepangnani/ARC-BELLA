#!/usr/bin/env python3
"""Getting places — how long, how far, and put it on the screen.

NO API KEY, deliberately. Google's Directions API needs a billing account and a
key with a card behind it, which is a strange thing to require of somebody who
just wants to know whether to leave now. Two free services do the job:

  · Nominatim to turn "Toronto Pearson Airport" into a point on the earth.
  · open-meteo's geocoder as its understudy — extras.py already trusts it for
    the weather, so it is one fewer service and already proven here. It knows
    PLACES but not LANDMARKS, which is why it is second: nobody asks for
    directions to a municipality.
  · OSRM's public router for the actual route.

WHAT IT DOES NOT DO, and the reason is worth stating. It does not navigate, and
it does not read a route out turn by turn. A voice assistant reciting "in four
hundred metres, keep left" while you are driving is worse than the phone mounted
on the dashboard that was built for it, and would be dangerous to get wrong. The
useful questions are "how long will it take" and "show me" — answered here, and
the screen link hands the actual navigating to the thing that does it properly.

DEGRADES RATHER THAN FAILS. If the router is unreachable — it is a public
good-will service with no promises attached — the answer still carries the
distance as the crow flies and a link that works. "I can't reach the router" is
a worse answer than "about 40 km away, here's the map".
"""

import math
import os
import urllib.parse
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.resolve()

# TWO geocoders, and the order matters. open-meteo's knows PLACES -- cities,
# towns, countries -- and extras.py already trusts it for the weather. It does
# not know POINTS OF INTEREST, and "how long to the airport" is the whole
# feature: nobody asks for directions to a municipality. Nominatim knows both,
# so it goes first and open-meteo catches whatever it misses.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
OSRM_URL = "https://router.project-osrm.org/route/v1"

# OSRM's public server asks callers to identify themselves and keep the volume
# sane. One person asking how long to the airport is well inside that.
UA = {"User-Agent": "ARC-voice-assistant (personal use)"}

# What a person says -> what OSRM calls it. Deliberately small: these are the
# three the public router actually serves.
MODES = {
    "driving": "driving", "drive": "driving", "car": "driving",
    "walking": "foot", "walk": "foot", "on foot": "foot",
    "cycling": "bike", "bike": "bike", "bicycle": "bike",
}

# Where "home" is, when somebody asks how long to somewhere without saying from
# where. Falls back to asking rather than guessing at the centre of the country.
HOME = os.getenv("ARC_HOME_PLACE", "").strip()


def connected() -> bool:
    return True     # nothing to set up


def _tidy(name: str) -> str:
    """Nominatim answers with the whole postal address. Spoken aloud, "Toronto
    Pearson International Airport, 6301, Silver Dart Drive, Malton, Mississauga,
    Peel Region, Ontario, L5P 1B2, Canada" is not an answer, it is a punishment.
    The first part and the region are what a person would have said."""
    bits = [b.strip() for b in (name or "").split(",") if b.strip()]
    if len(bits) <= 2:
        return ", ".join(bits)
    # Skip house numbers and postcodes in the middle; keep the head and the tail.
    tail = [b for b in bits[1:] if not any(ch.isdigit() for ch in b)]
    return ", ".join([bits[0]] + tail[-2:]) if tail else bits[0]


def _geocode(place: str):
    """(lat, lon, readable name) or None.

    Nominatim first because it knows landmarks; open-meteo second because it is
    already trusted here and answers when Nominatim is unreachable.
    """
    q = (place or "").strip()
    if not q:
        return None
    try:
        with httpx.Client(timeout=12, headers=UA) as c:
            r = c.get(NOMINATIM_URL,
                      params={"q": q, "format": "json", "limit": 1}).json()
        if r:
            hit = r[0]
            return float(hit["lat"]), float(hit["lon"]), _tidy(hit.get("display_name", q))
    except Exception:
        pass
    try:
        with httpx.Client(timeout=12, headers=UA) as c:
            r = c.get(GEO_URL, params={"name": q, "count": 1}).json()
        hit = (r.get("results") or [None])[0]
        if not hit:
            return None
        label = ", ".join(x for x in (hit.get("name"), hit.get("admin1"),
                                      hit.get("country")) if x)
        return hit["latitude"], hit["longitude"], label
    except Exception:
        return None


def _crow(a, b) -> float:
    """Kilometres in a straight line. The fallback when routing is unavailable —
    honest about being a straight line rather than pretending to be a road."""
    R = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = math.radians(b[0] - a[0])
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _spoken_time(minutes: float) -> str:
    """Read aloud, so no digits-and-colons. 95 minutes is 'an hour and a half',
    not '1:35'."""
    m = int(round(minutes))
    if m < 1:
        return "under a minute"
    if m < 60:
        return "%d minute%s" % (m, "" if m == 1 else "s")
    h, rest = divmod(m, 60)
    out = "%d hour%s" % (h, "" if h == 1 else "s")
    if rest:
        out += " %d minute%s" % (rest, "" if rest == 1 else "s")
    return out


def _link(origin_label, dest_label, mode) -> str:
    """A plain Google Maps URL. No key, no SDK — the navigating is handed to the
    application built for it."""
    q = {"api": "1", "destination": dest_label, "travelmode":
         {"foot": "walking", "bike": "bicycling"}.get(mode, "driving")}
    if origin_label:
        q["origin"] = origin_label
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)


def directions(destination: str = "", origin: str = "", mode: str = "driving") -> str:
    """How long to get somewhere, and a map link to open."""
    dest = (destination or "").strip()
    if not dest:
        return "Where would you like to go?"
    src = (origin or "").strip() or HOME
    if not src:
        return ("Where are you starting from? I can remember it if you set "
                "ARC_HOME_PLACE, and then you won't have to say it again.")

    prof = MODES.get((mode or "driving").strip().lower(), "driving")
    a, b = _geocode(src), _geocode(dest)
    if not a:
        return "I couldn't find a place called %s to start from." % src
    if not b:
        return "I couldn't find %s on the map." % dest

    link = _link(a[2], b[2], prof)
    try:
        with httpx.Client(timeout=15, headers=UA) as c:
            r = c.get("%s/%s/%f,%f;%f,%f" % (OSRM_URL, prof, a[1], a[0], b[1], b[0]),
                      params={"overview": "false"}).json()
        route = (r.get("routes") or [None])[0]
        if not route:
            raise ValueError("no route")
        mins = route["duration"] / 60.0
        km = route["distance"] / 1000.0
        how = {"foot": "on foot", "bike": "by bike"}.get(prof, "driving")
        return ("%s to %s is about %s %s, roughly %s kilometres. %s"
                % (a[2], b[2], _spoken_time(mins), how,
                   ("%.0f" % km) if km >= 10 else ("%.1f" % km), link))
    except Exception:
        # The router is a public good-will service with no promises attached.
        # A straight-line distance and a working link beat an error message.
        km = _crow((a[0], a[1]), (b[0], b[1]))
        return ("I couldn't reach the route service, but %s is about %s "
                "kilometres from %s as the crow flies. %s"
                % (b[2], ("%.0f" % km) if km >= 10 else ("%.1f" % km), a[2], link))


def find_place(query: str = "") -> str:
    """Where something is, without routing to it."""
    hit = _geocode(query)
    if not hit:
        return "I couldn't find %s." % (query or "that")
    return "%s is at roughly %.4f, %.4f." % (hit[2], hit[0], hit[1])


TOOLS = [
    {"name": "directions",
     "description": (
         "How long it takes to get somewhere and how far it is, with a map link "
         "to open. Use for 'how long to the airport', 'how far is Ottawa', "
         "'directions to the office', 'how do I get to X', 'should I leave now'. "
         "`destination` is where they are going. `origin` is where from — omit "
         "it to use their usual starting point. `mode` is driving, walking or "
         "cycling. This does NOT navigate turn by turn; it answers how long and "
         "hands the map to Google Maps."),
     "input_schema": {"type": "object", "properties": {
         "destination": {"type": "string"},
         "origin": {"type": "string"},
         "mode": {"type": "string", "description": "driving, walking or cycling"}},
         "required": ["destination"]}},

    {"name": "find_place",
     "description": (
         "Where a place is, without working out a route to it. Use for 'where is "
         "X', 'which country is Y in'. For 'how long to X' use directions instead."),
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"}}, "required": ["query"]}},
]

_DISPATCH = {"directions": directions, "find_place": find_place}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
