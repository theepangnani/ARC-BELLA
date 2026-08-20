# -*- coding: utf-8 -*-
"""The control panel's layout, and the character ARC is asked to have.

Fifteen identical full-width buttons stacked in one column is a wall: nothing
looks more important than anything else, so nothing is findable. They are in
five labelled groups now. What a test can hold onto is that the grouping did
not lose a control or unwire one — every button must still exist exactly once
and still be reached by the script — and that spacing comes from one place
rather than from fifteen separate margins that drift apart.

What it cannot check is whether it looks right. That needs eyes on a screen.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
css = page[:page.index("</style>")]
c = Check()

CONTROLS = ["consentBtn", "personaBtn", "modelBtn", "learnBtn", "voiceGuessBtn",
            "liveScreenBtn", "watchBtn", "cameraBtn", "displayBtn",
            "googleBtn", "googleStatus",
            "nightBtn", "fullscreenBtn", "resetPanelsBtn",
            "micFixBtn", "reconnectBtn"]

print("Regrouping lost nothing — every control is still there, once, and wired:")
for i in CONTROLS:
    once = page.count('id="%s"' % i) == 1
    wired = ('$("%s")' % i) in body or ('getElementById("%s")' % i) in body
    c("  %-16s present once and wired" % i, (once, wired), (True, True))

print("\nThey are in labelled groups rather than one column:")
c("  five groups", page.count('class="cgroup"'), 5)
c("  each with a heading", page.count('class="chead"'), 5)
for head in ["How ARC behaves", "What ARC can see", "Your account",
             "This screen", "If something stops working"]:
    c.truthy("  %r" % head, ">%s<" % head in page)

# The controls that belong together should be inside the same group.
groups = re.findall(r'<div class="cgroup">(.*?)</div>\s*\n\s*\n', page, re.S)
c("  the groups parse out", len(groups), 5)
if len(groups) == 5:
    behave, see, account, screen, fix = groups
    c.truthy("  the safety switch leads the behaviour group", "consentBtn" in behave)
    c.truthy("  the screen-watchers are together",
             "liveScreenBtn" in see and "watchBtn" in see and "cameraBtn" in see)
    c.truthy("  Google's status sits with its button",
             "googleBtn" in account and "googleStatus" in account)
    c.truthy("  the two recovery buttons are together, at the end",
             "micFixBtn" in fix and "reconnectBtn" in fix)

print("\nSpacing is defined once, not fifteen times:")
c.truthy("  the group is a flex column with a gap", ".cgroup {" in css and "gap: 9px" in css)
# Per-button margins were 8px on some and 12px on others, which is what made the
# stack look accidental.
for rule in ["#nightBtn, #voiceGuessBtn, #learnBtn", "#personaBtn", "#modelBtn",
             "#micFixBtn, #reconnectBtn", "#liveScreenBtn, #watchBtn, #displayBtn",
             "#consentBtn", "#googleBtn"]:
    seg = css[css.index(rule + " {"):css.index(rule + " {") + 260]
    c("  %-40s carries no margin of its own" % rule, "margin-top" in seg, False)
c("  buttons are roomier than 9px by 4px", "padding: 9px 4px" in css, False)
c.truthy("  ...they are 11 by 10", "padding: 11px 10px" in css)
c.truthy("  and the odd-width ones are made to line up", ".cgroup > button { width: 100%" in css)

print("\nNothing else in the panel was disturbed:")
for keep in ["Wake word", "Thinking", "ARC voice", "Assistant", "Routines", "Schedules",
             "Core shape", "Depth"]:
    c.truthy("  %r still there" % keep, keep in page)
c.truthy("  the transcript buttons are untouched",
         'id="testBtn"' in page and 'id="clearBtn"' in page and 'id="forgetBtn"' in page)

print("\nThe character it is asked to have:")
c.truthy("  good at everything, and quiet about it",
         "=== GOOD AT EVERYTHING, QUIET ABOUT IT ===" in page)
c.truthy("  humility means calibration, not timidity",
         "Humility here means CALIBRATION, not timidity" in page)
c.truthy("  confidence tracks the evidence", "let your confidence track the evidence"
         in page.lower())
c.truthy("  'I don't know' is allowed to be the whole answer",
         '"I don\'t know" is a complete answer' in page)
c.truthy("  and bluffing is not", "NEVER BLUFF" in page)
c.truthy("  no hedging what it is sure of",
         "Do not qualify what you are sure about" in page)
c.truthy("  corrections taken cleanly, without grovelling", "No grovelling" in page)
c.truthy("  no boasting", "No boasting" in page)
c.truthy("  straight about not being the qualified person",
         "not the qualified person" in page)
# The old character is not replaced, only extended.
c.truthy("  the butler survives", "unflappable British butler" in page)
c.truthy("  so does the warmth", "=== YOU GENUINELY CARE ===" in page)

c.done()
