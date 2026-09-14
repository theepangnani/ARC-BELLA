# -*- coding: utf-8 -*-
"""A draggable card still lets a click reach what was clicked inside it.

Found by the owner on 14 Sep 2026: the stock chart's months (1m 3m 6m 1y), its
‹ › arrows and its stock tabs did nothing. makeDraggable took pointer capture
on every press, so the browser sent the pointerup to the card and the click
landed on the card, not the month. Capture now starts only once a press has
moved far enough to be a drag (or is on the resize grip).

Synthetic dispatchEvent does not reproduce capture retargeting, so this drives
the real draggablePanels code in headless Chrome with real mouse input over the
DevTools protocol.
"""
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import HUD, sandbox, Check   # noqa: E402
sandbox()

c = Check()
src = io.open(HUD, encoding="utf-8").read()
a = src.index("  (function draggablePanels() {")
b = src.index("  })();", a) + len("  })();")
block = src[a:b]

print("In the page's own code:")
c.truthy("  a plain press does not take the pointer",
         re.search(r'addEventListener\("pointerdown"[\s\S]*?\n      \}\);', block) is not None and
         "el.setPointerCapture(pid);\n        }\n" in block)
c.truthy("  a drag takes it once it is a drag",
         re.search(r'dragging = true;\s*el\.classList\.add\("dragging"\);\s*try \{ el\.setPointerCapture\(pid\); \}',
                   block) is not None)

try:
    import websockets
except ImportError:
    websockets = None
roots = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
exe = next((p for p in [os.path.join(r, *parts) for r in roots if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))] if os.path.isfile(p)), None) \
    or shutil.which("google-chrome") or shutil.which("chromium")

PAGE = """<!doctype html><meta charset="utf-8"><style>
#chart{position:fixed;left:100px;top:100px;width:300px;height:200px;background:#123}
.ch-r{display:inline-block;padding:10px;margin:40px 5px;background:#468}
</style><div id="chart"><span class="ch-r" data-r="1m">1m</span><span class="ch-r" data-r="1y">1y</span></div>
<script>
window.hits = [];
document.getElementById('chart').addEventListener('click', e => {
  const r = e.target.closest('.ch-r'); hits.push(r ? r.dataset.r : 'card');
});
%s
</script>"""


async def drive(prof):
    pf = os.path.join(prof, "DevToolsActivePort")
    for _ in range(300):
        if os.path.exists(pf) and open(pf).read().strip():
            break
        await asyncio.sleep(0.1)
    port = open(pf).read().split()[0]
    tab = None
    for _ in range(100):
        tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%s/json" % port))
        tab = next((t for t in tabs if t.get("type") == "page" and "t.html" in t.get("url", "")), None)
        if tab:
            break
        await asyncio.sleep(0.1)
    async with websockets.connect(tab["webSocketDebuggerUrl"]) as ws:
        n = 0

        async def call(method, params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == n:
                    return m.get("result", {})

        async def ev(expr):
            r = await call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            return r.get("result", {}).get("value")

        async def mouse(kind, x, y, buttons=0):
            await call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                                    "buttons": buttons, "clickCount": 1})

        async def click_on(sel):
            x, y = await ev("(() => { const r = document.querySelector('%s').getBoundingClientRect();"
                            " return [r.left + r.width / 2, r.top + r.height / 2]; })()" % sel)
            await ev("hits.length = 0")
            await mouse("mouseMoved", x, y)
            await mouse("mousePressed", x, y, 1)
            await mouse("mouseReleased", x, y)
            await asyncio.sleep(0.2)
            return await ev("hits.slice()")

        for _ in range(50):
            if await ev("document.readyState") == "complete":
                break
            await asyncio.sleep(0.1)
        out = {"click": await click_on('[data-r="1y"]')}
        await ev("hits.length = 0")
        await mouse("mouseMoved", 110, 110)
        await mouse("mousePressed", 110, 110, 1)
        for i in range(1, 11):
            await mouse("mouseMoved", 110 + i * 10, 110 + i * 5, 1)
        await mouse("mouseReleased", 210, 160)
        await asyncio.sleep(0.2)
        out["drag"] = await ev("[document.getElementById('chart').style.left, hits.slice()]")
        out["after"] = await click_on('[data-r="1m"]')
        return out


print("\nWith real mouse input in a browser:")
if not exe or websockets is None:
    print("  NOTE  no Chrome/Edge or no websockets here. NOT CONFIRMED - a gap, not a pass.")
else:
    res = None
    for attempt in range(3):
        work = tempfile.mkdtemp(prefix="arcdrag")
        proc = None
        try:
            io.open(os.path.join(work, "t.html"), "w", encoding="utf-8").write(PAGE % block)
            prof = os.path.join(work, "p")
            proc = subprocess.Popen(
                [exe, "--headless=new", "--no-first-run", "--no-default-browser-check",
                 "--user-data-dir=" + prof, "--remote-debugging-port=0", "--window-size=800,600",
                 "file:///" + os.path.join(work, "t.html").replace("\\", "/")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            res = asyncio.run(asyncio.wait_for(drive(prof), 90))
            break
        except Exception as e:
            print("  (browser run failed, try %d of 3: %s)" % (attempt + 1, type(e).__name__))
        finally:
            if proc:
                proc.kill()
                try:
                    proc.wait(10)
                except Exception:
                    pass
            time.sleep(0.5)
            shutil.rmtree(work, ignore_errors=True)
    c.truthy("  it ran (%s)" % os.path.basename(exe), res is not None)
    if res:
        c("  a click on a month reaches the month, not the card", res["click"], ["1y"])
        c("  a drag still moves the card", res["drag"][0], "200px")
        c("  ...and the click that ends a drag is still swallowed", res["drag"][1], [])
        c("  after a drag, a click on a month still reaches it", res["after"], ["1m"])

c.done()
