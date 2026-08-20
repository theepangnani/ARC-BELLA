# -*- coding: utf-8 -*-
"""Speaker mode: the phone put down and used like a smart speaker.

There is no browser here, so this checks the things that are checkable without
one — that every id is both declared and used, that nothing is read before its
`let` has run (the failure mode that kills the whole script on a phone and
leaves a blank page), and that the audio lever is actually applied on BOTH
speech paths rather than only the one that was easy to find.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def before(a, b):
    """Does `a` first appear before `b` in the script?"""
    ia, ib = body.find(a), body.find(b)
    return ia != -1 and ib != -1 and ia < ib


print("Every element it touches exists in the markup:")
IDS = ["speakerMode", "speakerFace", "spkClock", "spkDate", "spkState",
       "spkAlarm", "spkLine", "spkExit", "spkWake"]
for i in IDS:
    truthy('  markup has id="%s"' % i, ('id="%s"' % i) in page)
    truthy("  and el.%-12s is wired" % i, ("$(\"%s\")" % i) in body)

print("\nNothing is read before its declaration has run.")
# These are the ones that can be reached by the alarm poll, which now starts at
# page load rather than at boot. A temporal-dead-zone ReferenceError here does
# not degrade gracefully -- it kills the whole script.
truthy("speakerMode declared before addEntry reads it",
       before("let speakerMode = false;", "if (speakerMode && el.spkLine"))
truthy("lastAlarmInfo declared before showNextAlarm writes it",
       before("let lastAlarmInfo = null;", "lastAlarmInfo = next;"))
truthy("both are declared before the poll that can reach them starts",
       before("let speakerMode = false;", "\n  startAlarmPoll();") and
       before("let lastAlarmInfo = null;", "\n  startAlarmPoll();"))
check("speakerMode declared exactly once",
      len(re.findall(r"\blet speakerMode\b", body)), 1)
check("lastAlarmInfo declared exactly once",
      len(re.findall(r"\blet lastAlarmInfo\b", body)), 1)

print("\n1. The screen must not sleep -- the whole feature.")
truthy("requests a screen wake lock", 'navigator.wakeLock.request("screen")' in body)
truthy("feature-detects it first", '"wakeLock" in navigator' in body)
truthy("re-takes it when the tab comes back",
       "visibilitychange" in body and "takeWakeLock()" in body)
truthy("notices the system revoking it",
       'wakeLock.addEventListener("release"' in body)
truthy("releases it on the way out", "dropWakeLock()" in body)
truthy("and says which state it is in", "screen may sleep" in body
       and "screen held awake" in body)

print("\n2. It must be listening -- nobody is holding the phone.")
truthy("arms the wake word on entry", "el.wakeOn.checked = true" in body)
truthy("tap anywhere is a fallback way in", 'el.speakerFace.addEventListener("click"' in body)
truthy("...which does not fire while ARC is mid-reply",
       'state.mode === "speaking" || state.mode === "thinking"' in body)
truthy("exit button stops the tap handler firing too",
       "e.stopPropagation()" in body)

print("\n3. Audio must come out of the loudspeaker, not the earpiece.")
# An open mic is what puts Android into call-audio mode. abort() drops the
# capture at once; stop() finishes processing and can hold the session open
# across the start of playback -- which is the whole bug.
n = len(re.findall(r"else if \(speakerMode\) abortRecognition\(\);", body))
check("the mic is hard-released on BOTH speech paths", n, 2)
truthy("neural path", before("speakRemote", "else if (speakerMode) abortRecognition();"))
truthy("full volume on the element", "if (speakerMode) audio.volume = 1;" in body)
check("the ordinary (non-speaker) path is unchanged",
      len(re.findall(r"else stopRecognition\(\);", body)), 2)

print("\nThe face shows what is legible from across a room:")
truthy("clock", "spkClock" in body and "toLocaleTimeString" in body)
truthy("date", "spkDate" in body)
truthy("listening state", "spkState" in body)
truthy("the next alarm", "el.spkAlarm" in body and "lastAlarmInfo" in body)
truthy("the last thing said", "el.spkLine" in body)
truthy("system notices are kept OFF it", 'kind !== "sys"' in body)
truthy("ticks once a second", "setInterval(paintSpeakerFace, 1000)" in body)
truthy("and stops ticking when off", "clearInterval(speakerTick)" in body)

print("\nIt persists, because the point is a phone left on a nightstand:")
truthy("saved", 'localStorage.setItem("arc.speaker"' in body)
truthy("restored", 'localStorage.getItem("arc.speaker")' in body)

print("\nCSS: the hand-held HUD is hidden, the face is not:")
truthy("shell hidden in speaker mode", "html.speaker .shell," in page)
truthy("face hidden by default", ".speakerface { display: none; }" in page)
truthy("face shown in speaker mode", "html.speaker .speakerface {" in page)
truthy("clock scales to the screen", "clamp(56px, 22vw, 150px)" in page)
truthy("digits don't jitter each second", "tabular-nums" in page)
truthy("exit is a real thumb target", "min-height: 44px" in page)
truthy("the alarm bar still outranks it",
       page.index("z-index: 9999") > 0 and "z-index: 30;" in page)

print("\nAnd it does not disturb what was already there:")
truthy("room mode untouched", "function roomMode()" in body)
truthy("night mode untouched", "function applyNight(on)" in body)
truthy("input level fix still in place", "let lvlShown = 0, lvlLast = -1;" in body)
truthy("alarm poll still starts at load", "\n  startAlarmPoll();" in body)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
