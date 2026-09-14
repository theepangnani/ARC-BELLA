# -*- coding: utf-8 -*-
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
import sys
import threading

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
c("  remember refuses", memory.remember("Something new"), memory.COULD_NOT)
c("  the damage is left for self-repair",
  memory.STORE.read_text(encoding="utf-8"), "{ not json")
memory.STORE.write_text("[]", encoding="utf-8")
c("  a list is damage too, not an empty memory",
  memory.remember("Something new"), memory.COULD_NOT)

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

c.done()
