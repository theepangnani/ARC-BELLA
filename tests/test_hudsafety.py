# -*- coding: utf-8 -*-
"""What the 13 Sep 2026 bug check found in the page, held so it stays found.

  · A TICKER COULD BE MARKUP. The chart and the markets panel build their rows
    as HTML, and when the stock search finds nothing the raw text is kept as
    the ticker — text that can come from the model, which reads mail. A bug
    check charted <IMG SRC=X ONERROR=...> and it ran; it was also saved, so it
    would have run on every load after. Now: refused on the way in, escaped on
    the way out, and a bad one already saved is dropped.
  · SWITCHING STOCKS FAST SHOWED THE WRONG PRICE. A load in flight blocked the
    next, so › › fetched the first stock and drew it under the second's name.
  · Smaller: the chart and invented panels floated over chat mode; invented
    panels were missed by Reset panels; /graph 7d said "no such graph".
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import HUD, sandbox, Check   # noqa: E402
sandbox()     # nothing here touches data, but test_meta holds every suite to it

c = Check()
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]

HELPERS = body[body.index("  const TICKER_OK"):body.index("  (function stocks() {")]
MARKETS = body[body.index("  (function stocks() {"):body.index("(function stockChart()")]
CHART = body[body.index("(function stockChart()"):body.index("  /* ---------------- panels the user invented")]

EVIL = "<IMG SRC=X ONERROR=&#119;&#105;&#110;&#100;&#111;&#119;.pwned=1>"


def run(script, probe, storage=None):
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    pre = "".join("localStorage.setItem(%s, %s);" % (json.dumps(k), json.dumps(v))
                  for k, v in (storage or {}).items())
    html = ('<!doctype html><meta charset="utf-8"><div id="stocks"></div><div id="chart" hidden></div>'
            '<pre id="probe"></pre><script>' + pre + 'window.requestAnimationFrame = f => f();'
            + script + '\n(async () => { const log = []; const wait = ms => new Promise(r => setTimeout(r, ms));'
            + probe + ' document.getElementById("probe").textContent = log.join("|"); })();</script>')
    work = tempfile.mkdtemp(prefix="arcsafe")
    try:
        p = os.path.join(work, "t.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(work, "u"), "--virtual-time-budget=8000",
             "--dump-dom", "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        import html as _h
        return [_h.unescape(x) for x in m.group(1).split("|")] if m and m.group(1).strip() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


HISTORY = json.dumps({"currency": "USD", "days": [{"t": 1750000000 + i * 86400, "c": 100 + i} for i in range(30)],
                      "first": 100, "last": 129, "change": 29, "pct": 29, "high": 129, "low": 100})

print("A ticker that is markup does nothing, anywhere:")
fetch_nothing = ("window.fetch = async (u) => ({ ok: true, json: async () => "
                 "(u.includes('stock-search') ? {} : u.includes('stock-history') ? " + HISTORY +
                 " : { quotes: [] }) });")
got = run(fetch_nothing + HELPERS + MARKETS + CHART, """
  await wait(50);
  const r1 = await window.arcMarket.add(%s);
  window.arcChart.watch(%s);
  await wait(100);
  log.push(String(r1), window.arcChart.watching(), String(!!window.pwned),
           String(document.querySelectorAll("img").length),
           localStorage.getItem("arc.tickers"), localStorage.getItem("arc.chart.symbol"));
""" % (json.dumps(EVIL), json.dumps(EVIL)))
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
else:
    c("  the markets panel will not add it", got[0], "null")
    c("  the chart will not chart it", "IMG" in got[1], False)
    c("  nothing ran", got[2], "false")
    c("  no element was made", got[3], "0")
    c("  and it was not saved for next time", EVIL in (got[4] or "") + (got[5] or ""), False)

print("\n...including one saved by a version from before the check:")
got = run(fetch_nothing + HELPERS + MARKETS + CHART, """
  await wait(100);
  log.push(String(!!window.pwned), String(document.querySelectorAll("img").length),
           window.arcChart.watching(), JSON.stringify(window.arcMarket.list()));
""", storage={"arc.tickers": json.dumps(["AAPL", EVIL]), "arc.chart.symbol": EVIL})
if got is not None:
    c("  nothing ran", got[0], "false")
    c("  no element was made", got[1], "0")
    c("  the saved chart symbol was dropped for a real one", got[2], "AAPL")
    c("  and the saved list keeps only real tickers", got[3], '["AAPL"]')

print("\nA quote the server returns is shown as text, not markup:")
got = run("window.fetch = async (u) => ({ ok: true, json: async () => ({ quotes: [{ symbol: %s, price: 1, pct: 0 }] }) });"
          % json.dumps(EVIL) + HELPERS + MARKETS, """
  await wait(100);
  log.push(String(!!window.pwned), String(document.querySelectorAll("img").length),
           document.querySelector(".st-sym").textContent);
""")
if got is not None:
    c("  nothing ran", got[0], "false")
    c("  no element was made", got[1], "0")
    c.truthy("  it is on screen as the characters it is", got[2].startswith("<IMG"))

print("\nSwitching stocks mid-load shows the stock you switched to:")
slow = """
window.fetch = async (u) => {
  const sym = decodeURIComponent((/symbol=([^&]+)/.exec(u) || [0, ""])[1]);
  if (u.includes("stock-history")) {
    const price = { AAPL: 111, TSLA: 222, NVDA: 333 }[sym] || 1;
    await new Promise(r => setTimeout(r, sym === "AAPL" ? 400 : 20));
    const d = JSON.parse('""" + HISTORY + """'); d.last = price; return { ok: true, json: async () => d };
  }
  return { ok: true, json: async () => ({ quotes: [] }) };
};
window.arcMarket = { list: () => ["NVDA", "AAPL", "TSLA"] };
"""
got = run(slow + HELPERS + CHART, """
  await wait(80);
  window.arcChart.watch("AAPL");
  await wait(50);
  window.arcChart.watch("TSLA");
  await wait(700);
  log.push(window.arcChart.watching(), document.querySelector(".ch-sym").textContent,
           document.querySelector(".ch-price").textContent);
""")
if got is not None:
    c("  watching TSLA", got[0:2], ["TSLA", "TSLA"])
    c("  and showing TSLA's price, not AAPL's late answer", got[2], "222")

print("\nThe smaller ones:")
c.truthy("  chat mode hides the chart and invented panels",
         "html.chat .chart," in page and "html.chat .userpanel," in page)
c.truthy("  resizing and Reset panels include invented panels",
         page.count("allCards().forEach(") == 2)
c.truthy("  /graph with only a range is not 'no such graph'", "const unknown = words.filter(" in body)
c.truthy("  on a phone the tour scrolls its control clear of the card",
         'block: vw <= 640 ? "start" : "nearest"' in body)

c.done()
