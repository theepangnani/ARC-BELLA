# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The page asks before keeping a memory the server held back.

When a turn read something from outside, /api/chat/remember answers
{held: true} instead of storing (Claude 5's server half). The page shows the
fact with Keep and Skip, and only the Keep click sends confirmed: true. A reply
can never confirm it: nothing in extractDirectives sends that flag.
Also held here: three labels from outside services are escaped before they
reach innerHTML, and a net "saved" below zero is never shown as a loss.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

c = Check()
ARC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
page = open(os.path.join(ARC, "static", "index.html"), encoding="utf-8").read()
run = open(os.path.join(ARC, "run.py"), encoding="utf-8").read()


def fn(name):
    start = page.index("function " + name + "(")
    nxt = re.search(r"\n  (async )?function |\n  const |\n  let ", page[start + 10:])
    return page[start:start + 10 + (nxt.start() if nxt else 4000)]


print("A held memory:")
remember = fn("rememberFact")
c.truthy("  the reply is read, and a hold opens the question", "d.held" in remember and "askToRemember(" in remember)
c.truthy("  rememberFact itself never confirms", "confirmed: true }" not in remember)
ask = fn("askToRemember")
c.truthy("  the question says what and why", "Bella wants to remember" in ask and "Keep it?" in ask)
c.truthy("  Keep and Skip are real buttons", ask.count('document.createElement("button")') == 2)
c.truthy("  confirmed: true is sent only inside the Keep click",
         ask.count("confirmed: true }") == 1
         and ask.index('keep.addEventListener("click"') < ask.index("confirmed: true"))
c.truthy("  the fact goes in as text, never markup", "innerHTML" not in ask)
c("  nowhere else in the page sends confirmed", page.count("confirmed: true }"), 1)
c.truthy("  addEntry hands back its entry so a card can hang off it", "    return d;\n  }" in fn("addEntry"))

print("\nLabels from outside services are text:")
c.truthy("  the weather card's place name is escaped",
         "const label = String(loc.label" in page and "' + loc.label + '" not in page)
c.truthy("  the chart's currency is escaped", "escHtml(d.currency" in page)
c.truthy("  the second screen's ticker loses anything that could be markup",
         'String(s.symbol).replace("-USD","").replace(/[&<>"\']/g,"")' in run)

print("\nSaved by caching:")
c.truthy("  never printed below zero", 'Math.max(0, Number(t.saved) || 0).toFixed(4)' in page)

c.done()
