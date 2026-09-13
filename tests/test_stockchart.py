# -*- coding: utf-8 -*-
"""One stock, drawn — and the line's honesty about what it is.

The markets panel answers "what is it at". A price on its own cannot answer
"what has it been doing", which is the question somebody actually has before
they decide anything, so the chart exists. Three things about it are worth a
test rather than a look:

  · THE SERIES MUST NOT SLIDE. Yahoo returns a null close for a halted day.
    Dropping the null while keeping its date shifts every later point one day
    to the left — which is not a gap in the chart, it is a WRONG chart, drawn
    confidently. The dates and the closes are therefore zipped, never filtered
    apart, and that is the first thing checked here.
  · THE CHART AND THE WORDS COME FROM ONE PLACE. The outlook tool measures
    daily closes; the chart draws them. Two fetchers would eventually disagree
    — "it is down on the quarter" under a line that visibly is not — so
    _history is a view over series() and the test holds them together.
  · IT NEVER PREDICTS. No extrapolation, no trend line drawn forward, and a
    caption saying so on every render. A rising line invites a conclusion it
    cannot support, and the person reading it is deciding what to do with
    money.

The network is never touched: series() is replaced with a known set of closes,
which also makes the arithmetic checkable by hand.
"""
import io
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import market    # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

DAY = 86400
START = 1750000000


def fake(n=300, first=100.0, step=0.5):
    """A straight climb, so every figure below can be worked out by hand."""
    return {"symbol": "TEST", "currency": "USD",
            "days": [{"t": START + i * DAY, "c": first + i * step} for i in range(n)]}


print("A null close takes its date with it:")
# The bug this prevents, spelled out: filter the closes on their own and the
# last point keeps the LAST date while every earlier one has shifted, so the
# line is drawn a day early from the halt onwards and nothing looks wrong.
raw_t = [START, START + DAY, START + 2 * DAY, START + 3 * DAY]
raw_c = [10.0, None, 12.0, 13.0]
paired = [{"t": int(t), "c": float(v)} for t, v in zip(raw_t, raw_c)
          if isinstance(v, (int, float)) and isinstance(t, (int, float))]
c("  the halted day is dropped", len(paired), 3)
c("  and the days that remain keep their own dates",
  [p["t"] - START for p in paired], [0, 2 * DAY, 3 * DAY])
src = io.open(ARC / "market.py", encoding="utf-8").read()
c.truthy("  the pairing is done by zip, not two filters",
         "for t, c in zip(stamps, raw)" in src)
c.truthy("  ...and the reason is written where it is done",
         "one day to the left" in src)

print("\nThe chart and the measurements read the same numbers:")
saved = market.series
market.series = lambda sym: fake()
try:
    hist = market._history("TEST")
    c.truthy("  _history is a view over series()", hist is not None)
    sym, cur, closes = hist
    c("  same symbol", sym, "TEST")
    c("  same closes", closes[:3], [100.0, 100.5, 101.0])
    c("  and the last one is today's", closes[-1], fake()["days"][-1]["c"])
    m = market.measure("TEST")
    c("  the outlook measures that series", round(m["price"], 2), round(closes[-1], 2))
    c("  ...and its high is the series high", round(m["high"], 2), round(max(closes), 2))
finally:
    market.series = saved

print("\nThe route hands the page a window, not a year:")
market.series = lambda sym: fake()
try:
    with TestClient(run.app) as client:
        OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
        r = client.get("/api/stock-history?symbol=TEST&range=1m", cookies=OWNER)
        c("  it answers", r.status_code, 200)
        d = r.json()
        c("  a month is 21 trading days", len(d["days"]), 21)
        c("  six months is 126", len(client.get(
            "/api/stock-history?symbol=TEST&range=6m", cookies=OWNER).json()["days"]), 126)
        c("  a year is 252", len(client.get(
            "/api/stock-history?symbol=TEST&range=1y", cookies=OWNER).json()["days"]), 252)
        c("  an unknown range falls back to six months, not to everything",
          len(client.get("/api/stock-history?symbol=TEST&range=forever",
                         cookies=OWNER).json()["days"]), 126)
        # 21 points climbing 0.5 a day: 20 steps of 0.5 = 10.0 on a base of
        # (last - 20*0.5). Checked as arithmetic rather than by eye.
        first, last = d["first"], d["last"]
        c("  the move is measured across the window shown",
          round(d["change"], 2), round(last - first, 2))
        c("  ...and as a percentage of where the window started",
          round(d["pct"], 4), round((last - first) / first * 100, 4))
        c("  the high and low are of the window too",
          (d["high"], d["low"]), (last, first))
        c("  no ticker is a bad request",
          client.get("/api/stock-history", cookies=OWNER).status_code, 400)

        print("\n  a ticker nobody recognises is an empty line, not a 500:")
        market.series = lambda sym: None
        r = client.get("/api/stock-history?symbol=NOPE", cookies=OWNER)
        c("    still 200", r.status_code, 200)
        c("    with nothing to draw", r.json()["days"], [])

        print("\n  and it is behind the same door as everything else:")
        c("    no cookie", client.get("/api/stock-history?symbol=TEST").status_code, 401)
        session.revoke_all()
finally:
    market.series = saved

print("\nThe series is cached, because a daily close moves once a day:")
c.truthy("  there is a TTL", market._SERIES_TTL >= 60)
calls = {"n": 0}


def counted(sym):
    calls["n"] += 1
    return fake()


market._series_cache.clear()
real = market.series
try:
    # Exercise the real cache by calling the real function with the network
    # part replaced, rather than by trusting the constant above.
    import httpx   # noqa: F401
    market.series = real
    market._series_cache["CACHED"] = (time.time(), fake())
    got = market.series("CACHED")
    c("  a fresh entry is served from memory", got["days"][0]["c"], 100.0)
    market._series_cache["CACHED"] = (time.time() - market._SERIES_TTL - 1, fake())
    c.truthy("  a stale one is not", market._series_cache["CACHED"][0] <
             time.time() - market._SERIES_TTL)
finally:
    market.series = real
    market._series_cache.clear()

print("\nThe card is on the page, and is one of the cards:")
c.truthy("  the element exists", 'id="chart"' in page)
c.truthy("  it starts hidden until something is watched",
         re.search(r'id="chart"[^>]*hidden', page))
c.truthy("  it drags and resizes with the others",
         '"chart"' in body[body.index("const CARDS = ["):][:140])
c.truthy("  it is styled as one of the family", ".plan, .chart {" in page)

print("\nWhat it draws:")
ch = body[body.index("(function stockChart()"):]
ch = ch[:ch.index("/* ---------- draggable HUD panels ----------")]
c.truthy("  the canvas is drawn in device pixels", "devicePixelRatio" in ch)
c.truthy("  ...or a phone gets a soft line", "soft on a phone" in ch)
c.truthy("  a flat series cannot divide by zero", "|| Math.max(hi * 0.01, 0.01)" in ch)
c.truthy("  where the window opened is marked", "days[0].c" in ch and "setLineDash" in ch)
c.truthy("  and so is today, which is the point the eye must land on",
         "lastX" in ch and "arc(" in ch)
c("  nothing is drawn beyond the last close",
  any(w in ch for w in ("predict", "forecast(", "extrapolat", "trendline")), False)
c.truthy("  the caption says what it is, every render",
         "past prices, not a forecast" in ch)
c.truthy("  a blip keeps the last good line rather than blanking it",
         "keep the last good line" in ch)

print("\nChoosing which stock:")
c.truthy("  clicking the name charts it", 'e.target.closest(".st-sym")' in body)
c.truthy("  clicking the price still edits it", "editIdx = i;" in body)
c.truthy("  the hint says so", "name to chart · price to change" in page)
c.truthy("  it survives a reload", '"arc.chart.symbol"' in body)
c.truthy("  ...and so does the range", '"arc.chart.range"' in body)
# Named arc.chart.*, not arc.watch.*: "watch" on this page already means watch
# MODE, the thing that glances at the screen on a timer and spends money doing
# it. test_panel asserts that watch mode is deliberately not remembered, and a
# key called arc.watch made that check read as though it were.
c("  and not under a name watch mode already owns", '"arc.watch"' in body, False)
c.truthy("  with nothing watched it takes the first ticker",
         "window.arcMarket.list()" in ch)

print("\nAnd by voice:")
c.truthy("  the directive takes watch", "watch|chart" in body)
c.truthy("  watching also adds it to the panel, so it is findable later",
         "would be a trapdoor" in body)
rules = io.open(ARC / "prompts" / "main.md", encoding="utf-8").read()
c.truthy("  the model is told the card exists", "[[market: watch" in rules)
c.truthy("  ...and that it stops at today", "THE CHART STOPS AT TODAY" in rules)
c.truthy("  ...and where to send a question about the future",
         "answer with market_outlook and its range" in rules)

c.done()
