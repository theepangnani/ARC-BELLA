# -*- coding: utf-8 -*-
"""Arc Watch draws usage as graphs, not just as numbers on cards.

Asked for as: "visualize daily spend, message count, and other metrics over
time as a graph instead of just static panel numbers". The record was already
there — stats.py keeps a row per day for a year — and the page drew exactly one
thing from it, a bar per day of cost. So this is about the drawing and about
the sentence under it, and what is worth holding:

  · EVERY METRIC IS A FIELD stats.py ALREADY KEEPS, not an estimate made in the
    page, and a day with no figure is a gap, not a zero.
  · THE COMPARISON IS HONEST: "up 30% on the previous 30 days" only when the
    record reaches back that far. A previous year made of zeros would announce
    a collapse that never happened.
  · IT WORKS IN A REAL BROWSER: metric buttons, a tool picked from the table, a
    link from the HUD naming a metric, and a hover that says what day it is.
  · NOTHING FROM THE RECORD IS WRITTEN AS MARKUP unescaped. Tool and model names
    come from the server, and the page must not be a way to inject into itself.
"""
import io
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import stats     # noqa: E402

c = Check()
WATCH = io.open(ARC / "static" / "watch.html", encoding="utf-8").read()
page = io.open(HUD, encoding="utf-8").read()

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                          "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]


def seed():
    """Sixty days: the last thirty at $0.20 and 10 messages a day, the thirty
    before at $0.10 and 5, with every seventh day idle. Worked out by hand below."""
    stats._days.clear()
    stats._loaded = True
    today = date.today()
    for i in range(60):
        d = (today - timedelta(days=i)).isoformat()
        if i % 7 == 3:
            continue
        recent = i < 30
        stats._days[d] = {**stats._blank(), "cost": 0.2 if recent else 0.1,
                          "turns": 10 if recent else 5,
                          "tools": {"web_search": 2, "weather": 1},
                          "spend": {"claude-sonnet-5": 0.2 if recent else 0.1},
                          "models": {"claude-sonnet-5": 10 if recent else 5}}


print("The route hands the page this window and the one before it:")
seed()
with TestClient(run.app) as client:
    O = {run.COOKIE: session.create("owner@example.com", "browser")}
    G = {run.COOKIE: session.create("guest@example.com", "browser")}
    d = client.get("/api/usage?days=30", cookies=O).json()
    c("  thirty days drawn", len(d["series"]), 30)
    c("  and thirty before them to compare with", len(d["previous"]), 30)
    c("  the previous window ends the day before this one starts",
      (date.fromisoformat(d["previous"][-1]["date"]) + timedelta(days=1)).isoformat(),
      d["series"][0]["date"])
    c("  empty days are present, as zeros", sum(1 for r in d["series"] if not r["turns"]), 4)
    y = client.get("/api/usage?days=365", cookies=O).json()
    c("  a year has nothing to compare with: the record is not two years long",
      y["previous"], None)
    c("  a guest still cannot read the owner's bill",
      client.get("/api/usage?days=30", cookies=G).status_code, 403)
    session.revoke_all()
SAMPLE = d

print("\nThe page draws it itself, from what it was given:")
c("  no chart library, no outside request", re.search(r"<script[^>]+src=", WATCH), None)
c.truthy("  a canvas", "<canvas" in WATCH)
for key, field in [("cost", "d.cost"), ("turns", "d.turns"), ("tokens", "d.tok_in"),
                   ("tools", "sum(d.tools)"), ("searches", "d.searches"), ("errors", "d.errors"),
                   ("saved", "d.saved"), ("voice", "d.voice_chars"), ("alarms", "d.alarms"),
                   ("alerts", "d.alerts")]:
    c.truthy("  %-9s reads %s" % (key, field), re.search(r"\b%s:\s*\{[^}]*%s" % (key, re.escape(field)), WATCH))
    c.truthy("  %-9s is a field stats.py keeps" % key,
             field.split("(")[-1].rstrip(")").replace("d.", "") in stats._blank())
c.truthy("  cost per message is a gap on a day with no messages", "d.turns ? d.cost / d.turns : null" in WATCH)
c.truthy("  and over a period it is spend over messages, not an average of days", "RATIO" in WATCH)

print("\nNothing from the record becomes markup unescaped:")
# Proved in the browser below with a tool name that is an attack; these are
# the helpers that make it hold.
c.truthy("  text is escaped before it is markup", "d.textContent = s; return d.innerHTML" in WATCH)
c.truthy("  quotes are escaped inside attributes", 'replace(/"/g, "&quot;")' in WATCH)
c.truthy("  the tooltip is built with textContent", "tip.textContent = \"\"" in WATCH)


def run_page(query, probe):
    import shutil
    import subprocess
    import tempfile
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    stub = ("<script>window.fetch = async (u) => { const n = +(/days=(\\d+)/.exec(u) || [0, 30])[1];"
            " const d = JSON.parse(JSON.stringify(" + json.dumps(SAMPLE) + "));"
            " d.series = d.series.slice(-n); if (n !== 30) d.previous = null;"
            " return { ok: true, status: 200, json: async () => d }; };</script>")
    html = WATCH.replace("<script>", stub + "<script>", 1).replace(
        "</body>", '<pre id="probe"></pre><script>(async () => { const log = [];'
        ' const wait = () => new Promise(r => setTimeout(r, 50));'
        ' for (let i = 0; i < 100 && !(window.arcWatch && arcWatch.said()); i++) await wait();'
        + probe + ' document.getElementById("probe").textContent = log.join("|"); })();</script></body>')
    work = tempfile.mkdtemp(prefix="arcwatch")
    try:
        p = os.path.join(work, "watch.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--window-size=1100,900",
             "--no-default-browser-check", "--user-data-dir=" + os.path.join(work, "p"),
             "--virtual-time-budget=8000", "--dump-dom",
             "file:///" + p.replace("\\", "/") + query],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        # --dump-dom serialises the page, so the probe's own text comes back
        # with < written as &lt;. Undo that, or a correctly escaped name reads
        # here as if the page had double-escaped it.
        import html as _html
        return ([_html.unescape(x) for x in m.group(1).split("|")]
                if m and m.group(1).strip() else None)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nIn a real browser:")
got = run_page("", """
  const cv = document.getElementById("canvas"), g = cv.getContext("2d");
  const px = g.getImageData(0, 0, cv.width, cv.height).data;
  let lit = 0; for (let i = 3; i < px.length; i += 4) if (px[i]) lit++;
  log.push(arcWatch.chosen, String(lit > 500), arcWatch.said());
  document.querySelector('#metrics [data-metric="turns"]').click();
  log.push(arcWatch.chosen, arcWatch.said(), document.getElementById("gTitle").textContent);
  document.querySelector('.pick[data-metric="tool:web_search"]').click();
  log.push(arcWatch.chosen, document.querySelector("#metrics .on").textContent);
  arcWatch.hover(29);
  log.push(arcWatch.tip());
  document.querySelector('.card[data-metric="cost"]').click();
  log.push(arcWatch.chosen, new URL(location.href).searchParams.get("metric"));
""")
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
else:
    # 26 active days of the 30 at $0.20 = $5.20. The thirty before have five
    # idle days (i = 31, 38, 45, 52, 59), so 25 at $0.10 = $2.50: up 108%.
    c("  opens on spend", got[0], "cost")
    c("  and something is actually drawn", got[1], "true")
    c.truthy("  the total is worked out: " + got[2], "Total $5.200 over 30 days" in got[2])
    c.truthy("  per active day, not per calendar day", "$0.200 a day on the 26 days" in got[2])
    c.truthy("  and compared honestly with the month before", "▲ 108% on the previous 30 days" in got[2])
    c("  the Messages button switches the graph", got[3], "turns")
    c.truthy("  to messages: " + got[4], "Total 260 over 30 days" in got[4])
    c("  and says so in the title", got[5], "Messages per day")
    c("  a tool picked from the table gets its own graph", got[6], "tool:web_search")
    c("  with a button naming it", got[7], "web_search calls")
    c.truthy("  hovering a day says which day and how much: " + got[8],
             "today" in got[8] and "2 web_search calls" in got[8])
    c("  a card is a way into its graph", got[9], "cost")
    c("  and the address follows, so the link can be shared", got[10], "cost")

EVIL = '<img src=x onerror="window.pwned=1">"x'
SAMPLE["totals"]["tools"] = {EVIL: 3}
SAMPLE["totals"]["models"] = {EVIL: 3}
got = run_page("", """
  const pick = [...document.querySelectorAll(".pick")].find(b => b.dataset.metric.startsWith("tool:"));
  log.push(String(document.querySelectorAll("#out img").length), String(!!window.pwned),
           pick ? pick.textContent : "none");
  if (pick) pick.click();
  log.push(String(document.querySelectorAll("img").length), String(!!window.pwned), arcWatch.chosen);
""")
if got is not None:
    print("\\n  with a tool name that is an attack:".replace("\\n", "\n"))
    c("  a tool name that is an HTML tag is not made into one", got[0], "0")
    c("  and runs nothing", got[1], "false")
    c("  it is shown as the text it is", got[2], EVIL)
    c("  charting it builds no markup either", got[3:5], ["0", "false"])
    c("  and the name survives the attribute intact", got[5], "tool:" + EVIL)
SAMPLE["totals"]["tools"] = {"web_search": 52}

got = run_page("?metric=errors&days=7", """
  log.push(arcWatch.chosen, String(arcWatch.days), arcWatch.said());
""")
if got is not None:
    c("  a link naming a metric opens on it", got[0], "errors")
    c("  and on the range it names", got[1], "7")
    c.truthy("  an empty graph says so rather than drawing nothing silently",
             got[2].startswith("Nothing recorded for errors"))

print("\nThe HUD's numbers lead to their graphs:")
c.truthy("  /graph is a command", "graph:   { hint:" in page)
c.truthy("  and owner only, like Arc Watch", "run: cmdGraph" in page and "owner: true, run: cmdGraph" in page)
c.truthy("  the spend and exchanges readouts open a graph",
         '[["rCost", "cost"], ["rTurns", "turns"]]' in page)
c.truthy("  but not for a guest", 'if (myTier === "owner") openGraph(metric, 30)' in page)

stats._days.clear()
c.done()
