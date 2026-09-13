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

# Which ARC this guardian is for. Taken from the command line as well as the
# environment, because a Windows scheduled task can carry arguments trivially
# and environment variables only awkwardly — and the private Bella needs BOTH a
# different port and a different data directory.
#
# Getting this wrong is worse than not watching at all: a guardian that
# relaunches run.py with no environment starts a SHARED Bella pointed at the
# PRIVATE data directory. That is not a failed rescue, it is a second app
# writing into somebody's private notes. So the same values that decide what to
# watch are the values handed to what gets started.
_args = sys.argv[1:]


def _arg(name, fallback=""):
    key = "--" + name
    for i, a in enumerate(_args):
        if a == key and i + 1 < len(_args):
            return _args[i + 1]
        if a.startswith(key + "="):
            return a.split("=", 1)[1]
    return fallback

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(_arg("data") or os.getenv("ARC_DATA_DIR") or ROOT).resolve()
LOG = DATA_DIR / "guardian.log"
STATUS = DATA_DIR / "guardian-status.json"

PORT = int(_arg("port") or os.getenv("ARC_GUARD_PORT")
           or os.getenv("ARC_PORT") or "8420")
CHILD_DATA = _arg("data") or os.getenv("ARC_DATA_DIR", "")
CHILD_VARIANT = _arg("variant") or os.getenv("ARC_APP_VARIANT", "")
EVERY = int(os.getenv("ARC_GUARD_EVERY", "60"))          # seconds between checks
TIMEOUT = int(os.getenv("ARC_GUARD_TIMEOUT", "10"))      # seconds to wait for a reply
# How long a start may take before it counts as failed, checked every
# BOOT_POLL seconds rather than once at the end. It was a single look after 75s:
# a boot that needed 90 counted as a failure, the next round started a SECOND
# copy on top of the one still loading, and on 13 Sep 2026 that became six copies
# of run.py fighting each other for the CPU until the guardian gave up and both
# Bellas were down. A start is now waited for, for as long as it is alive and
# still inside this limit, and the moment it answers it is back.
BOOT_LIMIT = int(os.getenv("ARC_GUARD_BOOT", "240"))
BOOT_POLL = int(os.getenv("ARC_GUARD_BOOT_POLL", "5"))
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


# The copy this guardian started last. Held so that a start which is still
# loading is waited for rather than started again on top of, and so that a copy
# which never came up is stopped BEFORE another one is launched.
_child = None


def _alive(proc) -> bool:
    try:
        return proc is not None and proc.poll() is None
    except Exception:
        return False


def _stop(proc, why: str) -> None:
    """Stop one process: politely, then not."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
            proc.wait(timeout=8)
        note("stopped the copy that %s (pid %s)" % (why, proc.pid))
    except Exception as e:
        note("could not stop pid %s: %s" % (getattr(proc, "pid", "?"), e))


def _port_holder() -> int:
    """The pid LISTENING on this port, or 0. Windows only; elsewhere 0."""
    if os.name != "nt":
        return 0
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception:
        return 0
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[3].upper() == "LISTENING"
                and parts[1].rsplit(":", 1)[-1] == str(PORT)):
            try:
                return int(parts[4])
            except ValueError:
                return 0
    return 0


def _image_of(pid: int) -> str:
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=15).stdout
        return out.split(",")[0].strip().strip('"').lower()
    except Exception:
        return ""


def clear_the_way() -> None:
    """Before a start: nothing left over may be competing with it.

    The copy this guardian started, if it is still alive and still not
    answering. And whatever holds the port without answering — a hung ARC, or
    one started by hand — but ONLY if it is Python: the port belongs to ARC, and
    something else sitting on it is for a person to look at, not for this to kill.
    """
    global _child
    if _alive(_child):
        _stop(_child, "never started answering")
    _child = None
    pid = _port_holder()
    if pid and pid != os.getpid():
        image = _image_of(pid)
        if image.startswith("python"):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=20)
                note("stopped a %s (pid %d) holding port %d without answering"
                     % (image, pid, PORT))
            except Exception as e:
                note("could not stop pid %d on port %d: %s" % (pid, PORT, e))
        elif image:
            note("port %d is held by %s (pid %d), which is not ARC — leaving it "
                 "alone; ARC cannot start until it lets go" % (PORT, image, pid))


def wait_for_boot():
    """(True, seconds) once ARC answers; (False, reason) if the copy died or the
    limit passed. Checked every BOOT_POLL seconds, so a quick start is noticed
    quickly and a slow one is not written off at a fixed moment."""
    began = time.monotonic()
    while True:
        waited = time.monotonic() - began
        if answering():
            return True, int(waited)
        if _child is not None and not _alive(_child):
            code = _child.poll()
            return False, "it exited during start-up (code %s) — see arc-server.log" % code
        if waited >= BOOT_LIMIT:
            return False, "it was still not answering after %d seconds" % BOOT_LIMIT
        time.sleep(BOOT_POLL)


def start() -> bool:
    """Launch ARC detached, so it outlives this process rather than dying with
    it — a supervisor whose children die when it is closed is a supervisor that
    turns one problem into two."""
    global _child
    try:
        # The child gets the SAME identity this guardian was given. Inheriting
        # the bare environment is how the private instance would come back as
        # the shared one.
        env = dict(os.environ)
        env["ARC_PORT"] = str(PORT)
        if CHILD_DATA:
            env["ARC_DATA_DIR"] = CHILD_DATA
        if CHILD_VARIANT:
            env["ARC_APP_VARIANT"] = CHILD_VARIANT
        # The child's output goes to a FILE, not to DEVNULL. Throwing it away
        # was a mistake found the first time something went wrong afterwards:
        # every instance the guardian had rescued was running with no log at
        # all, so the one question worth asking — what did it say on the way
        # up — had no answer anywhere on the machine.
        #
        # Appended, so a restart does not erase the reason for the one before
        # it, which is usually the reason for this one.
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            out = io.open(DATA_DIR / "arc-server.log", "a", encoding="utf-8",
                          errors="replace")
            out.write("\n===== started by the guardian at %s =====\n"
                      % time.strftime("%Y-%m-%d %H:%M:%S"))
            out.flush()
        except Exception:
            out = subprocess.DEVNULL
        kw = {"cwd": str(ROOT), "env": env, "stdout": out,
              "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kw["creationflags"] = 0x00000008 | 0x00000200
        else:
            kw["start_new_session"] = True
        try:
            _child = subprocess.Popen([sys.executable, str(ROOT / "run.py")], **kw)
        finally:
            # The child has its own handle now. Keeping ours open leaked one per
            # restart and kept arc-server.log locked against rotation.
            if out is not subprocess.DEVNULL:
                out.close()
        return True
    except Exception as e:
        note("could not launch ARC at all: %s: %s" % (type(e).__name__, e))
        return False


def main(cycles=None) -> None:
    """Watch for ever. `cycles` bounds the loop, for the test that drives it."""
    note("guardian watching port %d, checking every %ds" % (PORT, EVERY))
    misses = 0
    failed_restarts = 0
    restarts = 0
    quiet = False          # given up, and already said so once

    while cycles is None or cycles > 0:
        if cycles is not None:
            cycles -= 1
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
                clear_the_way()    # a last copy still loading would sit there for ever
                note("ARC will not start after %d attempts — leaving it alone now. "
                     "Something needs a person: check the .env, the port, and "
                     "whether python still runs here." % failed_restarts)
                status(state="given up", port=PORT, restarts=restarts,
                       needs="a person")
                quiet = True
            time.sleep(EVERY)
            continue

        note("ARC stopped answering (%d checks) — restarting it" % misses)
        # Nothing left over competing with the new copy: not a previous start,
        # not a hung process on the port.
        clear_the_way()
        if not start():
            failed_restarts += 1
            time.sleep(EVERY)
            continue

        restarts += 1
        misses = 0
        status(state="starting", port=PORT, restarts=restarts)
        up, detail = wait_for_boot()
        if up:
            note("ARC is back, %d second(s) after the restart" % detail)
            failed_restarts = 0
            status(state="ok", port=PORT, restarts=restarts)
        else:
            failed_restarts += 1
            note("restart attempt %d of %d did not come up: %s"
                 % (failed_restarts, GIVE_UP_AFTER, detail))
        time.sleep(EVERY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        note("guardian stopped by hand")
