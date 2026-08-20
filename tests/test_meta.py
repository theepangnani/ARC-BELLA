# -*- coding: utf-8 -*-
"""Checks on the suites themselves.

Every one of these is a mistake already made rather than a hypothetical. A
hard-coded `c:\\dev\\...` path passed on the machine it was written on, passed
the fresh-clone rehearsal on that same machine, and failed in CI — where the
path genuinely does not exist. A suite that imports ARC before sandbox() runs
reads the developer's real data directory. A suite that prints FAILURES ABOVE
and exits 0 is a green tick over a broken test.

None of that is visible from the output of a passing run, which is exactly why
it needs a test of its own.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

TESTS = ARC / "tests"
suites = sorted(p for p in TESTS.glob("test_*.py"))
c = Check()

print("%d suites, plus the harness and the runner\n" % len(suites))
c.truthy("there are suites to check", suites)

# An absolute path is machine-specific by definition. URLs are fine; a colon
# after a single letter, or a leading /home//Users, is not.
ABS = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![\w/])/(?:home|Users)/")

print("No suite hard-codes a path off this machine:")
# This file is exempt from its own rule, and has to be: it cannot hunt for
# "c:\\" and "/home/" without containing them.
for p in [s for s in suites if s.name != "test_meta.py"] + \
        [TESTS / "_harness.py", TESTS / "run_all.py"]:
    src = io.open(p, encoding="utf-8").read()
    hits = [m.group(0) for m in ABS.finditer(re.sub(r"https?://\S+", "", src))]
    c("  %-24s" % p.name, hits, [])

print("\nEvery suite sandboxes BEFORE it imports ARC:")
# Import order is the whole ballgame: run.py, session.py and the toolkits read
# ARC_DATA_DIR once, at import, and never look again.
for p in suites:
    src = io.open(p, encoding="utf-8").read()
    if "sandbox()" not in src:
        c("  %-24s calls sandbox()" % p.name, False, True)
        continue
    at_sandbox = src.index("sandbox()")
    first_arc = min([src.index(m) for m in
                     ("\nimport run", "\nimport session", "\nimport alarm",
                      "\nimport gauth", "\nimport pc", "\nimport extras",
                      "\nfrom starlette")
                     if m in src] or [len(src)])
    c.truthy("  %-24s sandbox() first" % p.name, at_sandbox < first_arc)

print("\nEvery suite fails loudly rather than quietly:")
for p in suites:
    src = io.open(p, encoding="utf-8").read()
    c.truthy("  %-24s exits non-zero on failure" % p.name,
             "sys.exit(0 if ok else 1)" in src or "c.done()" in src)

print("\nThe runner picks up all of them:")
runner = io.open(TESTS / "run_all.py", encoding="utf-8").read()
c.truthy("  it globs rather than listing names", 'glob("test_*.py")' in runner)
c("  and leaves tests/manual alone", "manual" in runner.split("SUITES =")[1][:200], False)

print("\nThe hardware suites are where they belong:")
manual = sorted(p.name for p in (TESTS / "manual").glob("test_*.py"))
c.truthy("  tests/manual exists and has some", manual)
c("  none of them is in the automatic set", [m for m in manual if (TESTS / m).exists()], [])

c.done()
