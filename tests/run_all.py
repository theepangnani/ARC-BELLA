# -*- coding: utf-8 -*-
"""Run every suite in this directory and report.

    python tests/run_all.py            everything
    python tests/run_all.py alarm      only suites whose name contains "alarm"

Each suite runs in its own process. That is not tidiness: they set environment
variables before importing ARC — allowlists, session clocks, whether the owner
is exempt from them — and those are read once at import and never again. Two
suites sharing an interpreter would silently test each other's configuration,
and the one that ran second would be the one lying to you.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ARC = TESTS.parent

# Everything here is expected to pass with no hardware, no network and no keys.
# Suites needing a microphone, a second monitor or a live screen live in
# tests/manual/ and are not run from here.
SUITES = sorted(p.name for p in TESTS.glob("test_*.py"))


def main(argv):
    picked = [s for s in SUITES if not argv or any(a in s for a in argv)]
    if not picked:
        print("nothing matched %r; have: %s" % (argv, ", ".join(SUITES)))
        return 2

    # The suites sandbox themselves, but an inherited ARC_DATA_DIR would make
    # the harness refuse to start, so clear it here rather than 19 times.
    env = dict(os.environ)
    env.pop("ARC_DATA_DIR", None)
    env["PYTHONIOENCODING"] = "utf-8"    # box-drawing and arrows in the output

    width = max(len(s) for s in picked)
    failed, started = [], time.time()

    for name in picked:
        t0 = time.time()
        r = subprocess.run([sys.executable, str(TESTS / name)],
                           cwd=str(ARC), env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        secs = time.time() - t0
        good = r.returncode == 0
        if not good:
            failed.append((name, r))
        # One line each while it runs; the detail only for what broke.
        print("%s %-*s  %5.1fs" % ("PASS " if good else "FAIL ", width, name, secs))

    print("\n" + "-" * (width + 20))
    if failed:
        for name, r in failed:
            print("\n=== %s ===" % name)
            out = (r.stdout or "") + (r.stderr or "")
            # The failures and whatever preceded them, not 200 lines of PASS.
            lines = out.splitlines()
            keep = [i for i, ln in enumerate(lines) if "FAIL" in ln or "Error" in ln
                    or "Traceback" in ln]
            if keep:
                lo, hi = max(0, keep[0] - 3), min(len(lines), keep[-1] + 12)
                print("\n".join(lines[lo:hi]))
            else:
                print("\n".join(lines[-25:]))
        print("\n%d of %d suites FAILED: %s"
              % (len(failed), len(picked), ", ".join(n for n, _ in failed)))
    else:
        print("all %d suites passed in %.0fs" % (len(picked), time.time() - started))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
