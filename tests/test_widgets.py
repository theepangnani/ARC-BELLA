# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Widgets: the screen arranged the way the person wants it.

Asked for as: "a widget system like what phones have, but for panels and other
features, so people can customise their space".

What this holds:
  · A WIDGET IS A PANEL. Added from the gallery, it lands in the same per-person
    store as one Bella makes and passes the same checks: a guest's widgets are
    their own, and nothing the page sends becomes markup or an unlisted `live`.
  · each kind (clock, countdown, note, stock) is built correctly, and bad input
    is refused with a sentence, not a crash
  · a card's ✕ removes exactly that card, by id
  · the built-in cards can be put away and brought back, and that is remembered
    on this device
  · arrange mode: a press on ✕ removes; a click inside a card does nothing else
  · a title that is an HTML tag is shown as text, never parsed
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
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run      # noqa: E402
import session  # noqa: E402
import whose    # noqa: E402
import panels   # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

print("Each kind of widget is a proper panel:")
if panels.PANELS.exists():
    panels.PANELS.unlink()
with whose.acting_as(OWNER):
    c("  a clock", panels.add_widget("clock")["ok"], True)
    got = panels.add_widget("countdown", title="Holiday", date="2026-12-20")
    c("  a countdown", got["ok"], True)
    c.truthy("  ...with an id to remove it by", got["id"])
    c("  a note", panels.add_widget("note", title="Shopping", text="milk " * 60)["ok"], True)
    c("  a stock", panels.add_widget("stock", symbol="nvda")["ok"], True)
    shown = {p["title"]: p for p in panels.panels_for_screen()}
    c("  all four on screen", sorted(shown), ["Clock", "Holiday", "NVDA", "Shopping"])
    c("  the clock ticks", shown["Clock"]["items"][0].get("live"), "clock")
    c("  the countdown counts to its date", shown["Holiday"]["items"][0].get("live"), "countdown:2026-12-20")
    c("  the stock is live", shown["NVDA"]["items"][0].get("live"), "ticker:NVDA")
    note_rows = shown["Shopping"]["items"]
    c.truthy("  a long note is split into whole-word lines",
             all(len(r["value"]) <= panels.MAX_VALUE and not r["value"].startswith(" ") for r in note_rows))
    c("  ...and stops at the row limit", len(note_rows) <= panels.MAX_ITEMS, True)

print("\nTwo widgets in the same millisecond still get their own ids:")
# The laptop's test runs failed on a different check each time: the id was the
# millisecond, and a fast machine added two widgets inside one. Frozen clock
# here, so the collision happens every run rather than on a fast day.
import time as _time   # noqa: E402
_real_time = panels.time.time
panels.time.time = lambda: 1790000000.123
try:
    with whose.acting_as(OWNER):
        a = panels.add_widget("clock", title="Same ms A")
        b = panels.add_widget("clock", title="Same ms B")
    c("  both were made", (a["ok"], b["ok"]), (True, True))
    c("  with different ids", a["id"] != b["id"], True)
    with whose.acting_as(OWNER):
        panels.remove_panel_id(a["id"])
        left = [p["title"] for p in panels.panels_for_screen() if p["title"].startswith("Same ms")]
    c("  removing one leaves the other", left, ["Same ms B"])
    with whose.acting_as(OWNER):
        panels.remove_panel_id(b["id"])
finally:
    panels.time.time = _real_time

print("\nBad input is refused in words:")
with whose.acting_as(OWNER):
    for kind, kw, want in (("rocket", {}, "don't have a widget"),
                           ("countdown", {"date": "next tuesday"}, "Pick a date"),
                           ("note", {"text": "   "}, "Write something"),
                           ("stock", {"symbol": "<img src=x>"}, "stock symbol")):
        r = panels.add_widget(kind, **kw)
        c("  %-9s %s" % (kind, want), (r["ok"], want in r["said"]), (False, True))
    r = panels.add_widget("countdown", title="<b>x</b>", date="2026-01-01")
    c("  angle brackets in a title are not kept as markup",
      "<" in [p for p in panels.panels_for_screen() if "x" in p["title"]][0]["title"], False)

print("\nA guest's widgets are their own:")
with whose.acting_as(GUEST):
    panels.add_widget("clock", title="Guest clock")
    c("  the guest sees only theirs", [p["title"] for p in panels.panels_for_screen()], ["Guest clock"])
with whose.acting_as(OWNER):
    c("  the owner does not see it", "Guest clock" in [p["title"] for p in panels.panels_for_screen()], False)
    theirs = None
with whose.acting_as(GUEST):
    theirs = panels.panels_for_screen()[0]["id"]
with whose.acting_as(OWNER):
    c("  and cannot remove it by id", panels.remove_panel_id(theirs), "That panel is already gone.")
with whose.acting_as(GUEST):
    c("  ...it is still there", len(panels.panels_for_screen()), 1)

print("\nOver HTTP:")
with TestClient(run.app) as client:
    O = {run.COOKIE: session.create(OWNER, "browser")}
    G = {run.COOKIE: session.create(GUEST, "browser")}
    r = client.post("/api/widgets/add", cookies=G, json={"kind": "note", "text": "hello"})
    c("  a guest may add to their own screen", (r.status_code, r.json()["ok"]), (200, True))
    pid = r.json()["id"]
    c("  the owner removing that id changes nothing",
      client.post("/api/widgets/remove", cookies=O, json={"id": pid}).json()["said"], "That panel is already gone.")
    c("  the guest can", client.post("/api/widgets/remove", cookies=G, json={"id": pid}).json()["said"], "Removed.")
    c("  a stranger is turned away", client.post("/api/widgets/add", json={"kind": "clock"}).status_code, 401)
    c("  a body that is not an object is a 400",
      client.post("/api/widgets/add", cookies=O, content="[1]",
                  headers={"Content-Type": "application/json"}).status_code, 400)
    session.revoke_all()

print("\nIn the page:")
c.truthy("  a Widgets button", 'id="widgetsBtn"' in page)
c.truthy("  and a /widgets command", "widgets: { hint:" in page and "function cmdWidgets()" in page)
start = body.index("  const arcWidgets = (() => {")
MODULE = body[start:body.index('if (widgetsBtn) widgetsBtn.addEventListener("click", () => arcWidgets.open());')]
c("  no innerHTML anywhere in the module", "innerHTML" in MODULE, False)
CSS = page[page.index("  /* ---------------- widgets ----------------"):page.index("</style>")]

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]


def run_page(probe, panels_json):
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    html = ("<!doctype html><meta charset='utf-8'><style>:root{--accent:#5fd9ff;--deep:#091320;--line:#12293c;"
            "--ice:#d7eefc;--void:#04070c;--cyan-dim:#1c6d8f;--mono:monospace;--display:sans-serif}"
            ".forecast,.stocks,.userpanel{position:fixed;width:190px;height:80px}"
            ".forecast{top:40px;left:300px}.stocks{top:200px;left:300px}.userpanel{top:360px;left:300px}"
            + CSS + "</style>"
            "<div class='forecast' id='forecast'>weather</div><div class='stocks' id='stocks'>markets</div>"
            "<div class='userpanel' id='userpanel-p1'>mine</div><pre id='probe'></pre><script>"
            "const posted = []; let refreshed = 0; window.arcPanels = { refresh: async () => { refreshed++; } };"
            "window.fetch = async (u, o) => { if (o && o.method === 'POST') { posted.push([u, JSON.parse(o.body)]);"
            " return { ok: true, json: async () => ({ ok: true, said: 'Added.', id: 'p9' }) }; }"
            " return { ok: true, json: async () => (" + json.dumps({"panels": panels_json}) + ") }; };"
            + MODULE +
            "\n(async () => { const log = []; const wait = ms => new Promise(r => setTimeout(r, ms));" + probe +
            " document.getElementById('probe').textContent = log.join('|'); })();</script>")
    import html as _h
    # Up to three tries. Alone this takes a few seconds; in the full suite on a
    # loaded laptop one run went past its timeout (Claude 2, 14 Sep), which is
    # the machine being busy, not the widgets being wrong. A try that finishes
    # without the probe written counts as a failed try too, never as "no
    # browser": that used to pass silently.
    for attempt in range(3):
        work = tempfile.mkdtemp(prefix="arcwidgets")
        try:
            p = os.path.join(work, "t.html")
            io.open(p, "w", encoding="utf-8").write(html)
            out = subprocess.run(
                [exe, "--headless=new", "--no-first-run", "--no-default-browser-check", "--window-size=1200,520",
                 "--user-data-dir=" + os.path.join(work, "u"), "--virtual-time-budget=6000",
                 "--dump-dom", "file:///" + p.replace("\\", "/")],
                capture_output=True, timeout=90, encoding="utf-8", errors="replace").stdout
            m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
            if m and m.group(1).strip():
                return [_h.unescape(x) for x in m.group(1).split("|")]
            print("  (the browser run came back without its probe, try %d of 3)" % (attempt + 1))
        except subprocess.TimeoutExpired:
            print("  (the browser run timed out, try %d of 3)" % (attempt + 1))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return False


EVIL = '<img src=x onerror="window.pwned=1">'
got = run_page("""
  await arcWidgets.open();
  const rows = [...document.querySelectorAll('.wgal .wg-row')].map(r => r.querySelector('.grow').textContent);
  log.push(rows.join(','), String(document.querySelectorAll('.wgal img').length), String(!!window.pwned));
  const box = document.querySelector('.wgal .wg-card').getBoundingClientRect();
  window.__fits = box.top >= 0 && box.bottom <= innerHeight;
  // Hide the weather from the gallery.
  [...document.querySelectorAll('.wgal .wg-row')].find(r => r.textContent.includes('Weather')).querySelector('button').click();
  await wait(20);
  log.push(String(document.getElementById('forecast').classList.contains('widget-off')),
           localStorage.getItem('arc.widgets.hidden'));
  // Add a countdown from its tile.
  const tile = [...document.querySelectorAll('.wgal .wg-tile')].find(t => t.textContent.includes('Countdown'));
  const [title, date] = tile.querySelectorAll('input');
  title.value = 'Holiday'; date.value = '2026-12-20';
  tile.querySelector('button').click();
  await wait(50);
  log.push(JSON.stringify(posted[0]), String(refreshed > 0));
  // Arrange mode: a click inside a card is swallowed; a press on its X removes it.
  arcWidgets.close(); arcWidgets.arrange(true);
  let clicked = false; document.getElementById('stocks').addEventListener('click', () => { clicked = true; });
  document.getElementById('stocks').dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 350, clientY: 240 }));
  log.push(String(clicked), String(document.documentElement.classList.contains('widget-edit')));
  const up = document.getElementById('userpanel-p1').getBoundingClientRect();
  document.getElementById('userpanel-p1').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: up.right - 2, clientY: up.top + 2 }));
  await wait(50);
  log.push(JSON.stringify(posted[1] || null));
  const st = document.getElementById('stocks').getBoundingClientRect();
  document.getElementById('stocks').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: st.right - 2, clientY: st.top + 2 }));
  await wait(20);
  log.push(String(document.getElementById('stocks').classList.contains('widget-off')));
  arcWidgets.arrange(false);
  log.push(String(document.documentElement.classList.contains('widget-edit')), String(window.__fits));
""", [{"id": "p1", "title": EVIL, "items": []}])
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
elif got is False:
    c.truthy("  the browser half ran (three tries, none finished)", False)
else:
    c.truthy("  the gallery lists the built-in cards and the person's own", got[0].startswith("Weather,Markets,"))
    c.truthy("  a panel title that is a tag is shown as text", EVIL in got[0])
    c("  ...not made into an element", got[1:3], ["0", "false"])
    c("  Hide puts the weather away", got[3], "true")
    c("  and it is remembered on this device", got[4], '["forecast"]')
    c("  a countdown is added with what was typed",
      got[5], '["/api/widgets/add",{"kind":"countdown","title":"Holiday","date":"2026-12-20"}]')
    c("  and the cards are refreshed", got[6], "true")
    c("  arrange mode swallows a click inside a card", got[7:9], ["false", "true"])
    c("  the ✕ on the person's card removes it by id", got[9], '["/api/widgets/remove",{"id":"p1"}]')
    c("  the ✕ on a built-in card puts it away", got[10], "true")
    c("  Done leaves arrange mode", got[11], "false")
    # Its padding once sat outside max-height, and on a short window the top of
    # the gallery was cut off rather than scrolling inside it.
    c("  the gallery fits inside the window", got[12], "true")

c.done()
