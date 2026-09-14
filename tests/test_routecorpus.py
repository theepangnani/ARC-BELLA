# -*- coding: utf-8 -*-
"""A hundred-odd real things people say, and the brain each one should get.

test_brain.py pins the router's RULES one at a time: this word is hard, that
phrase is a correction. What it cannot show is how the rules add up on the
sentences people actually say to a voice assistant, where a noun can look like
a verb ("the window cleaner") and an easy question can be long-winded. So this
is the other half: a corpus, written from what gets said rather than from the
regexes, with the answer a person would give and a one-line reason.

The reasons matter more than the answers. The rule the router lives by is
"cheap only when clearly easy" (router.py explains why: a hard question sent to
Haiku is a bad answer, an easy one sent to Sonnet is a fraction of a penny). So
where a case could go either way, the answer here is whichever that rule picks,
and the reason says so.

KNOWN MISSES. Where router.why() gets a case wrong today, the case is marked
MISS instead of being quietly bent to match. A miss is checked the other way
round: it passes while the router still gets it wrong and FAILS once the router
gets it right. That failure is good news. It means a fix landed, so take the
MISS mark off that case and it becomes an ordinary guard. Router changes belong
to whoever owns router.py (Claude 3 on 14 Sep 2026), not to this file.

OWNER'S CALLS. Three misses were over-priced rather than wrong (a long polite
weather question and two hockey scores going to Sonnet). Asked whether to send
them to the cheap brain, the owner said "they all work" (14 Sep 2026), so they
are pinned as SMART, as the router answers them. Moving them to FAST is now a
decision to take back to the owner, not a fix.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()   # router reads no data, but this is also what makes ARC importable

import router   # noqa: E402

FAST, SMART = "fast", "smart"
MISS = "known miss"


def convo(*said):
    """Several things said in a row, each answered, as the page sends them."""
    out = []
    for i, t in enumerate(said):
        out.append({"role": "user", "content": t})
        if i < len(said) - 1:
            out.append({"role": "assistant", "content": "Okay."})
    return out


# (what was said, the brain it should get, why) — plus MISS where the router
# gets it wrong today. A str is one thing said; a tuple is a conversation, judged
# on its last line; a dict is passed to why() as it stands.
CASES = [
    # ---------------------------------------------------------- time and date
    ("what time is it", FAST, "the time is a lookup"),
    ("what's the date today", FAST, "so is the date"),
    ("what day is it", FAST, "and the day"),
    ("what's the time in Tokyo", FAST, "a time zone is still a lookup"),

    # ---------------------------------------------------------------- weather
    ("what's the weather like", FAST, "the weather is a lookup"),
    ("is it going to rain tomorrow", FAST, "a forecast is a lookup too"),
    ("what's the temperature outside", FAST, "one number"),
    ("how cold is it in Toronto", FAST, "'how' asking for a number is not 'how come'"),
    ("weather in London this weekend", FAST, "no verb at all, still a lookup"),
    ("hey can you tell me what the weather is going to be like on saturday afternoon",
     SMART, "the owner's call (14 Sep 2026, 'they all work'): long polite questions may"
     " cost Sonnet; was a MISS for FAST"),

    # ------------------------------------------------------------------ music
    ("play some jazz", FAST, "one media call"),
    ("pause", FAST, "a standing phrase"),
    ("skip this song", FAST, "one media call"),
    ("turn it up", FAST, "volume"),
    ("volume to 30 percent", FAST, "volume with a number"),
    ("play the next episode of my podcast", FAST, "'play' leads, so it is media however long"),
    ("put on something relaxing", FAST, "choosing a playlist is not reasoning"),

    # ----------------------------------------------------- stopping and thanks
    ("stop", FAST, "a standing phrase"),
    ("thanks", FAST, "a standing phrase"),
    ("thank you so much", FAST, "gratitude, at length"),
    ("never mind", FAST, "a standing phrase"),
    ("cancel that", FAST, "cancelling is one call"),
    ("goodnight Bella", FAST, "a greeting with her name on it"),
    ("hey Bella", FAST, "a greeting"),
    ("okay thanks", FAST, "an ending"),

    # ------------------------------------------------ alarms, timers, reminders
    ("set a timer for 5 minutes", FAST, "one call"),
    ("set an alarm for 7 am", FAST, "one call"),
    ("set an alarm for 6:30 tomorrow morning", FAST,
     "one call; 'set an' is a standing phrase as much as 'set a'"),
    ("remind me to call mum at 5", FAST, "one reminder"),
    ("remind me to take the bins out tonight", FAST, "one reminder, a few more words"),
    ("wake me up at 6 tomorrow", FAST, "an alarm in other words"),
    ("cancel my 7 o'clock alarm", FAST, "one call"),
    ("what alarms do I have", FAST, "reading a list"),

    # ------------------------------------------------------ apps and lookups
    ("open spotify", FAST, "open_app is one call and done"),
    ("launch calculator", FAST, "same"),
    ("open google chrome", FAST, "a two-word app name is still one call"),
    ("what's on my calendar today", FAST, "reading the calendar"),
    ("do I have any meetings tomorrow", FAST, "reading the calendar"),
    ("any new emails", FAST, "reading mail"),
    ("how much is bitcoin right now", FAST, "a price"),
    ("convert 50 dollars to euros", FAST, "a conversion"),
    ("what's 15 percent of 80", FAST, "one sum Haiku does not get wrong"),
    ("what's the name of that song", FAST, "a lookup"),
    ("who won the leafs game last night", SMART,
     "the owner's call (14 Sep 2026, 'they all work'): left on Sonnet; was a MISS for FAST"),

    # ------------------------------------------------------- work at the screen
    # The expensive day: 11 Sep 2026, 99 Haiku turns and 320 clicks.
    ("click the blue button", SMART, "finding a thing in a screenshot"),
    ("scroll down", SMART, "at the screen"),
    ("scroll down a bit more", SMART, "at the screen"),
    ("type my email address in", SMART, "typing into whatever has focus"),
    ("press enter", SMART, "a key into a live window"),
    ("open a new tab", SMART, "tabs are browser work"),
    ("close this window", SMART, "which window is a judgement"),
    ("fill in the form on this page", SMART, "many rounds at the screen"),
    ("log in to my bank", SMART, "a form, and a careful one"),
    ("go to amazon and find a phone charger", SMART, "navigating, then reading"),
    ("select all the text", SMART, "at the screen"),
    ("copy that link", SMART, "finding the link first"),
    ("download the pdf", SMART, "finding it, then saving"),
    ("what's on my screen", SMART, "reading a screenshot"),
    ("read this page to me", SMART, "reading a screenshot"),
    ("hit the submit button", SMART, "a button"),
    ("drag it to the trash", SMART, "dragging"),
    ("right click on the desktop", SMART, "a click"),
    ("move the mouse to the top left", SMART, "the mouse is the screen"),
    ("find the settings icon", SMART,
     "looking for something on screen"),
    ("tick the checkbox", SMART,
     "a click by another name"),

    # ------------------------------------------------------------ corrections
    ("no, the other one", SMART, "the last try missed"),
    ("that didn't work", SMART, "the last try missed"),
    ("try again", SMART, "the last try missed"),
    ("wrong window", SMART, "the last try missed"),
    ("you missed it", SMART, "said outright"),
    ("not that one", SMART, "the last try missed"),
    ("that's not what I asked", SMART, "the last answer was wrong"),
    ("nope, wrong one", SMART, "the last try missed"),
    ("no I meant the calendar app", SMART, "a correction without a comma"),
    ("still not working", SMART,
     "a correction: the last try missed"),
    ("it's still broken", SMART, "a correction: the last try missed"),

    # ------------------------------------------------------------- follow-ups
    (("click the blue button", "yes"), SMART, "yes to a click is the click"),
    (("fill in the form on this page", "carry on"), SMART, "what ARC says to say when a job runs out"),
    (("explain how caching works", "go on"), SMART, "more of a hard answer"),
    (("click the search bar", "yes", "keep going"), SMART, "a chain back to screen work"),
    (("compare these two laptops", "the first one"), SMART, "picking from a comparison"),
    (("what's the time", "thanks"), FAST, "an ending after an easy turn"),
    (("what's the time", "yes"), FAST, "yes to an easy question"),
    (("play something", "next"), FAST, "the next song"),
    (("open spotify", "the second one"), FAST, "choosing after an easy turn"),
    (("what's the weather", "and tomorrow?"), FAST, "the same lookup, another day"),
    (("set a timer for 10 minutes", "make it 15"), FAST, "changing one number"),
    (("click the blue button", "okay thanks"), FAST, "an ending, not a continuation"),
    (("click the blue button", "please stop"), FAST, "an order, not a continuation"),
    (("explain how caching works", "okay thanks"), FAST, "an ending after a hard turn"),
    (("scroll down", "no, you're right"), FAST, "agreement, not a correction"),
    (("write me a python script that renames files", "make it recursive"), SMART,
     "changing the code it just wrote, in the person's own words"),
    (("plan my week around my three deadlines", "move the gym to thursday"), SMART,
     "reworking a plan, in the person's own words"),

    # ------------------------------------------ edits in the person's own words
    # EDIT (router.py): a short "make/change/add/use ..." inherits the brain of
    # the request before it. Probed 14 Sep 2026, the day it landed.
    (("write me a python script that renames files", "add logging"), SMART,
     "adding to the code it just wrote"),
    (("explain how caching works", "make it shorter"), SMART, "rewriting a hard answer"),
    (("draft an email to my landlord", "make it more polite"), SMART, "rewriting a draft"),
    (("compare the iPhone and the Pixel", "add the Galaxy too"), SMART, "widening a comparison"),
    (("compare tesla and apple", "but what about ford"), SMART, "the same comparison, one more"),
    (("click the blue button", "use the other one"), SMART, "still at the screen"),
    (("write me a python script that renames files", "make it recursive", "add logging"), SMART,
     "an edit of an edit walks back to the script"),
    (("play something", "switch to jazz"), FAST, "an edit of an easy turn stays easy"),
    (("what's the weather", "actually, make that Paris"), FAST, "the same lookup, another city"),
    ("make a note to buy milk", FAST, "an edit word with nothing before it is judged as itself"),
    ("change the voice to the butler", FAST, "one setting"),
    (("explain how caching works", "add milk to my shopping list"), FAST,
     "a new, easy request that happens to start with 'add', aimed at one of their lists"),
    (("plan my week around my three deadlines", "put on some music"), FAST,
     "'put on' is play, not 'put the gym on thursday'"),
    (("write me a python script that renames files", "make it recursive", "and add logging"), SMART,
     "an edit that starts with 'and' is still an edit"),

    # ------------------------------------------- the words added on 14 Sep 2026
    ("zip code for Toronto", FAST, "a postcode is not programming"),
    ("what's the dress code", FAST, "a lookup, not programming"),
    ("find the nearest gas station", FAST, "a maps lookup, not finding a thing on screen"),
    ("what's the time? and why is it dark?", SMART, "one of the two questions is hard"),
    ("is it raining? is it cold?", FAST,
     "two easy questions, each a standing lookup"),

    # ----------------------------------------------------------------- traps
    # The same words as the hard lists, meaning something easy.
    ("what type of tree is this", FAST, "'type of' is a noun phrase"),
    ("press release for apple", FAST, "'press release' is a noun"),
    ("I need a hard copy", FAST, "'hard copy' is a noun"),
    ("no, you're right", FAST, "agreement"),
    ("why is the sky blue", SMART, "an explanation; the bias allows it even when Haiku would cope"),
    ("time to go to bed", FAST, "an announcement, not navigating"),
    ("field hockey scores today", FAST, "a score, not a form field"),
    ("the window cleaner comes tomorrow", FAST,
     "a note about the day, not a window on screen"),
    ("what's the area code for Toronto", FAST, "a lookup, not programming"),
    ("what's my plan for today", FAST, "reading the plan store, not planning"),
    ("leafs vs habs score", SMART,
     "the owner's call (14 Sep 2026, 'they all work'): 'vs' reads as a comparison, and"
     " that is left alone; was a MISS for FAST"),
    ("what's the weather and what time is it", FAST, "two lookups; 'and' is not a clause"),
    ("what time is it? what's the weather?", FAST,
     "two lookups, each a standing phrase"),

    # --------------------------------------------------------- hard questions
    ("why is my code failing", SMART, "debugging"),
    ("compare the iPhone and the Pixel", SMART, "a comparison"),
    ("help me plan a trip to Japan", SMART, "planning"),
    ("what's the difference between a Roth IRA and a traditional IRA", SMART,
     "an explanation, and long"),
    ("is it cheaper to lease or buy", SMART, "a trade-off"),
    ("which is better for me, Netflix or Disney", SMART, "a recommendation"),
    ("write a cover letter for this job", SMART, "writing"),
    ("summarize my unread emails", SMART, "summarising"),
    ("what should I cook tonight", SMART, "a recommendation"),
    ("what if I leave at 5 instead", SMART, "a hypothetical"),
    ("work out how much I owe", SMART, "arithmetic over something"),
    ("translate good morning into Tamil", SMART, "translation, which the bias keeps on Sonnet"),
    ("can you look at my calendar next week and tell me which day is least busy",
     SMART, "reading, then judging"),
    ("fix this bug", SMART, "debugging"),
    ("help me with my homework", SMART, "reasoning, however short"),
    ("how does a mortgage work", SMART, "an explanation"),
    ("how do I reset my router", SMART, "troubleshooting steps"),

    # ------------------------------------------------------ shapes of message
    ({"messages": [{"role": "user", "content": [{"type": "text", "text": "what's the time"}]}]},
     FAST, "the same words as a text block are judged the same"),
    ({"messages": [{"role": "user", "content": "what is this"}], "has_image": True},
     SMART, "there is a picture to read"),
    ({"messages": []}, SMART, "nothing to judge is not the same as easy"),
]


def _call(said):
    if isinstance(said, dict):
        return router.why(said["messages"], has_image=said.get("has_image", False))
    if isinstance(said, tuple):
        return router.why(convo(*said))
    return router.why([{"role": "user", "content": said}])


def _label(said):
    if isinstance(said, dict):
        m = said["messages"]
        return "(%s)" % ("empty" if not m else "with an image" if said.get("has_image") else "text block")
    if isinstance(said, tuple):
        return " -> ".join(said)
    return said


c = Check()
print("The corpus is big enough to mean something:")
c.truthy("  at least 100 cases (%d)" % len(CASES), len(CASES) >= 100)
c.truthy("  every case has a reason",
         all(isinstance(case[2], str) and case[2].strip() for case in CASES))
c.truthy("  every answer is fast or smart", all(case[1] in (FAST, SMART) for case in CASES))
c("  no sentence is listed twice",
  len({repr(case[0]) for case in CASES}), len(CASES))

misses = []
print("\nEach one gets the brain it should:")
for case in CASES:
    said, want, reason = case[:3]
    missed = len(case) > 3 and case[3] == MISS
    got, rsn = _call(said)
    text = _label(said)[:52]
    if missed:
        misses.append((text, want, rsn, reason))
        other = SMART if want == FAST else FAST
        # Passes while still wrong. Failing here means the router was fixed:
        # take the MISS mark off this case.
        c("  MISS  %-52s (should be %s; if this fails it's FIXED, unmark it)" % (text, want),
          got, other)
    else:
        c("  %-5s %-52s" % (want, text), got, want)

print("\n%d known misses, for router.py's owner:" % len(misses))
for text, want, rsn, reason in misses:
    print("  - %s: gets %s (%s), should be %s: %s"
          % (text, SMART if want == FAST else FAST, rsn, want, reason))

c.done()
