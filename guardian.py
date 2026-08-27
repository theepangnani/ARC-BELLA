#!/usr/bin/env python3
"""The guardian — something that watches ARC when nobody is home.

selfheal.py is ARC repairing ARC: it restarts a stalled background loop, puts a
damaged notes.json back, refetches voices. All of it runs INSIDE ARC, which is
the one thing it cannot help with. A process cannot restart itself once it has
stopped, and the failure that actually ruins a week away is the whole thing
being gone at three in the morning with nobody to notice until Sunday.

So this runs outside, knows almost nothing, and does one job: keep ARC
answering, and write down honestly what happened.

WHAT IT CHECKS, and why it is not "is the process running". A crashed process is
the easy case. The one that costs you a week is a process that is still there,
still holding the port, and no longer answering — and to `tasklist` that looks
identical to a healthy one. So the question asked is the only one that matters
to a person: does an HTTP request come back? An unauthenticated /api/health
answering 401 is a pass. 401 means the gate is up and the app is behind it,
which is exactly the state we want; a 200 there would be alarming.

WHAT IT REFUSES TO DO, deliberately:

  · It does not touch data. Not notes, not sessions, not .env. If the fix for
    something is "delete a file", that is a decision for a person who can see
    what was in it. This restarts a process and nothing else.
  · It does not edit code, for the same reason selfheal does not.
  · It does not restart forever. Three failures in a row and it stops trying
    and starts saying so instead. A supervisor that relaunches a program which
    cannot start is not helping — it is writing a megabyte of log an hour and
    burning a laptop battery to do it.
  · It does not report success. The log is for the times something went wrong;
    a week of "still fine" every minute is a week of noise you will not read,
    and the one line that mattered would be buried in it. Heartbeats go to a
    separate one-line status file that is overwritten, not appended.

READ IT AFTERWARDS: guardian.log is the incident log — plain English, one line
per event, nothing else in it. guardian-status.json is "as of now", rewritten
each pass. If the log is empty when you get back, nothing went wrong.
"""

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
LOG = DATA_DIR / "guardian.log"
STATUS = DATA_DIR / "guardian-status.json"

PORT = int(os.getenv("ARC_GUARD_PORT", os.getenv("ARC_PORT", "8420")))
EVERY = int(os.getenv("ARC_GUARD_EVERY", "60"))          # seconds between checks
TIMEOUT = int(os.getenv("ARC_GUARD_TIMEOUT", "10"))      # seconds to wait for a reply
GRACE = int(os.getenv("ARC_GUARD_GRACE", "75"))          # seconds to allow for a boot
# Two misses before acting. One can be a laptop waking up, a GC pause, or the
# moment a deploy is swapping the process — restarting on the strength of a
# single timeout would make the guardian the thing causing the outages.
MISSES = int(os.getenv("ARC_GUARD_MISSES", "2"))
GIVE_UP_AFTER = int(os.getenv("ARC_GUARD_GIVE_UP", "3"))  # consecutive failed restarts

# A healthy ARC refuses an unauthenticated caller. 200 would mean the gate is
# down, which is a different and worse problem — so both are "answering", and
# only silence counts as dead.
ALIVE = (200, 401, 403)


def note(line: str) -> None:
    """One line, plain English, appended. This file is only ever read by a
    person wondering what happened, so it is written for that person."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (stamp, line))
    except Exception:
        pass
    print("%s  %s" % (stamp, line), flush=True)


def status(**fields) -> None:
    """Overwritten, never appended — this is 'as of now', and a history of
    heartbeats is the noise that hides the incident."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        fields["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = STATUS.with_name(STATUS.name + ".tmp")
        tmp.write_text(json.dumps(fields, indent=1), encoding="utf-8")
        os.replace(tmp, STATUS)
    except Exception:
        pass


def answering() -> bool:
    req = urllib.request.Request(
        "http://127.0.0.1:%d/api/health" % PORT,
        headers={"User-Agent": "arc-guardian"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in ALIVE
    except urllib.error.HTTPError as e:
        # An HTTP error IS an answer. The gate saying no is the app working.
        return e.code in ALIVE
    except Exception:
        return False


def start() -> bool:
    """Launch ARC detached, so it outlives this process rather than dying with
    it — a supervisor whose children die when it is closed is a supervisor that
    turns one problem into two."""
    try:
        kw = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kw["creationflags"] = 0x00000008 | 0x00000200
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, str(ROOT / "run.py")], **kw)
        return True
    except Exception as e:
        note("could not launch ARC at all: %s: %s" % (type(e).__name__, e))
        return False


def main() -> None:
    note("guardian watching port %d, checking every %ds" % (PORT, EVERY))
    misses = 0
    failed_restarts = 0
    restarts = 0
    quiet = False          # given up, and already said so once

    while True:
        ok = answering()
        if ok:
            if misses or failed_restarts or quiet:
                note("ARC is answering again")
            misses = failed_restarts = 0
            quiet = False
            status(state="ok", port=PORT, restarts=restarts)
            time.sleep(EVERY)
            continue

        misses += 1
        status(state="not answering", port=PORT, misses=misses, restarts=restarts)
        if misses < MISSES:
            # Not an incident yet. A single miss is usually a laptop waking up.
            time.sleep(EVERY)
            continue

        if failed_restarts >= GIVE_UP_AFTER:
            if not quiet:
                note("ARC will not start after %d attempts — leaving it alone now. "
                     "Something needs a person: check the .env, the port, and "
                     "whether python still runs here." % failed_restarts)
                status(state="given up", port=PORT, restarts=restarts,
                       needs="a person")
                quiet = True
            time.sleep(EVERY)
            continue

        note("ARC stopped answering (%d checks) — restarting it" % misses)
        if not start():
            failed_restarts += 1
            time.sleep(EVERY)
            continue

        restarts += 1
        # Cleared here, BEFORE the wait, not only on success. Left standing, a
        # boot slower than GRACE would leave the counter already at the
        # threshold, so the very next failed check would launch a SECOND ARC on
        # top of the one still starting. run.py refuses to be the second copy,
        # so the damage was bounded — but relying on that is relying on someone
        # else's safety net for a mistake made here. Every restart now buys a
        # full fresh round of checks before another one is considered.
        misses = 0
        time.sleep(GRACE)
        if answering():
            note("ARC is back, %d second(s) after the restart" % GRACE)
            failed_restarts = 0
            status(state="ok", port=PORT, restarts=restarts)
        else:
            failed_restarts += 1
            note("restarted, but not answering yet (attempt %d of %d) — "
                 "giving it another round before deciding"
                 % (failed_restarts, GIVE_UP_AFTER))
        time.sleep(EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        note("guardian stopped by hand")
