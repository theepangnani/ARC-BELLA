# -*- coding: utf-8 -*-
"""Phone compatibility: Samsung, iPhone, Pixel, Oppo, OnePlus.

Every one of these regresses silently on a desktop, which is where the HUD is
always looked at. The split that actually matters is not vendor by vendor:
Samsung, Pixel, Oppo and OnePlus all ship Chromium, so they behave alike, and
iPhone is the real outlier because every browser on iOS is WebKit underneath.
So the checks are engine-shaped and hardware-shaped (notch, gesture bar,
soft keyboard), not brand-shaped.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

page = io.open(HUD, encoding="utf-8").read()
head = page[:page.index("<style")] if "<style" in page else page[:4000]
css = page[:page.index("</style>")]
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


print("Cutouts: notch, Dynamic Island, punch-hole (all of these phones).")
truthy("viewport-fit=cover — without it safe insets are always 0",
       "viewport-fit=cover" in head)
# The alarm bar is pinned to the top edge, which IS the cutout on every one of
# these handsets. Stop being unreachable is not an acceptable alarm failure.
i = css.index(".alarmbar {")
seg = css[i:i + 700]
truthy("the alarm bar pads for the top inset", "env(safe-area-inset-top)" in seg)
truthy("...and for both side insets",
       "env(safe-area-inset-left)" in seg and "env(safe-area-inset-right)" in seg)
for sel, need_bottom in [(".speakerface {", True), (".shell {", True), (".boot {", True)]:
    j = css.index(sel)
    s2 = css[j:j + 800]
    truthy("%-16s pads for the notch" % sel, "env(safe-area-inset-top)" in s2)
    if need_bottom:
        truthy("%-16s clears the gesture bar" % sel,
               "env(safe-area-inset-bottom)" in s2)
truthy("the speaker Exit button clears the cutout",
       "top: calc(14px + env(safe-area-inset-top))" in css)

print("\nViewport height: iOS Safari and Samsung Internet hide chrome dynamically.")
# 100vh is the height with browser chrome hidden, so the bottom of the page sits
# off-screen until you scroll. dvh is what is actually visible.
n_vh = len(re.findall(r"min-height: 100vh;", css))
n_dvh = len(re.findall(r"min-height: 100dvh;", css))
check("every min-height:100vh has a dvh companion", n_dvh, n_vh)
truthy("the transcript's max-height too", "calc(100dvh - 84px)" in css)
truthy("vh is still declared first, as the old-WebKit fallback",
       css.index("min-height: 100vh;") < css.index("min-height: 100dvh;"))

print("\nSoft keyboards: iOS zooms in on a small field and never zooms back.")
truthy("fields are lifted to 16px on touch devices", "@media (pointer: coarse)" in css)
i = css.index("@media (pointer: coarse)")
seg = css[i:i + 400]
truthy("  covers text inputs", 'input[type="text"]' in seg)
truthy("  covers selects", "select" in seg)
truthy("  covers textareas (routines, schedules)", "textarea" in seg)
truthy("  at exactly the 16px threshold", "font-size: 16px" in seg)

print("\nAndroid gestures and OEM browsers.")
truthy("pull-to-refresh cannot reload mid-conversation",
       "overscroll-behavior-y: contain" in css)
truthy("no grey tap-flash on every touch",
       "-webkit-tap-highlight-color: transparent" in css)
truthy("text is not inflated on rotation", "text-size-adjust: 100%" in css)
truthy("dark scheme declared so Samsung Internet does not re-tint it",
       "color-scheme: dark" in css)
truthy("...and declared in the head too", 'name="color-scheme"' in head)

print("\niPhone and iPad specifically.")
truthy("IS_IOS exists at all", "const IS_IOS" in body)
# iPadOS 13+ reports "Macintosh" on purpose. A plain UA test therefore calls an
# iPad a desktop and gives it the desktop microphone path, which holds
# getUserMedia open and leaves the speech recogniser deaf.
truthy("iPadOS is caught despite reporting as Macintosh",
       "/Macintosh/" in body and "maxTouchPoints" in body)
truthy("...and a real Mac is not caught (needs touch points > 1)",
       "(navigator.maxTouchPoints || 0) > 1" in body)
truthy("IS_MOBILE includes it", "IS_MOBILE = /Mobi|Android|iPhone|iPad|iPod/i.test("
       in body and "|| IS_IOS" in body)
truthy("home-screen install works on iOS",
       'name="apple-mobile-web-app-capable"' in head)
truthy("...and the status bar is styled for a dark, edge-to-edge app",
       "black-translucent" in head)
# "Use Chrome" is useless advice on iOS -- Chrome there IS Safari's engine.
n_ios_msg = body.count("IS_IOS")
truthy("iOS gets honest advice rather than 'install Chrome' (%d places)" % n_ios_msg,
       n_ios_msg >= 3)
truthy("  ...and says switching browsers won't help",
       "switching browsers won't" in body or "switching browsers won" in body)
truthy("  ...and points at typing, which does work",
       "Type" in body and "still" in body)

print("\nPrefixed APIs, for older WebKit on all of them.")
truthy("AudioContext is prefixed-tolerant",
       "window.AudioContext || window.webkitAudioContext" in body)
check("  in both the mic path and the alarm path",
      body.count("window.AudioContext || window.webkitAudioContext"), 2)
truthy("vibration is feature-detected (absent on iOS)", "if (navigator.vibrate)" in body)
truthy("wake lock is feature-detected (absent before Safari 16.4)",
       '"wakeLock" in navigator' in body)

print("\nNothing desktop-only was broken doing this:")
truthy("the coarse-pointer rule is scoped to touch, not global",
       "@media (pointer: coarse)" in css and
       css.count("font-size: 16px") <= 2)
truthy("the desktop grid is untouched", ".shell {" in css and "grid-template-columns" in css)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
