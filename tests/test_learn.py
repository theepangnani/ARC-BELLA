# -*- coding: utf-8 -*-
"""Lesson mode and the whiteboard, and the markdown strip that broke them.

The interesting one is last. Replies used to have markdown stripped the moment
they arrived, before directives were pulled out — so `[[remember: …]]` survived
but anything the model wrapped in emphasis or backticks did not, and a board or
a lesson written with any formatting at all silently lost it. The strip now
happens inside extractDirectives, after the markers have been taken out, which
is the only order where both survive.

Everything here is a presence check against the shipped page rather than a
behavioural one: this is a browser feature with no server side, and the point
is that a refactor cannot quietly delete half of it.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

page = io.open(HUD, encoding="utf-8").read()
c = Check()

print("The lesson engine is all still there:")
for name in ["function learnBlock", "function recordProgress", "function setLearner",
             "function currentLearner", "function saveLearn", "function showLearn",
             "const LESSON_TRACKS", "const LEARN_RULES"]:
    c.truthy("  %s" % name, name in page)

print("\n...and reachable from the interface:")
for el in ['id="learnBtn"', 'id="rLearn"']:
    c.truthy("  %s" % el, el in page)
c.truthy("  the lesson block reaches the model with the alternatives",
         "learnBlock() + altBlock" in page)

print("\nThe whiteboard:")
c.truthy("  addBoard exists", "function addBoard" in page)

print("\nMarkdown is stripped AFTER directives are extracted, not before:")
c.truthy("  the reply goes to extractDirectives raw", "extractDirectives(raw)" in page)
c.truthy("  ...untrimmed of formatting first", 'const raw = (data.reply || "").trim();' in page)
c("  the old pre-strip is gone from the chat path",
  'const raw = (data.reply || "").replace(/[*_#`]/g' in page, False)
# Watch mode DOES strip on arrival, at index.html:5628, and should: it is a
# different prompt with no directives in it, and a one-line glance that is
# spoken or discarded. Named here so the next reader doesn't "fix" it.
c("  watch mode still strips on arrival (one line, no directives)",
  page.count('(data.reply || "").replace(/[*_#`]/g, "").trim()'), 1)
c.truthy("  and the strip now lives inside extractDirectives",
         'return { text: out.replace(/[*_#`]/g, "").trim(), silent };' in page)

c.done()
