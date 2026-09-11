# -*- coding: utf-8 -*-
"""Saying you typed something is a claim about where it went.

The keyboard tool types blind — keystrokes go to whatever window is in front —
and it used to REPORT blind: "Typed 42 character(s)", true about the keystrokes
and silent about where they landed. The model read that as success and told the
user "I've typed it into the chat box", about a box it had never checked.
Sometimes nothing had arrived anywhere it meant.

Two separate claims were being made without being known:

  · WHERE. The result now names the window that received the keystrokes.
  · SENT. Typing into a chat box is not sending it. The result now says
    whether Enter was pressed, and the rulebook says never to claim "sent"
    unless it was.

And one case is refused BEFORE typing rather than reported after: when you talk
to Bella in her own window, her own window is the one in front — so text meant
for another app went into Bella's own message box, and a trailing Enter would
have had her send a message to herself.

EVERY KEYSTROKE IN THIS FILE IS INTERCEPTED. pc._tap_vk and pc._tap_unicode are
replaced before anything is typed, and the test fails loudly if they were not:
a keyboard test that pressed real keys would type into whatever window was in
front on the machine running it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pc      # noqa: E402
import retry   # noqa: E402
import prompt  # noqa: E402

c = Check()

pressed = []
pc._tap_vk = lambda vk: pressed.append(("vk", vk))
pc._tap_unicode = lambda ch: pressed.append(("ch", ch))
pc.IS_WIN = True
assert pc._tap_vk.__name__ == "<lambda>", "real keystrokes are NOT intercepted"

focus = {"hwnd": 0, "title": ""}
pc._focused = lambda: (focus["hwnd"], focus["title"])


def at(title, hwnd=42):
    focus.update(hwnd=hwnd, title=title)
    pressed.clear()


def refused(**kw):
    try:
        pc.keyboard(**kw)
    except RuntimeError as e:
        return str(e)
    return ""


print("Bella's own window is refused before anything is typed:")
at("ARC — Ambient Response Core - Google Chrome")
said = refused(text="hello Sam")
c.truthy("  typing is refused", said)
c("  and NOTHING was pressed", pressed, [])
c.truthy("  it says why", "ARC's own page" in said)
c.truthy("  and what to do instead", "focus_window" in said)
c.truthy("  it says plainly that nothing was typed", said.startswith("Nothing was typed"))
at("ARC — Ambient Response Core - Google Chrome")
c.truthy("  Enter is refused too — it would have Bella message herself",
         refused(key="enter"))
c("  ...and not pressed", pressed, [])
at("ARC — Ambient Response Core")                  # the installed app, no browser suffix
c.truthy("  the installed app is recognised as well", refused(text="x"))
at("ARC — Ambient Response Core - Google Chrome")
# The Windows key goes to the shell whatever is in front.
c.truthy("  the Windows key is still allowed", pc.keyboard(key="win").startswith("Pressed win"))
c("  ...and pressed", pressed, [("vk", 0x5B)])

print("\nNothing in front means nowhere for it to go:")
at("", hwnd=0)
c.truthy("  refused", "no window has focus" in refused(text="hello"))
c("  nothing pressed", pressed, [])

print("\nTyping into a real window says where, and whether it was sent:")
at("Untitled - Notepad")
out = pc.keyboard(text="hello")
print("    " + out)
c.truthy("  it names the window", "“Untitled - Notepad”" in out)
c.truthy("  says Enter was NOT pressed", "Enter was NOT pressed" in out)
c.truthy("  so nothing has been sent", "nothing has been sent yet" in out)
c.truthy("  and that it has not checked the text arrived", "not that they appeared" in out)
c("  five characters, no Enter", pressed, [("ch", ch) for ch in "hello"])

at("Telegram")
out = pc.keyboard(text="on my way\n")
c.truthy("  a trailing newline IS Enter, and it says so", "Enter was pressed at the end" in out)
c("  ...and pressed it last", pressed[-1], ("vk", 0x0D))

at("Telegram")
out = pc.keyboard(text="line one\nline two")
c.truthy("  Enter part-way is not the same as sent", "part-way through" in out)
c.truthy("  ...and it says what was left unsent", "after the last line break has not been sent" in out)

at("Telegram")
out = pc.keyboard(key="enter")
c("  a key press names the window too", out, "Pressed enter in the window “Telegram”.")

print("\nWhat it refuses without looking at focus at all:")
at("", hwnd=0)
c.truthy("  an unknown key is answered, not raised", "don't know the key" in pc.keyboard(key="frobnicate"))
c.truthy("  no text and no key", "Give me text" in pc.keyboard())
c.truthy("  far too much text", "too much text" in pc.keyboard(text="x" * 2001))
c("  and none of those pressed anything", pressed, [])

print("\nA very long window title is trimmed, not pasted whole:")
at("A" * 500)
c.truthy("  kept to a sensible length", len(pc.keyboard(text="x")) < 500)

print("\nThrough the tool door the model actually uses:")
at("ARC — Ambient Response Core - Google Chrome")
out, failed = pc.run_tool("keyboard", {"text": "hi"}, local=True)
c("  the refusal counts as a FAILURE, not a success", failed, True)
c("  still nothing pressed", pressed, [])
# Refusals are permanent, and typing is never repeated — see retry.py.
c("  the retry layer reads it as permanent", retry.classify(out, failed), "permanent")
c("  and would never repeat a keystroke", retry.may_retry("keyboard", "TimeoutError", True), False)
at("Untitled - Notepad")
out, failed = pc.run_tool("keyboard", {"text": "hi"}, local=True)
c("  a real window is a success", failed, False)

print("\nThe model is told what the result does and does not mean:")
rule = prompt.base("main")
c.truthy("  there is a section for it", "=== TYPING AND MESSAGING ON THE DESKTOP ===" in rule)
c.truthy("  typing is blind", "The keyboard tool types blind" in rule)
c.truthy("  no target: look, and ASK if there's more than one place",
         "NO TARGET GIVEN" in rule and "ASK which one before typing" in rule)
c.truthy("  target not on screen: say so", "THE TARGET ISN'T ON SCREEN" in rule)
c.truthy("  look after typing, and report a failure as one",
         "AFTER TYPING, LOOK" in rule and "Never claim success you have not seen" in rule)
c.truthy("  never claim 'into' an app the tool did not name",
         "an app the tool did not name" in rule)
c.truthy("  typing is not sending", "TYPING IS NOT SENDING" in rule)
c.truthy("  messages said mid-task are done in order", "MESSAGES THAT ARRIVE WHILE YOU ARE WORKING" in rule)
c.truthy("  on the phone, say it needs the computer", "ON THE PHONE" in rule)
c.truthy("  secrets: confirm, never repeat, never remember", "PASSWORDS, KEYS, CARD NUMBERS" in rule
         and "never put it in a reply, a note or a memory" in rule)
# The honest limit, which the user asked about and deserves to hear.
c.truthy("  and honest that it has already been said", "it has already been said to you" in rule)

print("\nA message said mid-task waits its turn instead of being dropped:")
import io, re   # noqa: E402
from _harness import HUD   # noqa: E402
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c("  the old 'one moment' refusal is gone",
  "One moment — still working on the last one." in body, False)
c.truthy("  it is held instead", "if (inFlight) { holdTurn(text, alts); return; }" in body)
c.truthy("  the queue lives on state, which setMode can reach during boot", "turnQueue: []," in body)
c.truthy("  it is bounded", "TURN_QUEUE_MAX = 3" in body)
c.truthy("  released once the reply has been SPOKEN", "speak(out.text, releaseTurn);" in body)
c.truthy("  ...or straight away when nothing was spoken",
         'if (state.mode === "standby") setTimeout(releaseTurn, 0);' in body)
c.truthy("  and by standby as the safety net for an interrupted reply",
         'state.mode === "standby" && state.turnQueue.length' in body)
c.truthy("  never twice for the same ending", "state.turnReleasing" in body)
c.truthy("  a replayed message is not shown twice", "if (!replay) addEntry(\"you\"" in body)
c.truthy("  a mid-task 'stop' clears what is waiting", "state.turnQueue.length = 0;" in body)
c.truthy("  ...and is honest that the running one can't be recalled",
         "can't be pulled back mid-way" in body)

c.done()
