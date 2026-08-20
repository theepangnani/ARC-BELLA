# -*- coding: utf-8 -*-
"""The INPUT LVL readout, simulated frame by frame.

The old readout was driven by the visualiser's `amp`, which is only an input
level in one of its four branches. This ports the NEW expressions out of
renderLoop and drives them the way the animation loop would, so the four
failure modes are checked rather than reasoned about.
"""
import io
import os
import math
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


class Meter:
    """The three lines that now decide what INPUT LVL says."""

    def __init__(self):
        self.shown = 0.0
        self.last = -1
        self.writes = 0
        self.text = "0%"

    def frame(self, amp, speaking, has_analyser, mic_activity):
        in_level = 0.0 if speaking else (amp if has_analyser else mic_activity)
        self.shown += (in_level - self.shown) * (0.5 if in_level > self.shown else 0.08)
        if self.shown < 0.005:
            self.shown = 0.0
        pct = round(min(1.0, self.shown) * 100)
        if pct != self.last:
            self.last = pct
            self.writes += 1
            self.text = "%d%%" % pct
        return self.text


def run(m, frames, **kw):
    for _ in range(frames):
        m.frame(**kw)
    return m.text


print("BUG 1: it showed ARC's OWN voice while she was speaking.")
m = Meter()
# amp during speech comes from the TTS output tap — loud, and nothing to do
# with the microphone.
check("ARC talking loudly -> reads 0%", run(m, 120, amp=0.9, speaking=True,
                                            has_analyser=True, mic_activity=0.0), "0%")

print("\nBUG 2: browser synthesis fabricated a sine wave with no audio behind it.")
m = Meter()
out = set()
for i in range(200):
    synth = min(1.0, abs(math.sin(i * 0.13)) * 1.5)   # the old fake cadence
    out.add(m.frame(amp=synth, speaking=True, has_analyser=False, mic_activity=0.0))
check("no longer dances during synthesis", out, {"0%"})

print("\nBUG 3: with no analyser it never rested at zero — it wobbled forever.")
m = Meter()
out = set()
for i in range(300):
    # The OLD idle amp: 0.02 + 0.02*sin(...) — always non-zero, always moving.
    old_idle = 0.02 + 0.02 * (0.5 + 0.5 * math.sin(i * 0.028 * 0.9))
    out.add(m.frame(amp=old_idle, speaking=False, has_analyser=False, mic_activity=0.0))
check("a silent room reads a clean 0%", out, {"0%"})

print("\n  (and the mobile path still works — it is fed by the recogniser)")
m = Meter()
run(m, 40, amp=0.0, speaking=False, has_analyser=False, mic_activity=0.6)
truthy("recogniser activity registers", int(m.text.rstrip("%")) > 30)

print("\nBUG 4: it was rewritten on every animation frame.")
m = Meter()
run(m, 600, amp=0.42, speaking=False, has_analyser=True, mic_activity=0.0)
truthy("600 frames caused far fewer than 600 DOM writes (%d)" % m.writes,
       m.writes < 60)
before = m.writes
run(m, 300, amp=0.42, speaking=False, has_analyser=True, mic_activity=0.0)
check("a steady level writes nothing at all", m.writes, before)

print("\nIt still tracks a real microphone:")
m = Meter()
quiet = run(m, 200, amp=0.02, speaking=False, has_analyser=True, mic_activity=0.0)
loud = run(m, 40, amp=0.80, speaking=False, has_analyser=True, mic_activity=0.0)
truthy("quiet is low (%s)" % quiet, int(quiet.rstrip("%")) <= 5)
truthy("speaking is high (%s)" % loud, int(loud.rstrip("%")) >= 60)

print("\nMeter ballistics — fast attack, slow release:")
m = Meter()
m.frame(amp=0.0, speaking=False, has_analyser=True, mic_activity=0.0)
run(m, 3, amp=1.0, speaking=False, has_analyser=True, mic_activity=0.0)
rise = m.shown
truthy("3 frames of speech gets most of the way up (%.2f)" % rise, rise > 0.8)
run(m, 3, amp=0.0, speaking=False, has_analyser=True, mic_activity=0.0)
truthy("3 frames of silence does NOT slam to zero (%.2f)" % m.shown, m.shown > 0.5)
run(m, 80, amp=0.0, speaking=False, has_analyser=True, mic_activity=0.0)
check("but it does settle to a true zero", m.text, "0%")

print("\nAnd the fix is actually in the page:")
page = io.open(HUD, encoding="utf-8").read()
truthy("readout no longer driven by the visualiser amp",
       "el.rLvl.textContent = Math.round(amp * 100)" not in page)
truthy("one figure feeds both the bar and the number",
       page.count("const inLevel = speaking ? 0 :") == 1)
truthy("smoothing state declared", "let lvlShown = 0, lvlLast = -1;" in page)
truthy("DOM write is guarded", "if (pct !== lvlLast)" in page)
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
check("rLvl is written in exactly one place", body.count("el.rLvl.textContent"), 1)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
