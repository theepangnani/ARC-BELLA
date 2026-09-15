# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Memory is kept, and not quietly lost, when the file is busy.

memory.py was the one personal store never moved onto storefile.py. Its _load()
turned ANY failure to read into {} — and on Windows a file being replaced by
another thread routinely fails to read. remember() would then write back a file
holding only the current person's facts, and every other account's memory was
gone. Its _save() swallowed every error, so the reply said "Noted." whether or
not anything was kept.

Nobody remembers being told something they then forget. That is what this
guards: a busy or damaged file stops the write, a failed write says so, and two
people saving at once both keep everything.
"""
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
DATA = sandbox()

import memory      # noqa: E402
import storefile   # noqa: E402

OWNER, GUEST = "owner@example.com", "guest@example.com"
c = Check()


def fresh():
    if memory.STORE.exists():
        memory.STORE.unlink()


print("It goes through storefile, like every other store:")
c.truthy("  the file's shared lock, not a private one",
         memory._lock is storefile.lock(memory.STORE))

print("\nA busy file is never read as empty:")
fresh()
memory.use(OWNER)
memory.remember("The owner's sister is called Maya")
memory.use(GUEST)
memory.remember("The guest likes green tea")

real_read = storefile.read


def busy(path, empty=list):
    raise storefile.Busy("memory.json stayed busy")


storefile.read = busy
try:
    said = memory.remember("The guest has a dog called Biscuit")
finally:
    storefile.read = real_read
c("  remembering says it could not", said, memory.COULD_NOT)
kept = json.loads(memory.STORE.read_text(encoding="utf-8"))
c("  and the owner's memory is still there", len(kept.get(OWNER, [])), 1)
c("  as is the guest's", len(kept.get(GUEST, [])), 1)

storefile.read = busy
try:
    c("  forgetting says so too", memory.forget("tea"), memory.COULD_NOT)
    # Display reads may show nothing; a turn must not fail over it.
    c("  a display read shows nothing rather than failing", memory.facts(), [])
    c("  and the prompt block is simply empty", memory.block(), "")
finally:
    storefile.read = real_read
c("  ...and nothing was forgotten", memory.count(), 1)

print("\nA damaged file is not written over:")
memory.STORE.write_text("{ not json", encoding="utf-8")
# Not "try again in a moment": damage is just as broken a moment later.
c("  remember refuses, pointing at self-repair", memory.remember("Something new"), memory.DAMAGED)
c("  forget says the same", memory.forget("tea"), memory.DAMAGED)
c("  the damage is left for self-repair",
  memory.STORE.read_text(encoding="utf-8"), "{ not json")
memory.STORE.write_text("[]", encoding="utf-8")
c("  a list is damage too, not an empty memory",
  memory.remember("Something new"), memory.DAMAGED)

print("\n...and self-repair can put it right:")
import selfheal   # noqa: E402
# It was only in the export, so there was never a copy to put back, and a
# refusal to write over damage would have been permanent.
c.truthy("  memory is one of its files", "memory.json" in selfheal.DATA_FILES)
c("  healthy memory passes the shape check",
  selfheal._shape_ok("memory.json", {OWNER: [], GUEST: []}), True)
c("  a list does not, since memory.py cannot read one",
  selfheal._shape_ok("memory.json", []), False)
c("  nor a dict of the wrong things", selfheal._shape_ok("memory.json", {OWNER: "x"}), False)
fresh()
memory.remember("The owner likes green tea")
selfheal.snapshot(force=True)
memory.STORE.write_text("{ broken", encoding="utf-8")
selfheal._fix_data()
c("  the last good copy is back", [m["text"] for m in memory.facts()],
  ["The owner likes green tea"])
c("  and remembering works again", memory.remember("The owner walks at six"), "Noted.")

print("\nThe one-time import checks strictly, not through a display read:")
fresh()
memory.remember("The owner moved to Leeds")
real_read = storefile.read
_calls = []


def busy_once(*a, **k):
    # Busy for the first read only: the moment a display read would have
    # called the file empty, followed by one that works.
    _calls.append(1)
    if len(_calls) == 1:
        raise storefile.Busy("memory.json stayed busy")
    return real_read(*a, **k)


storefile.read = busy_once
try:
    c("  a busy check imports nothing",
      memory.import_facts(["The owner lives in York"], only_if_empty=True), 0)
finally:
    storefile.read = real_read
c("  an account with memory takes no import",
  memory.import_facts(["The owner lives in York"], only_if_empty=True), 0)
_rsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "run.py"),
             encoding="utf-8").read()
c.truthy("  and the import route asks for that",
         "memory.import_facts(items[:memory.MAX_FACTS], only_if_empty=True)" in _rsrc)
c("  and the real facts are untouched", [m["text"] for m in memory.facts()],
  ["The owner moved to Leeds"])

print("\nA save that fails is not reported as kept:")
fresh()
real_write = storefile.write


def full_disk(path, blob, indent=None):
    raise OSError("disk full")


storefile.write = full_disk
try:
    c("  no 'Noted.' for a fact that was not written",
      memory.remember("The owner runs on Tuesdays"), memory.COULD_NOT)
finally:
    storefile.write = real_write
c("  and none of it was kept", memory.count(), 0)
c("  ...while a normal save still is", memory.remember("The owner runs on Tuesdays"), "Noted.")

print("\nTwo people remembering at once both keep everything:")
fresh()
errors = []


def go(who, n=60):
    # A new thread starts with the ContextVar's default, so each says who it is.
    memory.use(who)
    for i in range(n):
        try:
            said = memory.remember("%s item %s" % (who.split("@")[0], "x" * (i + 1)),
                                   supersede=False)
            if not said.startswith("Noted"):
                errors.append(said)
        except Exception as e:
            errors.append(repr(e))


ts = [threading.Thread(target=go, args=(OWNER,)), threading.Thread(target=go, args=(GUEST,))]
[t.start() for t in ts]
[t.join() for t in ts]
kept = json.loads(memory.STORE.read_text(encoding="utf-8"))
c("  nothing refused or raised", errors, [])
c("  the owner kept all 60", len(kept.get(OWNER, [])), 60)
c("  the guest kept all 60", len(kept.get(GUEST, [])), 60)


# --------------------------------------------------------------------------
# Kept, and not deleted by mistake. The checks above are about the FILE; these
# are about the facts in it. Each one was a real way to lose something true,
# found on 14 Sep 2026 — a wrong delete is unrecoverable, where a duplicate is
# only untidy, so every doubt here resolves to keeping both.

def texts():
    return [m["text"] for m in memory.facts()]


print("\nForget takes what was named, not everything containing its letters:")
fresh()
memory.use(OWNER)
memory.remember("The owner has a sister called Maya and a team at work")
memory.remember("The owner drinks green tea")
# Substring matching made this "Forgotten 2." — "tea" is inside "team".
c("  'tea' does not reach 'team'", memory.forget("tea"), "Forgotten 1.")
c("  and the other fact is still there", texts(),
  ["The owner has a sister called Maya and a team at work"])
fresh()
memory.remember("The owner likes cats")
memory.remember("The owner studied communication at university")
c("  'cat' does not reach 'communication'", memory.forget("cat"), "Forgotten 1.")
c.truthy("  ...which is kept", any("communication" in t for t in texts()))
fresh()
memory.remember("The owner likes green tea, no sugar")
c("  'the tea thing' still finds the tea fact", memory.forget("the tea thing"), "Forgotten 1.")

print("\nWhen a subject matches several facts, none go until somebody says which:")
fresh()
memory.remember("The owner's sister Maya lives in Leeds")
memory.remember("The owner's sister has two children")
said = memory.forget("sister")
c.truthy("  it asks rather than deleting", said.startswith("That matches 2 things"))
c.truthy("  and names each, with an id to pick by",
         all(m["text"] in said and m["id"] in said for m in memory.facts()))
c("  nothing was forgotten", memory.count(), 2)
pick = memory.facts()[1]
c("  the id then takes exactly that one", memory.forget(pick["id"]), "Forgotten 1.")
c("  leaving the other", texts(), ["The owner's sister Maya lives in Leeds"])
fresh()
memory.remember("The owner likes jazz")
# Kept apart on purpose: remembered normally, the richer fact would replace
# the one it covers, and there would be nothing to choose between.
memory.remember("The owner likes jazz festivals", supersede=False)
c("  a phrase that IS one fact takes that fact alone",
  memory.forget("the owner likes jazz"), "Forgotten 1.")
c("  ...not its longer neighbour", texts(), ["The owner likes jazz festivals"])
c("  'all' still means all", memory.forget("all"), "Forgotten all 1.")

print("\nTwo facts saved in the same millisecond are still two ids:")
fresh()
_real_time = memory.time.time
memory.time.time = lambda: 1700000000.0
try:
    memory.remember("The owner lives in Markham")
    memory.remember("The owner plays the piano")
finally:
    memory.time.time = _real_time
ids = [m["id"] for m in memory.facts()]
# They were both m1700000000000, so forgetting "one" of them took both.
c("  different ids", len(set(ids)), 2)
c("  forgetting one id forgets one fact", memory.forget(ids[0]), "Forgotten 1.")
c("  and the other is kept", texts(), ["The owner plays the piano"])
c.truthy("  ids keep their old shape, which stored files already use",
         all(re.match(r"^m\d+$", i) for i in ids))

print("\nA shorter restatement never eats the detail of a richer fact:")
fresh()
memory.remember("Her sister Maya lives in Leeds")
memory.remember("Her sister is Maya")
# The old rule divided by the SMALLER set of words, so anything wholly inside
# an older fact counted as a restatement of it — and Leeds was gone.
c.truthy("  Leeds survives", any("Leeds" in t for t in texts()))
fresh()
memory.remember("The owner is allergic to peanuts and shellfish")
memory.remember("The owner is allergic to peanuts")
c.truthy("  so does the shellfish allergy", any("shellfish" in t for t in texts()))
# Moved (second recall commit): a richer fact no longer replaces the one it
# covers either. With plurals and -ing folded together, "likes dog food" covered
# "likes dogs" and "the owner's wife drives a red Tesla" covered "drives a red
# Tesla" — different facts, one of them gone. Only a restatement replaces now.
c("  a richer fact does not replace the one it covers, either",
  memory._supersedes("Her sister Maya lives in Leeds", "Her sister is Maya"), False)
c("  a real rewording still replaces",
  memory._supersedes("His sister is Maya", "His sister is called Maya"), True)
c("  ...and so does its mirror",
  memory._supersedes("His sister is called Maya", "His sister is Maya"), True)
c("  a one-word fact is never folded into a longer one",
  memory._supersedes("The owner's daughter is vegetarian", "Vegetarian"), False)
for new, old in (("Theepan works in Toronto", "Theepan lives in Toronto"),
                 ("Theepan finished the Python course", "Theepan is learning Python"),
                 ("The owner has a cat", "The owner has a dog"),
                 ("His brother lives in Leeds", "His sister lives in Leeds"),
                 ("The owner dislikes coffee", "The owner likes coffee"),
                 ("The owner likes dog food", "The owner likes dogs"),
                 ("The owner's wife drives a red Tesla", "The owner drives a red Tesla"),
                 ("The owner's daughter is learning Python", "The owner is learning Python"),
                 ("The owner likes hiking in Wales", "The owner likes hiking"),
                 ("The owner lives in York", "The owner lived in Leeds"),
                 ("The owner's son plays chess", "The owner plays chess")):
    c("  still two facts: %r / %r" % (new, old), memory._supersedes(new, old), False)

print("\nThe cap (pinned, not settled):")
# KNOWN, and waiting on the owner. At MAX_FACTS the oldest fact is dropped with
# nothing said — usually the oldest is their name. Whether to warn or to refuse
# the new one is the owner's call (put to them via Claude 1, 14 Sep 2026). This
# pins today's behaviour so that the change, when it comes, is deliberate: when
# it lands, this check is expected to fail — replace it with the chosen rule.
fresh()
memory.remember("The owner's name is Sam")
for i in range(memory.MAX_FACTS):
    memory.remember("Filler %d" % i, supersede=False)
c("  the cap is still %d" % 200, memory.MAX_FACTS, 200)
c("  KNOWN: the oldest is dropped silently at the cap",
  any("Sam" in t for t in texts()), False)


print("\nOne fact however it is worded:")
fresh()
memory.remember("I take my coffee black")
c("  a trailing full stop is already known, not a replacement",
  memory.remember("I take my coffee black."), "I already knew that.")
fresh()
c("  an import keeps one of copies that differ only in punctuation",
  memory.import_facts(["Likes green tea", "likes green tea.", "Likes  green tea!"]), 1)
fresh()
memory.remember("The owner likes hiking")
memory.remember("The owner likes to hike")
c("  'likes hiking' and 'likes to hike' are one fact", texts(), ["The owner likes to hike"])
fresh()
memory.remember("The owner is learning Python")
memory.remember("The user is learning Python")
c("  'the owner' and 'the user' are the same person", texts(), ["The user is learning Python"])
fresh()
memory.remember("The owner's name is Sam")
memory.remember("Sam is learning Python")
memory.remember("The owner is learning Python")
c("  and so is their name, once a fact says what it is",
  texts(), ["The owner's name is Sam", "The owner is learning Python"])
fresh()
memory.remember("Sam is learning Python")
memory.remember("The owner is learning Python")
# No fact says the owner is Sam, so Sam may be anybody: keep both.
c("  but a name nobody said is theirs is left alone", memory.count(), 2)
fresh()
memory.remember("His sister's name is Maya")
memory.remember("Maya is learning Python")
memory.remember("The owner is learning Python")
c("  a sister's name is not taken for theirs", memory.count(), 3)
# Only the words a fact OPENS with are its subject. Anywhere else, "guest",
# "user", "him" and "them" are what the fact says, and folding them away made
# "Maya is a guest" replace "Maya is a user" (Claude 2, landing the recall work).
for first, second in (("Maya is a user", "Maya is a guest"),
                      ("Maya likes him", "Maya likes them")):
    fresh()
    memory.remember(first)
    memory.remember(second)
    c("  a subject word later in the fact is part of it: %r / %r" % (first, second),
      texts(), [first, second])
fresh()
memory.remember("The owner likes hiking")
memory.remember("The user likes to hike")
c("  ...while 'the owner' and 'the user' in front still fold together",
  texts(), ["The user likes to hike"])
fresh()
memory.remember("I like green tea")
memory.remember("The owner likes green tea")
c("  and 'I' in front is the same subject as 'the owner'", texts(), ["The owner likes green tea"])

print("\nFacts that look alike to a word count, and are not (bug check, 15 Sep 2026):")
# Every pair here deleted one of its two facts before. A duplicate is untidy;
# a fact that is simply gone cannot be got back.
for first, second, why in (
        ("His son was born in 2015", "His son was born in 2018", "a different year"),
        ("Takes the 7am train", "Takes the 9am train", "a different time"),
        ("The guest bedroom is upstairs", "The bedroom is upstairs",
         "a subject word in front of a noun is part of the fact"),
        ("The user group meets on Tuesdays", "The group meets on Tuesdays", "the same, in front"),
        ("The owner likes fishing", "The owner likes fish", "-ing is not its plural"),
        ("The owner hates flying", "The owner hates flies", "nor is this one"),
        ("Maya is Leah's sister", "Leah is Maya's sister", "who is whose sister"),
        ("The owner's flight is at 6", "The owner's flight is at 9", "a different hour")):
    fresh()
    memory.remember(first)
    memory.remember(second)
    c("  %-38s (%s)" % (first[:38], why), len(texts()), 2)
fresh()
memory.remember("The owner likes hiking")
memory.remember("The owner likes to hike")
c("  ...and a real rewording still folds", len(texts()), 1)

print("\nA name that is also an ordinary word is not a word for the person:")
fresh()
memory.remember("My name is Will")
memory.remember("Will call the dentist on Monday")
memory.remember("Call the dentist on Monday")
c("  the appointment survives being restated", len(texts()), 3)
fresh()
memory.remember("My name is not Tom")
memory.remember("Allergic to peanuts")
memory.remember("Not allergic to peanuts")
c("  and 'not' never became a word for them", len(texts()), 3)

print("\n'What do you know about me' is a request for all of it, however it is put:")
fresh()
memory.remember("The owner has a dog called Biscuit")
memory.remember("The owner lives in Markham")
for asked in ("what do you know about me", "what do you remember about me",
              "tell me everything", "do you know anything about me", "about me"):
    c.truthy("  %-32s" % asked, memory.list_memory(asked).startswith("2 things"))
c.truthy("  ...while a real word still searches", "Biscuit" in memory.list_memory("dog"))
c.truthy("  ...and one that matches nothing still says so",
         "Nothing I know" in memory.list_memory("snowboarding"))

print("\nA name is per person, like everything else here:")
fresh()
memory.use(OWNER)
memory.remember("The owner's name is Sam")
memory.remember("Sam plays the cello")
memory.use(GUEST)
memory.remember("The user plays the cello")
c("  the guest's 'user' does not borrow the owner's name", memory.count(), 1)
memory.use(OWNER)
c("  and the owner's facts are untouched", texts(),
  ["The owner's name is Sam", "Sam plays the cello"])

print("\nSearch finds what was asked for:")
fresh()
memory.use(OWNER)
memory.remember("The owner has a dog called Biscuit")
memory.remember("The owner lives in Markham")
c.truthy("  'dogs' finds the dog", "Biscuit" in memory.list_memory("dogs"))
memory.remember("The owner is training to run a marathon")
c.truthy("  'running' finds 'run a marathon'", "marathon" in memory.list_memory("running"))
c.truthy("  'about me' is everything", memory.list_memory("me").startswith("3 things"))
c.truthy("  as is 'everything'", memory.list_memory("everything").startswith("3 things"))
memory.use(GUEST)
memory.remember("The guest likes green tea")
c("  and everything is still only whoever is asking",
  memory.list_memory("me"), "1 thing I know about me: The guest likes green tea (just now)")
memory.use(OWNER)
fresh()
memory.remember("The owner uses Google Drive for work")
memory.remember("The owner is learning Go")
c("  'Go' is the word Go, not the start of Google",
  [m["text"] for m in memory.search("Go")], ["The owner is learning Go"])
fresh()
_t = [1700000000.0]
memory.time.time = lambda: _t[0]
try:
    memory.remember("The owner lives in York")
    _t[0] += 86400
    memory.remember("The owner lives in Leeds, after moving")
finally:
    memory.time.time = _real_time
c("  on a tie the newer fact comes first",
  [m["text"] for m in memory.search("lives")][0], "The owner lives in Leeds, after moving")

print("\nDates read as a person would say them:")
_now = time.time()
for days, want in ((0.01, "just now"), (0.5, "today"), (1, "1 day ago"), (20, "2 weeks ago"),
                   (100, "3 months ago"), (364, "12 months ago"), (400, "1 year ago"),
                   (800, "2 years ago")):
    c("  %s days" % days, memory._ago(_now - days * 86400), want)

c.done()
