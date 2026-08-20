# -*- coding: utf-8 -*-
"""looksLikeEcho — ARC's own voice coming back through the speakers.

Two failures matter here and they pull in opposite directions. Miss an echo and
ARC answers itself, sometimes in a loop. Suppress too eagerly and the person is
simply ignored — no error, no transcript line, nothing to explain it. The
second is worse, because it looks like the microphone is broken.

The rule that shipped is a longest CONSECUTIVE run of words, not a count of
words in common. This suite is the reason: an earlier version scored bag-of-
words overlap and threw away "what did you just say about the dashboard",
which shares seven perfectly ordinary words with a long answer. Both the
behaviour and the shape of the implementation are checked, so a well-meaning
rewrite back to overlap scoring fails here rather than in someone's kitchen.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

page = io.open(HUD, encoding="utf-8").read()
c = Check()

# ---------------------------------------------------------------- the port
# A faithful transcription of echoWords/looksLikeEcho from index.html. Kept
# beside the static checks at the bottom, which fail if the two drift apart.


def echo_words(s):
    s = (s or "").lower()
    s = re.sub(r"['’`]", "", s)          # apostrophes deleted, not split on
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return [w for w in s.split() if w]


def echo_of_arc(text, speaking):
    if not speaking:
        return False
    t = re.sub(r"[.,!?;:'’]", "", (text or "").lower()).strip()
    return bool(t) and t in speaking


def looks_like_echo(text, last, within_window=True, speaking=None):
    if not last or not within_window:
        return False
    words = echo_words(text)
    if len(words) < 4:
        return echo_of_arc(text, last if speaking is None else speaking)
    hay = " " + " ".join(echo_words(last)) + " "
    best = 0
    for i in range(len(words)):
        run = ""
        for j in range(i, len(words)):
            run = (run + " " + words[j]) if run else words[j]
            if (" " + run + " ") not in hay:
                break
            best = max(best, j - i + 1)
    return best >= 5 or best >= -(-len(words) * 6 // 10)     # ceil(n * 0.6)


# The reply ARC actually spoke when this first went wrong.
REPLY = ("every sentence. I'm a butler, not a cheerleader. What sets me apart: other AIs "
         "are cloud services you talk to. I'm here, running locally on your desktop, with "
         "access to your actual life and the ability to control it. That's the real "
         "difference. Show him the right screen - that's my private instance running full "
         "tilt. Markets live, state processing, the whole dashboard. Have him ask me to read "
         "something off your left screen, or ask about what's happening right now. That's "
         "not a trick - that's just what I do every day.")

print("ARC's own tail, as the recogniser mangles it, is caught:")
# Words dropped, punctuation gone: this is what came back through the mic.
c.truthy("  the exact failure from the screenshot",
         looks_like_echo("left screen or ask about what's happening right now "
                         "that's not a trick that's just what I do every day", REPLY))
for t in ["that's my private instance running full tilt",
          "markets live state processing the whole dashboard",
          "running locally on your desktop with access to your actual life",
          "have him ask me to read something off your left screen"]:
    c.truthy("  %r" % t[:50], looks_like_echo(t, REPLY))

print("\nGenuine follow-ups inside the same window still get through:")
for t in ["what did you just say about the dashboard",   # the regression itself
          "what's the weather tomorrow",
          "open spotify and play something calm",
          "add tesla to my markets",
          "read me the last email from mum",
          "set a timer for ten minutes",
          "how long until my next meeting",
          "can you show that on the right screen instead",
          "what did you mean by running locally"]:
    c(" %r" % t[:50], looks_like_echo(t, REPLY), False)

print("\nWhy the run rule and not word overlap:")
words = echo_words("what did you just say about the dashboard")
hay = " " + " ".join(echo_words(REPLY)) + " "
shared = sum(1 for w in words if (" " + w + " ") in hay)
print("      that follow-up shares %d of its %d words with the reply (%.0f%%)"
      % (shared, len(words), 100.0 * shared / len(words)))
c.truthy("  overlap alone would have suppressed it (>=70%)",
         shared / len(words) >= 0.7)
c("  the run rule does not", looks_like_echo("what did you just say about the dashboard",
                                             REPLY), False)

print("\nApostrophes: ARC writes them, the recogniser often doesn't.")
c(" echoWords('that's') == echoWords('thats')",
  echo_words("that's"), echo_words("thats"))
c("  and neither becomes two tokens", echo_words("that's"), ["thats"])
c.truthy("  so a run across one still matches",
         looks_like_echo("thats not a trick thats just what i do every day", REPLY))

print("\nShort utterances fall back to exact matching:")
c("  'stop' is not in the reply", looks_like_echo("stop", REPLY), False)
c.truthy("  'every day' is", looks_like_echo("every day", REPLY))
c("  'yes'", looks_like_echo("yes", REPLY), False)
c("  and with nothing being spoken, nothing is echo",
  looks_like_echo("every day", REPLY, speaking=""), False)

print("\nOutside the 1.6s window nothing is suppressed at all:")
c("  the same verbatim tail gets through",
  looks_like_echo("that's just what I do every day and markets live", REPLY,
                  within_window=False), False)

print("\nAnd the page still implements it that way:")
c.truthy("  looksLikeEcho exists", "function looksLikeEcho(text)" in page)
c.truthy("  matching is by consecutive run", "let best = 0;" in page
         and "if (hay.indexOf(\" \" + run + \" \") < 0) break;" in page)
c.truthy("  with the 5-word / 60% threshold",
         "best >= 5 || best >= Math.ceil(words.length * 0.6)" in page)
c.truthy("  apostrophes are deleted before the punctuation pass",
         'replace(/[\'’`]/g, "")' in page)
c("  bag-of-words scoring is gone", "hits / words.length" in page, False)
c.truthy("  short utterances still defer to echoOfArc", "return echoOfArc(text)" in page)
c.truthy("  the window is still 1.6s", "echoUntil = Date.now() + 1600" in page)

c.done()
