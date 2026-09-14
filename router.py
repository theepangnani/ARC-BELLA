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

# Working the computer by hand. Short, no reasoning words, and the hardest job
# ARC does: find the thing in a screenshot, click it, check it worked, fix what
# did not. "click the search bar" is five words and HARD never saw it, so every
# one of these went to Haiku — and Haiku then drove the whole job. The usage
# file for 11 September 2026 is the evidence: 99 Haiku turns, 320 mouse
# clicks, 294 screenshots, $10.62 on Haiku against $0.35 on Sonnet. The cheap
# brain was not cheap; a model that misreads the screen takes more rounds to
# get there, and every round re-sends the pictures. So it was the expensive
# day AND the unreliable one.
#
# "what type of..." and "open a new tab" land here too. Both are wrong in the
# cheap direction, which is the direction this file has already chosen.
HANDS = re.compile(r"""
    \b(click|double[- ]?click|right[- ]?click|tap\s+on|scroll|drag|type|press|
       select|highlight|fill\s+in|fill\s+out|log\s*in|sign\s*in|
       go\s+to|navigate|download|upload|install|copy|paste|
       button|tab|field|menu|window|
       (on|at)\s+(my|the|this)\s+screen|this\s+page|that\s+page)\b
""", re.I | re.X)

# Being told the last answer was wrong. Short by nature — "no, the other one",
# "that didn't work" — and it means the easy route already failed once. Sending
# the retry to the brain that just got it wrong is how one mistake becomes three.
# Checked before EASY, because a bare "no" is a standing phrase and "no, not
# that one" is not.
CORRECTION = re.compile(r"""
    ^\s*no[,.!]?\s+(not|the\s+other|that'?s|i\s+(said|meant)|wrong|you)\b
    |\b(that'?s|you'?re|you\s+got\s+it|still)\s+(wrong|not\s+(it|right|what))\b
    |\b(didn'?t|doesn'?t|did\s+not|does\s+not|isn'?t)\s+work
    |\b(try\s+again|not\s+that\s+one|wrong\s+(one|window|button|thing)|you\s+missed)\b
""", re.I | re.X)

# Carrying on with whatever came before. These say nothing about difficulty on
# their own — "yes" is easy after "is it raining?" and a thirty-click job after
# "shall I fill in the form?" — so they are judged by the request they continue.
# "carry on" matters most: it is exactly what ARC tells you to say when a job
# ran out of rounds, and on its own it read as two easy words.
FOLLOW = re.compile(r"""
    ^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please|go\s+(on|ahead)|do\s+it|do\s+that|
        carry\s+on|continue|keep\s+going|proceed|next|and\s+then|
        (the\s+)?(first|second|third|last|other|top|bottom)(\s+one)?|that\s+one|this\s+one|
        same\s+again|again|one\s+more|more)\b
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


def _before_last_user(messages) -> list:
    """The history up to and including the PREVIOUS thing the user said.

    Empty when there is none. Everything after it is dropped, so why() on the
    result reads that earlier request as though it were the latest.
    """
    msgs = list(messages or [])
    seen = 0
    for i in range(len(msgs) - 1, -1, -1):
        if (msgs[i] or {}).get("role") == "user":
            seen += 1
            if seen == 2:
                return msgs[:i + 1]
    return []


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
    if CORRECTION.search(text):
        return "smart", "a correction — the last try missed"
    if HANDS.search(text):
        return "smart", "working the screen"
    if CLAUSES.search(text):
        return "smart", "more than one clause"
    if text.count("?") > 1:
        return "smart", "more than one question"
    if words <= 6 and FOLLOW.match(text):
        before = _before_last_user(messages)
        if before:
            # Judged as the request it continues. That one may itself be a
            # follow-up — "yes", "carry on" — so this walks back until it finds
            # something with content, bounded by the history the page sends.
            got, _ = why(before)
            if got == "smart":
                return "smart", "carrying on with a harder request"
    if EASY.match(text):
        return "fast", "a standing phrase"
    if words <= 6:
        return "fast", "%d words, nothing hard in it" % words
    return "smart", "not clearly simple"


# The tools that mean a turn has turned into hands-on work at the screen. Not
# open_app or media: "open spotify" really is one call and done.
SCREEN_TOOLS = frozenset({"screenshot", "mouse_control", "keyboard", "key_macro",
                          "scroll", "hold_key", "auto_click", "focus_window"})

# A job still going on its third round is a job, however short the sentence
# that started it. "look at the calendar, then act on it" is two.
STEP_UP_ROUND = 3


def step_up(round_no: int, called) -> str:
    """Why an Auto turn that started on Haiku should finish on Sonnet, or "".

    why() judges a turn by its first sentence and cannot see where it goes.
    "sort this out" is three words and may be thirty rounds at the screen. So
    the question is asked again while the turn runs, from what it is actually
    DOING — the one thing the words could not tell us. It only ever moves up.
    Nothing here moves a turn down, and nothing moves it to deep.
    """
    hands = sorted(SCREEN_TOOLS.intersection(called or ()))
    if hands:
        return "stepped up: it is working the screen (%s)" % hands[0]
    if round_no >= STEP_UP_ROUND:
        return "stepped up: round %d, this is a job" % round_no
    return ""


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
