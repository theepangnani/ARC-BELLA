# -*- coding: utf-8 -*-
"""Port of isStopPhrase / echoOfArc, checked against what the recogniser
actually hands back — no punctuation, filler words, mangled casing."""
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


def is_stop(text):
    t = (text or "").lower()
    t = re.sub(r"['’]", "", t)
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


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label + ("" if good else "  (got %r)" % (got,)))


print("SHOULD stop:")
for p in ["stop", "Stop.", "stop it", "STOP TALKING", "ok stop", "okay stop bella",
          "that's enough", "thats enough", "I got it", "i've got it", "got it",
          "alright I got it", "never mind", "nevermind", "cancel that", "shut up",
          "be quiet", "enough", "thank you", "thanks bella", "understood",
          "move on", "skip it", "drop it", "I know", "hey stop"]:
    check(repr(p), is_stop(p), True)

print("\nShould NOT stop (real requests that merely contain a stop word):")
for p in ["stop the timer", "can you stop the music", "don't stop",
          "what time does the shop stop serving", "I got it from the shop",
          "thanks for that, now what's the weather", "tell me more",
          "no", "yes", "what's next", "cancel my three o'clock meeting",
          "I know a good place for dinner", "stop by the shop on the way",
          "okay", "yeah", "alright"]:
    check(repr(p), is_stop(p), False)

print("\nEcho guard — ARC's own voice must never trigger it:")
arc = "it is twenty two degrees say stop whenever you want me to stop talking"
check("'stop' while ARC is saying it", echo_of_arc("stop", arc), True)
check("'stop talking' while ARC says it", echo_of_arc("stop talking", arc), True)
check("'got it' NOT in ARC's line", echo_of_arc("got it", arc), False)
check("no speech in progress", echo_of_arc("stop", ""), False)

print("\nCombined gate (fires only when both agree):")
for phrase, arc_text, want in [
    ("stop", "the weather today is fine", True),
    ("stop", "just say stop if you want me to be quiet", False),
    ("got it", "just say stop if you want me to be quiet", True),
    ("what's the weather", "the weather today is fine", False),
]:
    check("%-18s vs ARC saying %-42s" % (repr(phrase), repr(arc_text[:40])),
          is_stop(phrase) and not echo_of_arc(phrase, arc_text), want)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
