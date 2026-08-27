# -*- coding: utf-8 -*-
"""Whose data is whose, and who is allowed into which Bella.

Two changes with one theme: things that were shared because there had only ever
been one person, made to belong to somebody.

WHOSE.PY is the per-request identity the stores read. memory.py already had it;
this pulls it out so the other six can share ONE notion of who is asking rather
than six that drift apart. notes.py is converted here as the first of them.

The stores that FIRE IN THE BACKGROUND — alarms, reminders, price alerts,
standing rules — are deliberately NOT converted yet, and the reason is written
down at the bottom of this file so the next person does not assume it was an
oversight.

THE PRIVATE BELLA'S ALLOWLIST was inherited from the shared .env, which carries
four guest addresses, on the one instance whose entire purpose is that nobody
else is in it. That was a precedence bug, and precedence bugs do not look like
bugs — everything is set correctly and the wrong one wins.
"""
import io
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

import whose   # noqa: E402
import notes   # noqa: E402

c = Check()

OWNER = "owner@example.com"
GUEST = "guest@example.com"
whose.set_owners([OWNER])

print("An address, set once per request:")
whose.use(OWNER)
c("  the request says who it is", whose.current(), OWNER)
c("  and the owner is known as one", whose.is_owner(), True)
whose.use(GUEST)
c("  a guest is not", whose.is_owner(), False)
whose.use("")
c("  nothing set falls back to a name no email can collide with",
  whose.current(), whose.DEFAULT)

print("\nA store written before any of this existed belongs to the owner:")
LEGACY = [{"id": "1", "text": "from before the split"}]
whose.use(OWNER)
c("  the owner reads it", whose.mine(LEGACY), LEGACY)
whose.use(GUEST)
# The whole point. A guest arriving at a file that has never been split must
# not inherit somebody's reminders because nobody had got round to keying it.
c("  a guest inherits NOTHING from it", whose.mine(LEGACY), [])

print("\nAnd the first write keys it, without losing the old pile:")
whose.use(GUEST)
after = whose.replace(LEGACY, [{"id": "2", "text": "the guest's own"}])
c("  the old pile went to the owner", after[OWNER], LEGACY)
c("  the guest got their own slice", [n["text"] for n in after[GUEST]], ["the guest's own"])
whose.use(OWNER)
c("  ...and the owner still reads only theirs",
  [n["text"] for n in whose.mine(after)], ["from before the split"])

print("\nOne account's save cannot erase another's:")
blob = {OWNER: [{"t": "a"}], GUEST: [{"t": "b"}]}
whose.use(GUEST)
out = whose.replace(blob, [{"t": "b2"}])
c("  the owner's slice survived a guest's write", out[OWNER], [{"t": "a"}])
c("  and the guest's was replaced", out[GUEST], [{"t": "b2"}])

print("\nNothing runs unattended without a way to see everybody:")
# A loop that rings alarms serves no request, so it has no address — asking
# mine() would silently give it the default one and ring one account's alarms.
c("  everyone() lists every account", sorted(k for k, _ in whose.everyone(blob)),
  [GUEST, OWNER])
c("  ...and a legacy pile counts as the owner's",
  [k for k, _ in whose.everyone(LEGACY)], [OWNER])
flat = whose.flatten(blob)
c("  flatten() keeps the address on each item",
  sorted(r["_who"] for r in flat), [GUEST, OWNER])

print("\nNotes are per account, end to end:")
d = tempfile.mkdtemp()
notes.NOTES = __import__("pathlib").Path(d) / "notes.json"
io.open(notes.NOTES, "w", encoding="utf-8").write(json.dumps(LEGACY))
whose.use(OWNER)
c("  the owner sees the old note", len(notes._load()), 1)
whose.use(GUEST)
c("  the guest sees none of it", notes._load(), [])
notes.add_note("a guest note")
c("  and can keep their own", [n["text"] for n in notes._load()], ["a guest note"])
whose.use(OWNER)
c("  which the owner cannot see", [n["text"] for n in notes._load()],
  ["from before the split"])
notes.add_note("an owner note")
whose.use(GUEST)
c("  and writing does not disturb the guest's",
  [n["text"] for n in notes._load()], ["a guest note"])
on_disk = json.loads(io.open(notes.NOTES, encoding="utf-8").read())
c("  on disk it is keyed by address", sorted(on_disk), [GUEST, OWNER])

print("\nThe stores that fire in the background are NOT converted yet:")
# Stated as a test so it is a decision on the record rather than a gap somebody
# finds later and assumes was carelessness. Converting them means teaching the
# monitor loop to use everyone() instead of mine() — and getting that wrong
# means alarms that silently stop ringing, which is the single worst failure
# this app has. It is also not urgent: every tool that touches one of these
# stores is already outside GUEST_TOOLS, so no guest can reach them today.
src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the monitor loop still exists to be taught", "async def _monitor_loop" in src)
for mod in ("alarm", "alerts", "triggers", "extras"):
    body = io.open(ARC / (mod + ".py"), encoding="utf-8").read()
    c("  %-8s is still shared, on purpose" % mod, "import whose" in body, False)

print("\nThe private Bella is the owner's alone, however it is started:")
# The bug: the shared .env loaded first, so "load the instance file without
# overriding" meant the shared GUEST list had already won. Four addresses, on
# the private instance. And it only ever worked through one launcher.
c.truthy("  config is read in precedence order", "_shared_env = dotenv_values" in src)
c.truthy("  the instance file is read by run.py itself", "_INSTANCE_ENV = DATA_DIR" in src)
c.truthy("  ...and the shared file only fills what is still unset",
         "if _v is not None and _k not in os.environ" in src)
c.truthy("  an EMPTY value counts as set, or the escape refills itself",
         "means no guests" in src)


def _emails(data_dir, extra=None):
    env = dict(os.environ)
    env.setdefault("ANTHROPIC_API_KEY", "x")
    env["ARC_DATA_DIR"] = data_dir
    env.pop("ARC_ALLOWED_EMAILS", None)
    env.pop("ARC_GUEST_EMAILS", None)
    env.update(extra or {})
    code = ("import sys; sys.path.insert(0, r'%s'); import run, json; "
            "print(json.dumps({'o': sorted(run.OWNER_EMAILS), "
            "'g': sorted(run.GUEST_EMAILS)}))" % str(ARC))
    r = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, cwd=str(ARC))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"o": ["<failed>"], "g": [r.stderr.strip()[-120:]]}


shared_dir = tempfile.mkdtemp()
priv = tempfile.mkdtemp()
io.open(os.path.join(priv, "arc.env"), "w", encoding="utf-8").write(
    "ARC_ALLOWED_EMAILS=me@example.com\nARC_GUEST_EMAILS=\n")

got = _emails(priv)
c("  the instance file decides who owns it", got["o"], ["me@example.com"])
c("  ...and an empty guest list stays empty", got["g"], [])
# An explicit variable is the launcher talking, and it still wins over both.
got = _emails(priv, {"ARC_GUEST_EMAILS": "someone@example.com"})
c("  but the environment still outranks the file", got["g"], ["someone@example.com"])

c.done()
