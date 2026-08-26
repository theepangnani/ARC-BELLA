#!/usr/bin/env python3
"""Which brain to use, decided per question rather than per person.

The Smart/Fast switch made you the router. It works, and nobody uses it well:
you set it once and forget, so either every "what's the time" costs Sonnet
money, or every hard question gets answered by Haiku. Both are the same
mistake in opposite directions.

The split is worth real money. On ARC's prompt a Sonnet turn is about $0.013
cached and a Haiku turn about $0.004 — roughly three times. Most of what gets
said to a voice assistant is trivially easy: the time, the weather, "stop",
"thanks", "play something". Sending those to the cheap model and keeping the
expensive one for anything with reasoning in it is close to free money.

HOW IT DECIDES, and why it decides this way.

Not with a model — asking Claude which Claude to use costs a call and adds
latency to every single turn, which is exactly what a voice loop cannot spare.
It is a handful of signals over the text, and it is deliberately BIASED TOWARDS
THE EXPENSIVE ONE: the cost of wrongly routing a hard question to Haiku is a
bad answer, and the cost of wrongly routing an easy one to Sonnet is a fraction
of a penny. Those are not comparable, so the rule is "cheap only when clearly
easy" rather than "cheap unless clearly hard".

The user's switch still wins. Auto is a third setting, not a replacement — if
somebody has chosen Smart, they get Smart, and nothing here overrules them.
"""

import os
import re

# Off by default. Something that quietly changes which model answers you should
# be a decision, not a surprise — even a good one.
ENABLED = os.getenv("ARC_AUTO_MODEL", "1").strip().lower() not in ("0", "false", "no", "off")

# Anything at or under this many words is a candidate for the cheap model, as
# long as nothing below vetoes it. Long questions are not always hard, but hard
# questions are almost never short.
SHORT = 12

# Signals that the turn needs the better model. Any one is enough.
#
# The reasoning words are the obvious half. The rest is experience: comparisons
# and trade-offs, arithmetic, anything about code, anything asking for a plan
# or an explanation, and anything conditional ("if X then Y") — all of which
# Haiku will answer confidently and less well.
HARD = re.compile(r"""
    \b(why|how\s+come|explain|compare|versus|vs|trade[- ]?off|
       plan|design|strategy|decide|choose|recommend|
       analyse|analyze|review|debug|refactor|optimi[sz]e|
       calculate|work\s+out|figure\s+out|estimate|forecast|
       code|script|function|error|exception|stack\s*trace|regex|
       summari[sz]e|draft|write\s+me|rewrite|translate|
       pros?\s+and\s+cons?|should\s+i|worth\s+it|what\s+if)\b
""", re.I | re.X)

# Multi-clause questions, which are almost never simple lookups.
CLAUSES = re.compile(r"\b(and\s+then|after\s+that|also|as\s+well\s+as|but\s+if|"
                     r"instead\s+of|rather\s+than|unless|whereas)\b", re.I)

# Things that ARE simple, however they are phrased. Checked before HARD so that
# "what's the weather and what time is it" does not get promoted by "and".
EASY = re.compile(r"""
    ^\s*(hi|hello|hey|thanks?|thank\s+you|cheers|ok|okay|stop|cancel|never\s*mind|
        yes|no|yep|nope|sure|goodnight|good\s+morning|good\s+evening)\b
    |^\s*what(?:'s|\s+is)\s+the\s+(time|date|day|weather|temperature)\b
    |^\s*(what\s+time|what\s+day|what'?s\s+today)\b
    |^\s*(play|pause|resume|skip|next|louder|quieter|volume|mute|unmute)\b
    |^\s*(set\s+a?\s*(timer|alarm)|remind\s+me)\b
    |^\s*(open|launch|close)\s+\w+\s*$
""", re.I | re.X)


def _text_of(messages) -> str:
    """The last thing the user actually said."""
    for m in reversed(messages or []):
        if (m or {}).get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            # A turn with an image attached is a turn about the image.
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            return " ".join(parts)
    return ""


def why(messages, has_image: bool = False, tools_likely: bool = False) -> tuple:
    """(choice, reason). choice is "fast" or "smart"; reason is for the log."""
    text = (_text_of(messages) or "").strip()
    if not text:
        return "smart", "nothing to judge"

    # Looking at a screenshot and deciding whether it is worth interrupting for
    # is a judgement call, and the cheap model is measurably worse at it.
    if has_image:
        return "smart", "there is an image to read"

    words = len(text.split())
    if words > SHORT:
        return "smart", "%d words" % words
    if HARD.search(text):
        return "smart", "reasoning words"
    if CLAUSES.search(text):
        return "smart", "more than one clause"
    if text.count("?") > 1:
        return "smart", "more than one question"
    if EASY.match(text):
        return "fast", "a standing phrase"
    if words <= 6:
        return "fast", "%d words, nothing hard in it" % words
    return "smart", "not clearly simple"


def pick(choice, messages, has_image: bool = False) -> tuple:
    """Resolve the brain for this turn. (choice, reason, auto_used).

    `choice` is whatever the client asked for. Anything other than "auto" is
    honoured exactly — a person who picked Smart gets Smart, and a router that
    overruled them would be a bug however much money it saved.
    """
    asked = str(choice or "").strip().lower()
    if asked != "auto":
        return asked, "", False
    if not ENABLED:
        return "smart", "auto is off", False
    got, reason = why(messages, has_image=has_image)
    return got, reason, True
