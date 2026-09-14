# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The guardian restarts ARC without making things worse.

On 13 Sep 2026 both Bellas were restarted for a settings change and did not
come back. The start took longer than the guardian's single look at 75 seconds,
so it counted as failed; the next round launched a second copy on top of the
first, still loading; then a third. Six copies of run.py were loading at once,
each slowing the others, and the guardian was one attempt from giving up for
good. Both were started by hand in the end, and each was up in under thirty
seconds once it had the machine to itself.

Played out here on a pretend clock, with pretend processes — nothing is
started, stopped or listened on:

  · a slow start is waited for, and only ONE copy is launched
  · a copy that never answers is stopped BEFORE the next is launched
  · a copy that crashes on the way up is noticed at once, not after four minutes
  · something holding the port is stopped only if it is Python
  · after the last attempt, nothing is left loading

And from the second bug check of the same day, which found the first version
of this fix too eager:

  · a server that is BUSY, not dead, is never stopped: one long look first
  · only the process on the port is stopped, not its children (the app window)
  · a failed stop is not logged as a stop
  · the port is found on a Windows that does not say LISTENING in English
"""
import importlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

sys.argv = ["guardian.py", "--port", "8499"]
sys.path.insert(0, str(ARC))
import guardian   # noqa: E402

c = Check()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def sleep(self, s):
        self.t += s

    def monotonic(self):
        return self.t


class Proc:
    count = 0

    def __init__(self, dies_at=None, clock=None):
        Proc.count += 1
        self.pid = 5000 + Proc.count
        self.dies_at, self.clock = dies_at, clock
        self.stopped = False

    def poll(self):
        if self.stopped:
            return -15
        if self.dies_at is not None and self.clock.t >= self.dies_at:
            return 1
        return None

    def terminate(self):
        self.stopped = True

    def kill(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


def scenario(up_after=None, dies_after=None, cycles=12, holder=(0, ""), slow_but_alive=False,
             kill_rc=0):
    """Run the guardian loop. ARC is 'down' until the first start, then answers
    `up_after` seconds after a start (None: never). `slow_but_alive`: the
    original server misses every quick check but answers a patient one.
    Returns what happened."""
    clock = Clock()
    events = []
    state = {"started_at": None, "procs": []}
    Proc.count = 0

    def answering(timeout=None):
        if slow_but_alive and not state["procs"]:
            return bool(timeout and timeout >= 30)
        s = state["started_at"]
        return s is not None and up_after is not None and clock.t - s >= up_after \
            and not state["procs"][-1].stopped

    def popen(*a, **k):
        p = Proc(dies_at=(clock.t + dies_after) if dies_after is not None else None, clock=clock)
        # Starting a copy while an earlier one is still alive is the bug.
        events.append(("start", sum(1 for q in state["procs"] if q.poll() is None)))
        state["procs"].append(p)
        state["started_at"] = clock.t
        return p

    killed = []
    saved = (guardian.time.sleep, guardian.time.monotonic, guardian.answering,
             guardian.subprocess.Popen, guardian._port_holder, guardian._image_of,
             guardian.subprocess.run)
    guardian.time.sleep, guardian.time.monotonic = clock.sleep, clock.monotonic
    guardian.answering = answering
    guardian.subprocess.Popen = popen
    guardian._port_holder = lambda: holder[0]
    guardian._image_of = lambda pid: holder[1]
    class Done:
        returncode = kill_rc
        stderr = "ERROR: Access is denied."

    guardian.subprocess.run = lambda args, **k: killed.append(args) or Done()
    guardian._child = None
    if guardian.LOG.exists():
        guardian.LOG.unlink()
    try:
        guardian.main(cycles=cycles)
    finally:
        (guardian.time.sleep, guardian.time.monotonic, guardian.answering,
         guardian.subprocess.Popen, guardian._port_holder, guardian._image_of,
         guardian.subprocess.run) = saved
    log = io.open(guardian.LOG, encoding="utf-8").read() if guardian.LOG.exists() else ""
    return events, state["procs"], log, killed


print("A slow start is waited for, not started again on top of:")
events, procs, log, _ = scenario(up_after=150, cycles=6)
c("  one copy launched", len(events), 1)
c.truthy("  and it is reported back at its real time", "back, 150 second(s)" in log)
c("  not counted as a failed attempt", "did not come up" in log, False)

print("\nA copy that never answers is stopped before the next one starts:")
events, procs, log, _ = scenario(up_after=None, cycles=40)
c("  three attempts, then it gives up", len(events), 3)
c("  no start ever had an earlier copy still alive", [alive for _, alive in events], [0, 0, 0])
c.truthy("  each attempt waited the full limit before counting as failed",
         "still not answering after %d seconds" % guardian.BOOT_LIMIT in log)
c.truthy("  it gave up, and said so", "will not start after 3 attempts" in log)
c("  and nothing is left loading after giving up", [p.poll() is None for p in procs], [False] * 3)

print("\nA copy that crashes on the way up is noticed at once:")
events, procs, log, _ = scenario(up_after=None, dies_after=3, cycles=3)
c.truthy("  reported as exiting, with where to look", "exited during start-up" in log and "arc-server.log" in log)
c("  not after waiting out the limit", "still not answering after" in log, False)

print("\nWhatever holds the port is stopped only if it is Python:")
_, _, log, killed = scenario(up_after=20, cycles=3, holder=(4242, "pythonw.exe"))
c.truthy("  a hung pythonw on the port is stopped", any("4242" in a for a in killed))
_, _, log, killed = scenario(up_after=20, cycles=3, holder=(4343, "sqlserver.exe"))
c("  something else is left alone", killed, [])
c.truthy("  and the log says what is in the way", "not ARC" in log and "sqlserver.exe" in log)

print("\nA server that is busy, not dead, is left alone:")
events, procs, log, killed = scenario(slow_but_alive=True, cycles=6, holder=(4242, "pythonw.exe"))
c("  nothing was stopped", killed, [])
c("  and nothing was started on top of it", len(events), 0)
c.truthy("  the log says it was slow, not dead", "slow to answer, not dead" in log)

print("\nOnly the process on the port is stopped, and only a real stop is logged:")
_, _, log, killed = scenario(up_after=20, cycles=3, holder=(4242, "pythonw.exe"))
c("  no /T, so the app window it opened survives", any("/T" in a for a in killed), False)
_, _, log, killed = scenario(up_after=20, cycles=3, holder=(4242, "pythonw.exe"), kill_rc=1)
c("  a refused stop is not called a stop", "stopped a pythonw" in log, False)
c.truthy("  it is called what it was", "could not stop pid 4242" in log)

print("\nThe port is found whatever language Windows speaks:")
guardian_port = guardian.PORT
GERMAN = """
  Proto  Lokale Adresse         Remoteadresse          Status           PID
  TCP    127.0.0.1:18499        0.0.0.0:0              ABHÖREN          1111
  TCP    127.0.0.1:8499         0.0.0.0:0              ABHÖREN          2222
  TCP    127.0.0.1:8499         127.0.0.1:50000        HERGESTELLT      2222
"""
c("  German netstat, and 18499 is not 8499", guardian._listener_in(GERMAN), 2222)
IPV6 = "  TCP    [::]:8499              [::]:0                 LISTENING       3333\n"
c("  an IPv6 listener", guardian._listener_in(IPV6), 3333)
c("  a connection is not a listener",
  guardian._listener_in("  TCP  127.0.0.1:8499  127.0.0.1:1  ESTABLISHED  9\n"), 0)

print("\nThe reason is on record:")
src = io.open(ARC / "guardian.py", encoding="utf-8").read()
c.truthy("  in the guardian itself", "six copies" in src)
c("  the single fixed look is gone", "time.sleep(GRACE)" in src, False)

c.done()
