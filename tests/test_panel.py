# -*- coding: utf-8 -*-
"""The control panel's layout, and the character ARC is asked to have.

Fifteen identical full-width buttons stacked in one column is a wall: nothing
looks more important than anything else, so nothing is findable. They are in
eight labelled, foldable groups now. What a test can hold onto is that the grouping did
not lose a control or unwire one — every button must still exist exactly once
and still be reached by the script — that spacing comes from one place rather
than from fifteen separate margins that drift apart, and that each group folds
away and comes back with what was folded remembered.

What it cannot check is whether it looks right. That needs eyes on a screen.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check, prompt_text   # noqa: E402
sandbox()


# ARC's own instructions live server-side now (prompts/main.md), where a
# browser cannot edit them. These checks ask what ARC is TOLD, so they read
# the prompt rather than the page it used to be pasted into.
PROMPT = prompt_text()
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
c("  eight groups", page.count('class="cgroup"'), 8)
c("  each with a heading", page.count('class="chead"'), 8)
for head in ["Language", "How ARC behaves", "What ARC can see", "Your account",
             "This screen", "If something stops working"]:
    c.truthy("  %r" % head, ">%s<" % head in page)

# The controls that belong together should be inside the same group. Keyed by
# the group's name rather than its position, so reordering the panel is not a
# test failure — only losing or misfiling a control is.
groups = dict(re.findall(r'<div class="cgroup" data-group="([a-z]+)">(.*?)\n      </div>',
                         page, re.S))
c("  the groups parse out", len(groups), 8)
if len(groups) == 8:
    behave, see, account, fix = (groups["behaves"], groups["see"],
                                 groups["account"], groups["fix"])
    c.truthy("  the safety switch leads the behaviour group", "consentBtn" in behave)
    c.truthy("  the screen-watchers are together",
             "liveScreenBtn" in see and "watchBtn" in see and "cameraBtn" in see)
    c.truthy("  Google's status sits with its button",
             "googleBtn" in account and "googleStatus" in account)
    c.truthy("  the recovery buttons are together, at the end",
             "micFixBtn" in fix and "reconnectBtn" in fix and "selfHealBtn" in fix)

print("\nSpacing is defined once, not fifteen times:")
c.truthy("  the group is a flex column with a gap", ".cgroup {" in css and "gap: 9px" in css)
# Per-button margins were 8px on some and 12px on others, which is what made the
# stack look accidental.
for rule in ["#nightBtn, #voiceGuessBtn, #learnBtn", "#personaBtn", "#modelBtn",
             "#micFixBtn, #reconnectBtn, #selfHealBtn", "#liveScreenBtn, #watchBtn, #displayBtn",
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
         "=== GOOD AT EVERYTHING, QUIET ABOUT IT ===" in PROMPT)
c.truthy("  humility means calibration, not timidity",
         "Humility here means CALIBRATION, not timidity" in PROMPT)
c.truthy("  confidence tracks the evidence", "let your confidence track the evidence"
         in PROMPT.lower())
c.truthy("  'I don't know' is allowed to be the whole answer",
         '"I don\'t know" is a complete answer' in PROMPT)
c.truthy("  and bluffing is not", "NEVER BLUFF" in PROMPT)
c.truthy("  no hedging what it is sure of",
         "Do not qualify what you are sure about" in PROMPT)
c.truthy("  corrections taken cleanly, without grovelling", "No grovelling" in PROMPT)
c.truthy("  no boasting", "No boasting" in PROMPT)
c.truthy("  straight about not being the qualified person",
         "not the qualified person" in PROMPT)
# The old character is not replaced, only extended.
c.truthy("  the butler survives", "unflappable British butler" in PROMPT)
c.truthy("  so does the warmth", "=== YOU GENUINELY CARE ===" in PROMPT)

print("\nEvery group folds away and comes back:")
c("  eight groups now", page.count('class="cgroup"'), 8)
c("  each is named, so a fold is remembered by what it is",
  page.count('class="cgroup" data-group='), 8)
c.truthy("  the heading is the control", '.cgroup .chead {' in css and "cursor: pointer" in css)
c.truthy("  folding hides the contents, not the heading",
         ".cgroup.folded > *:not(.chead) { display: none; }" in css)
c.truthy("  and the marker flips to a plus", '.cgroup.folded .chead::after { content: "+"; }' in css)
c.truthy("  what is folded is remembered", 'arc.folded' in body)
c.truthy("  reachable by keyboard too", 'head.setAttribute("role", "button")' in body
         and 'e.key === "Enter"' in body)
c.truthy("  and announced as expanded or not", 'aria-expanded' in body)
c.truthy("  nothing is destroyed by folding — the wiring runs regardless",
         "function wireFolding()" in body and "wireFolding();" in body)

print("\nWork, Relax and Business:")
c("  three buttons", page.count('class="modes"'), 1)
for m in ["work", "relax", "business"]:
    c.truthy('  data-mode="%s"' % m, 'data-mode="%s"' % m in page)
    c.truthy("  MODES has %s" % m, "    %s: {" % m in body)
c.truthy("  a mode changes how ARC WRITES, not just the switches",
         "function modeBlock()" in body and
         "personaBlock() + modeBlock() + langBlock() + contextBlock()" in body)
# The base prompt is no longer pasted in here — the page asks for it by name and
# the server puts its own copy first. What the page still contributes is exactly
# the per-turn stuff, mode among it.
c.truthy("  ...and the page asks the server for the rest by name",
         'prompt: "main",' in body)
MODEBLOCK = body[body.index("const MODES = {"):body.index("let modeNow")]
for setting in ["persona:", "night:", "cues:", "think:", "brain:", "prompt:", "hint:"]:
    c("  each of the three sets %-9s" % setting, MODEBLOCK.count(setting), 3)
c.truthy("  pressing the active one turns it off",
         "modeNow === b.dataset.mode ? \"\" : b.dataset.mode" in body)
c.truthy("  ...which the comment explains", "A mode you cannot" in body)
# A mode is about right now. Coming back tomorrow in yesterday's business mode
# would be a small daily annoyance for nothing.
c("  it is NOT remembered across restarts",
  'localStorage.setItem("arc.mode"' in body, False)
c.truthy("  it can be switched by voice too", "[[mode: work]]" in PROMPT)
c.truthy("  ...and the directive is handled",
         r"mode\s*:\s*(work|relax|business" in body)
c.truthy("  the switches are flipped through the real functions",
         "applyPersona(m.persona, false)" in body and "applyThink(m.think)" in body)

print("\nThe auto-clicker has a button now, not just a voice command:")
for i in ["clickBtn", "clickRate", "clickFor", "clickStat"]:
    c.truthy("  %s exists" % i, 'id="%s"' % i in page)
c.truthy("  it counts down first, so the pointer can be placed",
         "Starting in 3" in body)
c.truthy("  the same button stops it", 'clickBtn.textContent = running ? "STOP"' in body)
c.truthy("  and it is loud when running", "#clickBtn.on" in css and "--rose" in css)
c.truthy("  it reflects a run started by voice",
         "clickStatus().then(d => { if (d && d.running)" in body)
c.truthy("  polling stops when the run does", "clearInterval(clickPoll)" in body)

c.done()
