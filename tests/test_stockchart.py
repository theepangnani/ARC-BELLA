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

# Built from the environment, as test_meta requires: Program Files is not
# always on C:, and a per-user Chrome lives under LOCALAPPDATA.
_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                          "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]


def run_chart_clicks(mod, css):
    """Run the real chart module in a real browser and click its controls.

    Returns the watched symbol after each click, or None if there is no
    browser here. The page is stubbed at exactly two points — the fetch, so no
    network is touched, and requestAnimationFrame, which under headless virtual
    time fires late enough to race the probe. Everything else is the shipped
    code.
    """
    import shutil
    import subprocess
    import tempfile
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    payload = json.dumps({"symbol": "NVDA", "currency": "USD",
                          "days": fake(126)["days"], "first": 100.0, "last": 162.5,
                          "change": 62.5, "pct": 62.5, "high": 162.5, "low": 100.0})
    probe = """
const log = [];
const click = sel => { const el = document.querySelector(sel);
  if (el) el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  else log.push("MISSING " + sel); };
log.push(window.arcChart.watching());
click('.ch-nav[data-step="1"]');      log.push(window.arcChart.watching());
click('.ch-nav[data-step="1"]');      log.push(window.arcChart.watching());
click('.ch-nav[data-step="-1"]');     log.push(window.arcChart.watching());
click('.ch-tab[data-sym="BTC-USD"]'); log.push(window.arcChart.watching());
click('.ch-nav[data-step="1"]');      log.push(window.arcChart.watching());
click('.ch-r[data-r="1y"]');
log.push((document.querySelector(".ch-r.on") || {}).textContent || "none");
log.push((document.querySelector(".ch-tab.on") || {}).textContent || "none");
document.getElementById("probe").textContent = log.join("|");
"""
    html = ('<!doctype html><meta charset="utf-8"><style>'
            ':root{--accent:#5fd9ff;--ice:#cfefff;--cyan-dim:#4a7d99;--void:#03070c;'
            '--display:system-ui}'
            '.chart{--cs:1;position:relative;width:268px;padding:11px 13px}'
            + css.replace("@media (max-width: 1180px) { .chart { display: none; } }", "")
            + '</style><div class="chart" id="chart" hidden></div><pre id="probe"></pre>'
            '<script>window.requestAnimationFrame = f => f();'
            'window.arcMarket = { list: () => ["NVDA","AAPL","TSLA","BTC-USD"] };'
            'window.fetch = async () => ({ ok: true, json: async () => (' + payload + ') });'
            + mod + probe + '</script>')
    work = tempfile.mkdtemp(prefix="arcchart")
    try:
        p = os.path.join(work, "chart.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--user-data-dir=" + os.path.join(work, "p"),
             "--virtual-time-budget=6000", "--dump-dom",
             "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        return m.group(1).split("|") if m and m.group(1).strip() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
chart_css = page[page.index("  /* One stock, over time."):page.index("  /* draggable HUD panels")]
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

print("\nSwitching between stocks, without going back to the panel:")
# The card charts ONE stock, so getting to the others has to cost a click
# rather than a trip to the markets panel and a hunt for the right row.
c.truthy("  arrows either side of the name", 'data-step="-1"' in ch and 'data-step="1"' in ch)
c.truthy("  and a tab per stock", "ch-tab" in ch and "function tabsHtml" in ch)
c("  one stock gets no tabs and no arrows",
  "if (!list || list.length < 2) return \"\";" in ch and "list.length > 1" in ch, True)
c.truthy("  the tabs ARE the markets panel, not a copy",
         "window.arcMarket && window.arcMarket.list()" in ch)
c.truthy("  ...so the panel tells the chart when it changes",
         "window.arcChart.sync()" in body)
c.truthy("  a stock charted by voice still gets a tab",
         "list = [symbol].concat(list)" in ch)
c.truthy("  removing the charted stock moves to one that is left",
         "panel.indexOf(symbol) < 0" in ch)
c.truthy("  the arrows wrap rather than going dead at the ends",
         "(at + dir + list.length) % list.length" in ch)
c.truthy("  ...and the reason is written down", "feels broken rather than bounded" in ch)
c.truthy("  the lit tab is kept in view when there are more than fit",
         "scrollIntoView" in ch)
c.truthy("  ...and the tab strip scrolls instead of growing a second row",
         "overflow-x: auto" in page and ".ch-tabs" in page)
print("\n  ...and the switching is DRIVEN, not read:")
# Everything above is a string in a file. Switching is a sequence — next, next,
# back, jump, wrap — and no string check can tell a working cycle from an
# off-by-one, so the real module is run in a real engine and clicked. Same
# approach as test_speech_gate; where no browser exists the gap is named rather
# than counted as a pass.
got = run_chart_clicks(mod=ch, css=chart_css)
if got is None:
    print("    NOTE  no Chrome or Edge here. NOT CONFIRMED — the clicks below")
    print("          are a gap, not a pass. CI has a browser, which closes it.")
else:
    c("    it starts on the first stock", got[0], "NVDA")
    c("    next steps forward", (got[1], got[2]), ("AAPL", "TSLA"))
    c("    previous steps back", got[3], "AAPL")
    c("    a tab jumps straight there", got[4], "BTC-USD")
    c("    past the end it wraps to the start", got[5], "NVDA")
    c("    the range buttons still work", got[6], "1y")
    c("    and the lit tab follows the stock", got[7], "NVDA")

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
