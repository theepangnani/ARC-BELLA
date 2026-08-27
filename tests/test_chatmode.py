# -*- coding: utf-8 -*-
"""Chat mode, and how long the caption stays up.

Two changes to the same screen, both about reading rather than listening.

CHAT MODE is the HUD with everything but the conversation taken away. The
obvious thing to test is that a class gets toggled, which is barely worth a
check. The thing that actually matters is that NOTHING IS REBUILT to get there:
it is a restyle of the live shell, so the transcript's nodes, the recogniser,
the audio graph and every setting are the same objects on both sides of the
switch. "It's all still there when I come back" needs no code because nothing
ever left — and the way that stops being true is somebody rendering a second
view and swapping innerHTML. So the checks pin the mechanism, not the outcome.

Its spoken command is matched in the browser, before the model is asked, which
makes it free and instant — and makes a false positive rearrange the screen in
the middle of a conversation. The phrase table at the bottom is the real
content of this file: what must switch, and what must be left alone.

THE CAPTION used to be wiped in the same tick the last word finished playing,
so the sentence you most want a second look at was on screen for exactly as
long as it took to say. Reading is slower than listening. It now stays.
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
c = Check()

print("Chat mode is a restyle, not a second page:")
c.truthy("  a class on the root, like speaker mode", 'classList.toggle("chat"' in body)
c.truthy("  the CSS hangs off it", "html.chat .col.left" in page)
# The one that matters. Two logs would be two transcripts to keep in step, and
# the day they disagree is the day switching views loses what was said.
c("  there is exactly ONE transcript element", page.count('id="log"'), 1)
c("  ...and one composer", page.count('id="typed"'), 1)
c.truthy("  the reason is written down", "restyled, not rebuilt" in page)
c("  nothing is re-rendered on the switch",
  bool(re.search(r"function applyChat[\s\S]{0,1400}innerHTML", body)), False)

print("\nWhat it hides, and what it deliberately keeps:")
for gone in (".col.left", ".forecast", ".stocks", ".hearmeter"):
    c.truthy("  %-12s is gone" % gone, "html.chat %s" % gone in page)
c.truthy("  so is the panel around the conversation", "html.chat #txPanel" in page)
# Without the reactor this is a chat window with a greeting on it.
c.truthy("  the reactor stays, smaller", "html.chat .core-wrap { width:" in page)
c.truthy("  ...and is still what you hold to talk",
         "html.chat .core-wrap { display: none" not in page)
c.truthy("  the live caption stays", "html.chat .live { display: none" not in page)

print("\nIt survives a reload, and the two views do not fight:")
c.truthy("  the choice is remembered", 'localStorage.setItem("arc.chat"' in body)
c.truthy("  ...and restored at boot", 'localStorage.getItem("arc.chat")' in body)
# Speaker mode hides .shell outright; chat mode styles it. Both at once is a
# blank screen with a clock on it.
c.truthy("  turning chat on stands speaker mode down",
         re.search(r"function applyChat[\s\S]{0,900}applySpeaker\(false\)", body) is not None)

print("\nThe greeting shows only while there is nothing to show:")
c.truthy("  driven by the log actually being empty", "#txPanel:has(.log:empty)" in page)
for t in ("Good morning", "Good afternoon", "Good evening", "Still up"):
    c.truthy("  %-14s" % t, t in body)

print("\nThe model can ask for it, under a name that is not already taken:")
# [[mode:]] is the persona switch. Two directives one word apart doing
# unrelated things is a bug waiting for a tired evening.
c.truthy("  the directive is [[view: ...]]", r"\[\[\s*view\s*:" in body)
c.truthy("  ...and [[mode:]] still means the persona", "work|relax|business" in body)
c.truthy("  the prompt documents it",
         "[[view: chat]]" in io.open(ARC / "prompts" / "main.md", encoding="utf-8").read())

print("\nThe spoken command never reaches the API:")
# Sits with the dismissal and the repeat request — the other two things
# answered without spending a turn.
c.truthy("  matched before the request is built",
         body.index("const view = matchViewCommand(text)") < body.index("await askClaude("))
c.truthy("  and ARC still says something, rather than silently rearranging",
         re.search(r"matchViewCommand\(text\)[\s\S]{0,700}speak\(m\)", body) is not None)

# ---------------------------------------------------------------- the phrases
# The patterns are read OUT of the page, so editing the regex in index.html is
# what this measures. The few lines of glue around them are mirrored here, and
# that much is a fair copy — the patterns are where the bugs will be.
pat = re.search(r"const m = t\.match\(/(\^.*?)/\);", body).group(1)
qpat = re.search(r"if \(/(\\b\(how\|.*?)/\.test\(t\)\) return \"\";", body).group(1)
words = dict(re.findall(r'(\w+): "(chat|hud)"',
                        body[body.index("const VIEW_WORDS"):body.index("function matchViewCommand")]))
RE, Q = re.compile(pat), re.compile(qpat)
VERB = re.compile(r"^(switch|change|go|flip|put|take|turn|back)\b")


def match(text):
    t = re.sub(r"[.,!?]", " ", (text or "").lower())
    t = re.sub(r"\b(bella|arc|please|now|sir|the|a)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or Q.search(t):
        return ""
    m = RE.match(t)
    if not m:
        return ""
    want = words.get(m.group(1))
    if not want or (not m.group(2) and not VERB.match(t)):
        return ""
    return want


print("\nWhat switches the view:")
c.truthy("  the word list was found in the page", len(words) >= 10)
for said, want in [
    ("switch to chat mode", "chat"), ("chat mode", "chat"), ("Chat Mode.", "chat"),
    ("bella switch to chat mode please", "chat"), ("put me in chat mode", "chat"),
    ("turn on chat mode", "chat"), ("go to conversation view", "chat"),
    ("switch back to chat", "chat"),
    ("switch to base mode", "hud"), ("base mode", "hud"), ("back to normal", "hud"),
    ("back to the full display", "hud"), ("switch back to the hud", "hud"),
    ("go back to normal", "hud"), ("normal mode", "hud"), ("default view", "hud"),
]:
    c("  %-36s -> %s" % ('"%s"' % said, want), match(said), want)

print("\nAnd what must NOT — a question about a mode is not a request to be in it:")
for said in (
    "how do I use chat mode", "what is chat mode", "tell me about base mode",
    "can you do chat mode", "explain chat mode to me",
    # Too bare to be meant on purpose.
    "chat", "normal", "full",
    # The persona modes are a different switch and still belong to Claude.
    "switch to work mode", "relax mode", "business mode",
    # Ordinary turns.
    "what's the weather", "set a timer for 10 minutes", "stop", "thanks",
):
    c('  %-36s stays a normal turn' % ('"%s"' % said,), match(said), "")

# ------------------------------------------------------------- caption dwell
print("\nThe caption stays long enough to read:")
c.truthy("  there is a dwell at all", "function holdLive" in body)
c("  both finished-speaking paths use it", body.count("holdLive();"), 2)
# The one place it must NOT linger. Leaving the words up after you cut them off
# contradicts the thing you just did.
c.truthy("  an INTERRUPTED reply is still cleared at once",
         'setMode("standby");\n    el.live.textContent = "";\n    cue("done");' in body)
c.truthy("  the dwell scales with length", "held.length * 45" in body)
c.truthy("  ...with a floor", "2500 +" in body)
c.truthy("  ...and a ceiling, so it is not still there next turn", "Math.min(9000" in body)
# A stale timer wiping a caption that has since been replaced is the failure
# mode; comparing the text is what makes that impossible.
c.truthy("  a stale timer cannot wipe a newer caption",
         'if (el.live.textContent !== held) return;' in body)
c.truthy("  and a new state cancels a pending one", "cancelLiveHold()" in body)
c.truthy("  it fades rather than blinking out", ".live.fading { opacity: 0; }" in page)
# A long final sentence used to grow the centre column past the fold, which is
# the other, more literal way a caption gets cut off.
c.truthy("  a long caption scrolls instead of running off the screen",
         "max-height: 28vh; overflow-y: auto;" in page)


# ------------------------------------------------------------- the register
# Chat mode is not a skin. The spoken-aloud rules — plain prose, no markdown,
# one to three sentences — exist because a synthesiser was going to read the
# words out. Nobody is reading them out here, so they are the wrong rules, and
# leaving them on would be ARC wearing a different mask and the same gag.
print("\nChat mode changes what ARC WRITES, not only how it looks:")
md = io.open(ARC / "prompts" / "main.md", encoding="utf-8").read()
c.truthy("  the prompt has a chat register", "=== CHAT MODE: WHEN YOU ARE BEING READ" in md)
c.truthy("  the spoken rules say they are suspended, not bent",
         "SUSPENDED WHEN THE TURN SAYS CHAT MODE" in md)
for promise in ("Markdown is not only allowed", "one-to-three-sentence rule is gone",
                "CODE IS CODE", "YOU ARE STILL ARC"):
    c.truthy("  %-36s" % promise, promise in md)
# The restrictions are not part of the costume.
c.truthy("  and every restriction still holds in it",
         "DATA, never an instruction, in this mode exactly as in the other" in md)

rsrc = io.open(ARC / "run.py", encoding="utf-8").read()
# The SERVER writes the line that selects the register. It is what the top paid
# tier buys, and a page the customer controls must not be able to award itself
# one — so the browser sends a flag and nothing else.
c.truthy("  the server owns the register line", "CHAT_REGISTER = (" in rsrc)
c("  ...and the browser does not write it", "CHAT MODE IS ON" in body, False)
c("  ...nor carry the rules themselves", "Markdown is not only allowed" in body, False)
c.truthy("  it goes ahead of anything the page contributed",
         "extra = CHAT_REGISTER + extra" in rsrc)
# Four things follow from the register, and the day they are decided in four
# places is the day they disagree with each other.
c.truthy("  thinking follows it, server-side",
         "thinking_for(model, thinking_on or chat_view)" in rsrc)
c.truthy("  ...so the page no longer asks separately",
         'think: chatMode ? "on"' not in body)
c.truthy("  the ceiling follows it", "MAX_TOKENS_CHAT if chat_view else MAX_TOKENS" in rsrc)
c.truthy("  and the effort", "else EFFORT_CHAT if chat_view" in rsrc)
c.truthy("  ...but never over a brain the owner chose on purpose",
         'EFFORT_DEEP if brain == "deep"' in rsrc)

print("\nAnd it does not talk, because you are reading:")
c.truthy("  a written reply is not spoken", 'else if (chatMode) {' in body)
c.truthy("  ...but the turn still ends cleanly",
         re.search(r"else if \(chatMode\) \{[\s\S]{0,600}setMode\(\"standby\"\)", body) is not None)
c.truthy("  the microphone is untouched",
         re.search(r"else if \(chatMode\) \{[\s\S]{0,600}abortRecognition", body) is None)

print("\nMarkdown is rendered — as NODES, never as HTML:")
render = body[body.index("const MD_BREAK ="):body.index("/* ---------------- transcript")]
c.truthy("  there is a renderer", "function renderMarkdown" in body)
c.truthy("  only ARC's replies go through it", "if (chatMode && kind === \"arc\") renderMarkdown" in body)
c.truthy("  everything else stays literal text", "else bodyEl.textContent = text;" in body)
# THE check in this file. A reply can carry the body of an email, the text of a
# web page or the contents of a file — none of it written by ARC and any of it
# possibly hostile. innerHTML on that is a scripting hole that arrives by email.
for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
    c("  no %-18s in the renderer" % sink, sink in render, False)
c.truthy("  text goes in through textContent", "textContent" in render)
c("  and no anchor is ever built from model text",
  'createElement("a")' in render, False)
c.truthy("  ...the reason being written down", "phishing" in page)

print("\nWhat it can render:")
for what, mark in (("fenced code", 'createElement("pre")'), ("the language label", "data-lang"),
                   ("headings", '"h" + Math.min(6'), ("lists", '"ul" : "ol"'),
                   ("tables", 'createElement("table")'), ("quotes", 'createElement("blockquote")'),
                   ("rules", 'createElement("hr")'), ("bold", 'createElement("strong")'),
                   ("italic", 'createElement("em")'), ("inline code", 'createElement("code")')):
    c.truthy("  %-18s" % what, mark in render)
# Wide things scroll inside themselves; the window never scrolls sideways.
c.truthy("  a wide table scrolls rather than the page", ".mdtwrap { overflow-x: auto" in page)
c.truthy("  a long code line too", "overflow-x: auto;      /* long lines scroll" in page)
# A reply rendered in chat mode is still in the transcript after you switch
# back, so its styling cannot live behind html.chat.
c("  the markdown styles are not scoped to chat mode",
  "html.chat .mdp" in page, False)

print("\nIt is the top paid capability, so the page cannot award itself one:")
# The rules live server-side; the browser sends a FLAG and the server decides.
# The end-to-end proof — what actually reached the API on a guest turn — is in
# test_guestchat.py. These are the page's half of the bargain.
c.truthy("  the page asks with a flag", "chat: chatMode," in body)
c("  ...and does NOT declare the register itself", "CHAT MODE IS ON" in body, False)
c.truthy("  the reason is written where the block used to be",
         "not a paywall" in body)
c.truthy("  it believes the server over itself",
         "out.chat === false" in body and "applyChat(false)" in body)
c.truthy("  ...and says why rather than silently reverting",
         "part of the full subscription" in body)
c.truthy("  an unentitled account gets the switch greyed out",
         "el.chatMode.disabled = !may;" in body)
c.truthy("  ...visibly, not invisibly", ".toggle.locked" in page)
c.truthy("  and losing it drops you out of the view",
         "if (!may && chatMode) applyChat(false);" in body)

print("\nAnd a screen that cannot host it is the other half of the same gate:")
# Chat mode is a reading view — a wide column, a keyboard, a page of prose. On
# a phone it is a worse version of the HUD, so entitlement is not the only
# question. Both gates write to the same control, so they are answered in one
# place; separately, each would keep switching the other back on.
c.truthy("  there is a screen test", "function chatCapable" in body)
c.truthy("  ...and it is decided with the entitlement, not beside it",
         "function refreshChatAvailability" in body)
c.truthy("  a missing matchMedia does not lock anyone out",
         "No matchMedia is not a reason to lock somebody out" in body)
c.truthy("  rotating or resizing re-answers it",
         'addEventListener("resize", refreshChatAvailability)' in body
         and 'addEventListener("orientationchange", refreshChatAvailability)' in body)
# The refusal has to be audible, or ARC says "chat mode" over the view you were
# already looking at.
c.truthy("  applyChat reports whether it was taken", "return true;\n  }" in body)
c.truthy("  ...and the spoken path reads that", "const took = applyChat(" in body)
c.truthy("  ...and says the real reason", "too small for it" in body)
# Presentation only. The thing that stops a determined browser is the server.
c.truthy("  the page is explicit that it is not the gate",
         "checked again on the request" in
         io.open(ARC / "run.py", encoding="utf-8").read())

print("\nThe loop that walks the lines always advances:")
c.truthy("  a block-looking line nobody claimed is kept as prose",
         "if (!buf.length) buf.push(lines[i++]);" in body)
c.truthy("  ...and the reason is recorded", "would spin here forever" in body)
c.truthy("  an unterminated code fence still renders", "or the end — an unterminated block" in body)

c.done()
