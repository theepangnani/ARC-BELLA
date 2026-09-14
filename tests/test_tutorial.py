# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The first-visit tour: shown once per person, and then never again.

Asked for as: "an instruction start when it's your first time logging in, and
once they finish the tutorial they won't have to do the tutorial again, so
people know what to do instead of wandering around".

What is worth holding:

  · ONCE PER ACCOUNT, NOT PER BROWSER. The obvious place to remember "seen it"
    is localStorage, and it is wrong both ways: the same person on a phone is a
    stranger again, and two people on one computer share one "seen it".
  · FINISHING AND SKIPPING BOTH COUNT. Somebody who skipped it was not asking
    to be shown it on every visit; they were told how to get it back.
  · A GUEST'S TOUR IS THEIR OWN, and a guest is not shown owner-only steps.
  · EVERY STEP POINTS AT SOMETHING THAT IS ON SCREEN, or is left out.
  · NOTHING TYPED BY A USER BECOMES MARKUP — the wake word appears in it.
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
import run        # noqa: E402
import session    # noqa: E402
import tutorial   # noqa: E402

c = Check()
page = io.open(HUD, encoding="utf-8").read()

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                          "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]

print("On the server, once per account:")
with TestClient(run.app) as client:
    O = {run.COOKIE: session.create("owner@example.com", "browser")}
    O2 = {run.COOKIE: session.create("owner@example.com", "phone")}
    G = {run.COOKIE: session.create("guest@example.com", "browser")}
    c("  a new owner has not seen it", client.get("/api/tutorial", cookies=O).json()["done"], False)
    c("  nor has a new guest", client.get("/api/tutorial", cookies=G).json()["done"], False)
    r = client.post("/api/tutorial", cookies=G, json={"how": "finished"})
    c("  the guest finishes it", (r.status_code, r.json()["done"]), (200, True))
    c("  which does not finish it for the owner",
      client.get("/api/tutorial", cookies=O).json()["done"], False)
    client.post("/api/tutorial", cookies=O, json={"how": "skipped"})
    s = client.get("/api/tutorial", cookies=O).json()
    c("  skipping counts as done", (s["done"], s["how"]), (True, "skipped"))
    c("  and holds on the owner's other device, not just this browser",
      client.get("/api/tutorial", cookies=O2).json()["done"], True)
    c("  an unknown 'how' is recorded as finished, not refused",
      client.post("/api/tutorial", cookies=G, json={"how": "<script>"}).json()["how"], "finished")
    c("  a stranger is turned away", client.get("/api/tutorial").status_code, 401)

    print("\n  a tour that grows shows itself again, once:")
    tutorial.VERSION += 1
    c("  the owner, who saw the old one, sees the new", client.get("/api/tutorial", cookies=O).json()["done"], False)
    client.post("/api/tutorial", cookies=O, json={"how": "finished"})
    c("  and then not again", client.get("/api/tutorial", cookies=O).json()["done"], True)
    tutorial.VERSION -= 1
    session.revoke_all()
on_disk = json.loads(io.open(tutorial.STORE, encoding="utf-8").read())
c("  stored by address", sorted(on_disk), ["guest@example.com", "owner@example.com"])

print("\nIn the page:")
c.truthy("  it starts after boot, once the tier is known",
         "refreshSession().finally(() => setTimeout(() => arcTour.maybeStart(), 900));" in page)
c.truthy("  the server decides, the browser only stands in",
         'fetch("/api/tutorial")' in page and 'localStorage.getItem(LOCAL) === "1"' in page)
c.truthy("  there is a way back in", 'id="tourBtn"' in page and 'tutorial: { hint:' in page)
c.truthy("  and a replay does not re-record anything", 'if (mode === "replay") return;' in page)
c.truthy("  steps are built from text nodes, not markup",
         "document.createTextNode(part)" in page and "q.textContent = part.q" in page)

start = page.index("  const arcTour = (() => {")
end = page.index("window.arcTour = arcTour;") + len("window.arcTour = arcTour;")
MODULE = page[start:end]
css_start = page.index("  /* ---------------- first-visit tour ----------------")
CSS = page[css_start:page.index("</style>", css_start)]


def run_tour(tier, done, probe, wide=True, wake="bella"):
    """The shipped tour module, in a real browser, over a stand-in screen that
    has the same controls it points at. Stubbed: fetch (records what is sent),
    the tier, and the transcript."""
    import shutil
    import subprocess
    import tempfile
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    screen = """
<div class="rail" style="position:absolute;left:10px;top:10px;width:220px">
  <div class="panel"><span id="rState">STANDBY</span><div style="height:120px"></div></div>
  <div class="panel">
    <label><span>Always listening</span><input type="checkbox" id="wakeOn"></label>
    <input id="wakeWord" value="WAKE">
    <div class="cgroup" data-group="mode"><button>Work</button><button>Relax</button></div>
    <select id="neuralVoice"><option>Bella</option></select>
    <select id="langSel"><option>Auto</option></select>
    <button id="googleBtn">Connect Google</button>
    <button id="watchStatsBtn">Arc Watch</button>
  </div>
</div>
<div id="core" style="position:absolute;left:420px;top:200px;width:260px;height:260px;border-radius:50%"></div>
<div style="position:absolute;right:10px;top:10px;width:300px">
  <div id="log" style="height:300px"></div>
  <div class="composer"><input id="typed"></div>
</div>
<div id="stocks" style="position:absolute;left:300px;top:20px;width:200px;height:90px@@HIDE@@"></div>
<pre id="probe"></pre>
""".replace("@@HIDE@@", "" if wide else ";display:none")
    screen = screen.replace('value="WAKE"', 'value="%s"' % wake.replace('"', "&quot;"))
    html = ("<!doctype html><meta charset='utf-8'><style>:root{--accent:#5fd9ff;--deep:#091320;"
            "--line:#12293c;--ice:#d7eefc;--void:#04070c;--cyan-dim:#1c6d8f;--mono:monospace;"
            "--display:sans-serif} body{margin:0;background:#04070c;height:900px}" + CSS + "</style>"
            + screen +
            "<script>"
            "const posted = []; let myTier = " + json.dumps(tier) + "; const said = [];"
            "const addEntry = (a, b, t) => said.push(t);"
            "const $ = id => document.getElementById(id);"
            "const el = { core: $('core'), log: $('log'), typed: $('typed'), wakeOn: $('wakeOn'), wakeWord: $('wakeWord') };"
            "window.fetch = async (u, o) => { if (o && o.method === 'POST') { posted.push(JSON.parse(o.body)); return { ok: true, json: async () => ({}) }; }"
            " return { ok: true, json: async () => ({ done: " + json.dumps(done) + " }) }; };"
            + MODULE +
            "\n(async () => { const log = []; const wait = ms => new Promise(r => setTimeout(r, ms));"
            + probe +
            " document.getElementById('probe').textContent = log.join('|'); })();</script>")
    work = tempfile.mkdtemp(prefix="arctour")
    try:
        p = os.path.join(work, "tour.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run",
             "--window-size=%s" % ("1200,900" if wide else "400,800"),
             "--no-default-browser-check", "--user-data-dir=" + os.path.join(work, "p"),
             "--virtual-time-budget=6000", "--dump-dom", "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        import html as _html
        return [_html.unescape(x) for x in m.group(1).split("|")] if m and m.group(1).strip() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nA first visit, as the owner, clicked all the way through:")
got = run_tour("owner", False, """
  await arcTour.maybeStart();
  log.push(String(arcTour.open), String(arcTour.count), arcTour.step);
  const titles = [arcTour.step];
  const card = document.querySelector('.tour-card').getBoundingClientRect();
  log.push(String(card.left >= 0 && card.right <= innerWidth && card.top >= 0 && card.bottom <= innerHeight));
  document.querySelector('.tour-next').click();
  const hole = document.querySelector('.tour-hole').getBoundingClientRect();
  const core = document.getElementById('core').getBoundingClientRect();
  log.push(arcTour.step, String(Math.abs(hole.left - (core.left - 6)) < 1 && Math.abs(hole.width - (core.width + 12)) < 1));
  for (let i = 0; i < 30 && arcTour.open; i++) { document.querySelector('.tour-next').click(); if (arcTour.open) titles.push(arcTour.step); }
  log.push(titles.join(','), String(arcTour.open), JSON.stringify(posted), localStorage.getItem('arc.tutorial.done'));
""")
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
else:
    c("  it opens", got[0], "true")
    c("  with every step whose control is on screen", got[1], "13")
    c("  starting with a welcome", got[2], "Welcome to ARC")
    c("  the card is on screen", got[3], "true")
    c("  the next step lights the ring itself", got[4:6], ["Talk to me", "true"])
    c.truthy("  it reaches Arc Watch, an owner's step", "Arc Watch" in got[6])
    c.truthy("  and ends on what to try first", got[6].endswith("You're all set"))
    c("  Finish closes it", got[7], "false")
    c("  and tells the server it was finished", got[8], '[{"how":"finished"}]')
    c("  with the browser's own note as the fallback", got[9], "1")

print("\nSomebody who has done it is not shown it again:")
got = run_tour("owner", True, """
  await arcTour.maybeStart();
  log.push(String(arcTour.open), JSON.stringify(posted));
  arcTour.start('replay');
  log.push(String(arcTour.open));
  document.querySelector('.tour').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await wait(20);
  log.push(String(arcTour.open), JSON.stringify(posted), String(said.length));
""")
if got is not None:
    c("  not opened", got[0], "false")
    c("  and nothing recorded", got[1], "[]")
    c("  but it can be asked for", got[2], "true")
    c("  Escape closes it", got[3], "false")
    c("  and a replay records nothing and says nothing", got[4:6], ["[]", "0"])

print("\nSkipping on a first visit counts, and says how to get it back:")
got = run_tour("owner", False, """
  await arcTour.maybeStart();
  document.querySelector('.tour-skip').click();
  await wait(20);
  log.push(String(arcTour.open), JSON.stringify(posted), said.join(' '));
""")
if got is not None:
    c("  closed", got[0], "false")
    c("  recorded as skipped", got[1], '[{"how":"skipped"}]')
    c.truthy("  and the transcript says /tutorial brings it back", "/tutorial" in got[2])

print("\nA guest, on a phone-width screen with the side panels hidden:")
got = run_tour("guest", False, """
  await arcTour.maybeStart();
  const titles = [arcTour.step];
  for (let i = 0; i < 30 && arcTour.open; i++) { document.querySelector('.tour-next').click(); if (arcTour.open) titles.push(arcTour.step); }
  log.push(titles.join(','));
""", wide=False)
if got is not None:
    c("  no owner-only step", "Arc Watch" in got[0], False)
    c("  and no step for a panel that is not on screen", "Panels" in got[0], False)
    c.truthy("  but the ones that matter are there", all(t in got[0] for t in ("Talk to me", "Or type", "You're all set")))

print("\nA wake word that is markup is shown as text:")
got = run_tour("owner", False, """
  await arcTour.maybeStart();
  document.querySelector('.tour-next').click(); document.querySelector('.tour-next').click();
  log.push(String(document.querySelectorAll('.tour img').length), String(!!window.pwned),
           document.getElementById('tourBody').textContent);
""", wake='<img src=x onerror="window.pwned=1">')
if got is not None:
    c("  no element made from it", got[0], "0")
    c("  nothing ran", got[1], "false")
    c.truthy("  it is there as the text it is", "<img src=x" in got[2])

c.done()
