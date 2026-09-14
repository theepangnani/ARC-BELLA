# -*- coding: utf-8 -*-
"""The HUD does not spend the machine's CPU on a ring nobody is talking to.

Measured on 13 Sep 2026: the ARC desktop window, left open, used a third of all
eight cores — nearly three cores' worth, on the machine Bella's own server and
speech recognition run on. Two causes, found by switching each effect off and
measuring (three runs each, interleaved):

  · the core was redrawn every display frame, forever, including a --amp value
    on the root that four blurred and shadowed layers read
  · a drop-shadow over the whole core — bars that change shape every frame, so
    the blur is redrawn every frame — was the single largest cost

So, held here:
  · idle, the core is drawn at IDLE_FRAME_MS; busy (listening, thinking,
    speaking, touched, turning) at the display rate
  · the idle breath keeps its pace at the lower rate (scaled by elapsed time)
  · --amp and the hearing meter are written only when they change
  · the glow is on only while the core is lively, and lingers briefly after
  · the loop schedules itself exactly once per frame
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import HUD, sandbox, Check   # noqa: E402
sandbox()     # nothing here touches data; test_meta holds every suite to it

c = Check()
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]

print("In the source:")
loop = body[body.index("function renderLoop(now)"):body.index("/* ---------------- 2D / 3D projection")]
c("  the loop schedules itself exactly once", loop.count("requestAnimationFrame(renderLoop)"), 1)
c.truthy("  ...at the top, so an early return still keeps it running",
         loop.index("requestAnimationFrame(renderLoop)") < loop.index("if (!busy && now - lastDrawn < IDLE_FRAME_MS) return;"))
c.truthy("  idle is drawn at a lower rate", "const IDLE_FRAME_MS = 1000 / 20;" in body)
c.truthy("  the idle breath is scaled by elapsed time", "idlePhase += 0.028 * step;" in body)
c.truthy("  --amp is written only when it changes",
         "if (ampNow !== ampWritten)" in body and 'setProperty("--amp", ampNow)' in body)
c("  and never unconditionally per frame", 'setProperty("--amp", shown.toFixed(3))' in body, False)
css = page[:page.index("</style>")]
c.truthy("  the core's glow belongs to .lively only",
         re.search(r"\.core-wrap\.lively\s*\{\s*filter:\s*drop-shadow", css) is not None)
c("  and not to the resting core", re.search(r"\.core-wrap\s*\{[^}]*drop-shadow", css), None)
c.truthy("  the breathing disc pauses at rest", ".core-wrap:not(.lively) .glow-disc { animation-play-state: paused; }" in css)

HARNESS_START = body.index("  let synthPhase = 0;")
HARNESS_END = body.index("/* ---------------- 2D / 3D projection")
LOOP = body[HARNESS_START:HARNESS_END]
PTR = body[body.index("  let spin = 0;"):body.index("  function setBar(i, v) {")]


def run(probe):
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    stub = """
<div id="core" class="core-wrap"></div><div id="hearFill"></div><span id="rLvl"></span><pre id="probe"></pre>
<script>
const $ = id => document.getElementById(id);
const el = { core: $("core"), rLvl: $("rLvl") };
const state = { mode: "standby", holding: false, booted: true, analyser: null };
const geo = []; const BAR_COUNT = 4; let depthMode = "2d"; let spinMul = 1;
const boost = () => 1; let drawn = 0; function setBar() {} function samplePitch() {} function drawWire() {}
// Frames are fed by hand, sixty to a simulated second: headless Chrome under a
// virtual time budget does not fire animation frames on its own schedule.
window.requestAnimationFrame = () => 0;
let T = 1000;
function frames(seconds) {
  const before = window.__drawn || 0, calls = window.__calls || 0;
  for (let i = 0; i < Math.round(seconds * 60); i++) { T += 1000 / 60; renderLoop(T); }
  return [(window.__calls || 0) - calls, (window.__drawn || 0) - before];
}
"""
    wrapped = LOOP.replace("function renderLoop(now) {", "function renderLoop(now) {\n    window.__calls = (window.__calls || 0) + 1;") \
                  .replace("let amp = 0;", "let amp = 0; window.__drawn = (window.__drawn || 0) + 1;", 1)
    html = ("<!doctype html><meta charset='utf-8'>" + stub + PTR + wrapped +
            "\n(async () => { const log = []; const wait = ms => new Promise(r => setTimeout(r, ms));" +
            probe + " document.getElementById('probe').textContent = log.join('|'); })();</script>")
    work = tempfile.mkdtemp(prefix="arcspeed")
    try:
        p = os.path.join(work, "t.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(work, "u"), "--virtual-time-budget=6000",
             "--dump-dom", "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        return m.group(1).split("|") if m and m.group(1).strip() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nIn a real browser:")
got = run("""
  frames(3);                                    // settle past the start-up glow
  const [idleCalls, idleDrawn] = frames(1);
  log.push(String(idleCalls), String(idleDrawn), String(el.core.classList.contains("lively")));
  state.mode = "speaking";
  const [busyCalls, busyDrawn] = frames(1);
  log.push(String(busyDrawn), String(busyCalls), String(el.core.classList.contains("lively")));
  state.mode = "standby";
  frames(1);
  log.push(String(el.core.classList.contains("lively")));
  frames(2);
  log.push(String(el.core.classList.contains("lively")));
""")
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
else:
    idle_calls, idle_drawn = int(got[0]), int(got[1])
    busy_drawn, busy_calls = int(got[3]), int(got[4])
    print("    idle: %d frames offered, %d drawn · speaking: %d offered, %d drawn"
          % (idle_calls, idle_drawn, busy_calls, busy_drawn))
    c.truthy("  idle, most frames are skipped (about 20 a second drawn)", 12 <= idle_drawn <= 28)
    c("  and the glow is off at rest", got[2], "false")
    c.truthy("  speaking, every frame is drawn", busy_drawn >= busy_calls - 2 and busy_drawn > idle_drawn * 2)
    c("  with the glow on", got[5], "true")
    c("  the glow lingers a moment after it goes quiet", got[6], "true")
    c("  ...and then rests", got[7], "false")

c.done()
