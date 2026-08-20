# -*- coding: utf-8 -*-
"""tailIsStop: the interim stream is ARC's own voice with, maybe, the user's
word on the end. Only the tail may be a dismissal."""
import re
import sys
import os

STOP = re.compile(
    r"^(stop|stop it|stop talking|stop there|be quiet|quiet|shush|shut up|enough|"
    r"thats enough|that is enough|i got it|ive got it|i get it|got it|understood|"
    r"i know|i know that|nevermind|never mind|forget it|cancel|cancel that|drop it|"
    r"skip it|move on|no more|thats all|that is all|thats enough thanks|thanks|"
    r"thank you|cheers)$", re.I)
FILLER = re.compile(r"\b(bella|arc|sir|please|now|just|hey|ok|okay|yeah|yep|alright|all right)\b", re.I)


def words(s):
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split() if w]


def is_stop(text):
    t = re.sub(r"['’]", "", (text or "").lower())
    t = re.sub(r"[.,!?;:]", " ", t)
    t = FILLER.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t or len(t.split(" ")) > 4:
        return False
    return bool(STOP.match(t))


def echo_of_arc(text, speaking):
    if not speaking:
        return False
    t = re.sub(r"[.,!?;:'’]", "", (text or "").lower()).strip()
    return bool(t) and t in speaking


def tail_is_stop(text, speaking):
    w = words(text)
    if not w:
        return False
    for n in range(1, min(4, len(w)) + 1):
        tail = " ".join(w[len(w) - n:])
        if is_stop(tail) and not echo_of_arc(tail, speaking):
            return True
    return False


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label)


# What the recogniser actually hands back mid-reply: ARC's stream, then the user.
ARC_CHUNK = "im here running locally on your desktop with access to your actual life"

print("User cuts in while ARC is mid-sentence:")
for t in [ARC_CHUNK + " stop",
          ARC_CHUNK + " ok stop",
          ARC_CHUNK + " i got it",
          ARC_CHUNK + " thats enough",
          ARC_CHUNK + " thank you",
          "stop",
          "bella stop"]:
    check("…" + t[-26:], tail_is_stop(t, ARC_CHUNK), True)

print("\nARC's voice alone must never trigger it:")
for t in [ARC_CHUNK,
          ARC_CHUNK[:40],
          "im here running locally on your desktop"]:
    check("…" + t[-26:], tail_is_stop(t, ARC_CHUNK), False)

print("\nARC saying a stop word herself is caught by the echo guard:")
arc2 = "just say stop whenever you want me to stop"
check("ARC's own trailing 'stop'", tail_is_stop(arc2, arc2), False)
# KNOWN LIMIT, and the safe side of the trade: while ARC's own current or
# previous sentence contains the dismissal word, a real one is suppressed too.
# Preferring a missed barge-in over ARC cutting herself off is the right way
# round; tapping the ring always works. Rare in practice — she seldom says
# "stop" mid-answer.
check("user's 'stop' suppressed while ARC's line also says it (accepted)",
      tail_is_stop("you want me to stop talking stop", "you want me to stop talking"), False)
check("…but fires once she has moved on to another sentence",
      tail_is_stop("and the markets panel is live stop", "and the markets panel is live"), True)

print("\nMid-reply chatter that is NOT a dismissal is ignored:")
for t in [ARC_CHUNK + " what about the weather",
          ARC_CHUNK + " can you open spotify",
          ARC_CHUNK + " tell me more about that"]:
    check("…" + t[-26:], tail_is_stop(t, ARC_CHUNK), False)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
