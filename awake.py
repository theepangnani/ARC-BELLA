#!/usr/bin/env python3
"""Keeping the computer awake while an alarm is waiting to go off.

An alarm is a promise to make a noise at seven tomorrow, and ARC runs on the
machine that has to make it. This one goes to sleep on an idle timer — Windows'
own power log shows it did four times in two days — and a computer that is
asleep at seven does not ring anything. The alarm was then written off silently
on wake (alarm.STALE_AFTER), and nobody found out it hadn't gone off.

The irony worth writing down: run.py already holds the SESSION's idle clock
off while an alarm is armed (ARC_ALARM_KEEPS_SESSION), so the browser tab would
still be signed in at seven. Nothing did the same for the machine underneath it.

HOW. SetThreadExecutionState(ES_SYSTEM_REQUIRED), WITHOUT ES_CONTINUOUS. That
form does not set a lasting state; it resets the system's idle timer once, and
has to be repeated. It is repeated by the background loop — the same loop that
rings the alarms, every thirty seconds. That is the point of doing it there and
not in a thread of its own: if the loop stops, the pinging stops, and the
machine is free to sleep again. Keeping a computer awake for an alarm loop that
can no longer ring anything would be the worst of both.

WHAT IT CANNOT DO, and says so rather than pretend:
  · It stops IDLE sleep only. The power button, the Start menu's Sleep, a lid
    closing, a flat battery — Windows is right not to let an application
    overrule any of those. For those, alarm.py now says the alarm was missed
    instead of writing it off without a word.
  · It keeps the SYSTEM awake, not the display. The screen still turns off;
    the machine behind it stays on, and so does the sound.
  · Every night that has an alarm in it, the PC does not idle-sleep. A daily
    alarm means it never does. That is the trade that was asked for, and it is
    one environment variable to undo: ARC_ALARM_KEEPS_AWAKE=0.

This machine uses classic S3 sleep (powercfg /a), which is the model this API
is documented against. On a Modern Standby machine the screen timing out takes
a different route to standby; that is noted here rather than silently assumed.
"""

import os
import sys
import time

ES_SYSTEM_REQUIRED = 0x00000001

ENABLED = os.getenv("ARC_ALARM_KEEPS_AWAKE", "1").strip().lower() not in (
    "0", "false", "no", "off")

_state = {"holding": False, "since": 0.0, "pings": 0, "error": ""}


def _ping() -> bool:
    """Reset the system idle timer once. True if Windows accepted it."""
    if sys.platform != "win32":
        return False
    import ctypes
    # Returns the previous state, or 0 on failure.
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED))


def hold(needed: bool) -> bool:
    """Called once per background-loop cycle. Keeps the machine awake while
    `needed`, and does nothing otherwise — which lets it sleep normally.

    Returns whether it is holding. Never raises: the loop that calls this is
    the loop that rings alarms, and nothing about staying awake is worth
    stopping it for.
    """
    try:
        want = bool(needed) and ENABLED
        if not want:
            if _state["holding"]:
                _state.update(holding=False, since=0.0)
                print("  · nothing waiting to go off - the PC may sleep again")
            return False
        ok = _ping()
        if ok:
            _state["pings"] += 1
            _state["error"] = ""
            if not _state["holding"]:
                _state.update(holding=True, since=time.time())
                print("  · an alarm is set - keeping the PC awake until it has gone off")
        else:
            _state["error"] = ("not on Windows" if sys.platform != "win32"
                               else "Windows refused the request")
        return ok
    except Exception as e:
        _state["error"] = str(e)[:200]
        return False


def status() -> dict:
    """For /api/health and the self-check: whether the PC is being held, and
    why not if an alarm is set and it isn't."""
    return {"enabled": ENABLED, "holding": _state["holding"],
            "since": _state["since"], "error": _state["error"]}
