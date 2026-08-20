#!/usr/bin/env python3
"""
Repeating input for ARC — auto-clicker, held keys, short macros.

"Bella, click here every second for two minutes." ARC can already click and
type; what it could not do is keep doing it while you get on with something
else. This is that: a background worker that repeats an input on a timer and
stops on a word.

Three rules shape all of it, and they are all about the same thing — that a
program driving your mouse and keyboard must never be something you have to
fight to switch off:

  1. EVERYTHING IS BOUNDED. Every run has a duration and a count, both capped.
     There is no "until I say stop" mode that outlives the conversation, because
     the one thing worse than an auto-clicker is an auto-clicker you cannot
     catch. Anything running stops on its own within MAX_SECONDS.
  2. STOPPING IS INSTANT AND ALWAYS AVAILABLE. One flag, checked between every
     single event, and stop_automation() sets it. Only one job runs at a time,
     so "stop" is never ambiguous about what it stops.
  3. IT SAYS WHAT IT IS DOING. Starting reports the rate and the duration;
     status reports what is left. Nothing runs silently.

On the games question, plainly: this drives your own machine, which is yours to
drive. But a lot of online games treat input automation as cheating and ban for
it, and that is between you and them — ARC says so once when it starts and does
not moralise further.

Windows only, for the same reason as the rest of computer control.
"""

import os
import sys
import time
import threading

import pc          # reuse the input primitives and the local-only gate

IS_WIN = sys.platform.startswith("win")

# Caps, not defaults. A macro that runs for an hour is not a macro, it is a
# resident program; and 60 clicks a second is beyond anything a person or a
# game does anything useful with.
MAX_SECONDS = int(os.getenv("ARC_AUTOMATION_MAX_SECONDS", "600"))
MAX_RATE = float(os.getenv("ARC_AUTOMATION_MAX_RATE", "40"))
MAX_EVENTS = 20000

_lock = threading.RLock()
_job = None          # {"what","thread","stop","started","until","done","target"}


def connected() -> bool:
    # Same rule as computer control: offered on a non-deployed instance, and
    # run_tool refuses it for a remote caller. Nothing here makes sense over a
    # tunnel — the mouse it would move is not the one in front of you.
    return not pc.CLOUD


# --- the worker ------------------------------------------------------------

def _running():
    j = _job
    return j is not None and j["thread"].is_alive()


def _start(what: str, target: str, seconds: float, fn, interval: float, count: int):
    """One job at a time, always bounded, always interruptible."""
    global _job
    with _lock:
        if _running():
            return (f"Already {_job['what']}. Say stop first — I only run one of "
                    f"these at a time, on purpose.")
        stop = threading.Event()
        state = {"what": what, "target": target, "stop": stop, "done": 0,
                 "started": time.time(), "until": time.time() + seconds,
                 "count": count}

        def loop():
            for _ in range(count):
                if stop.is_set() or time.time() >= state["until"]:
                    break
                try:
                    fn()
                except Exception:
                    break
                state["done"] += 1
                # Wait in slices so stop is felt immediately even on a slow
                # repeat — a two-second interval must not mean a two-second
                # wait to stop.
                end = time.time() + interval
                while time.time() < end:
                    if stop.is_set():
                        return
                    time.sleep(min(0.02, max(0.0, end - time.time())))

        t = threading.Thread(target=loop, daemon=True, name="arc-automation")
        state["thread"] = t
        _job = state
        t.start()
        return None


def _bounds(rate, seconds):
    try:
        rate = float(rate or 5)
    except (TypeError, ValueError):
        rate = 5.0
    try:
        seconds = float(seconds or 30)
    except (TypeError, ValueError):
        seconds = 30.0
    rate = max(0.1, min(rate, MAX_RATE))
    seconds = max(1.0, min(seconds, MAX_SECONDS))
    count = int(min(rate * seconds, MAX_EVENTS))
    return rate, seconds, max(1, count)


# --- tools -----------------------------------------------------------------

def auto_click(rate: float = 5, seconds: float = 30, button: str = "left",
               x=None, y=None) -> str:
    """Click repeatedly, where the pointer is or at a fixed point."""
    if not IS_WIN:
        return pc._unsupported("drive the mouse")
    import ctypes
    u = ctypes.windll.user32
    b = (button or "left").strip().lower()
    down, up = (pc._ME["rdown"], pc._ME["rup"]) if b in ("right", "r") \
        else (pc._ME["ldown"], pc._ME["lup"])
    fixed = x is not None and y is not None
    if fixed:
        try:
            x, y = int(x), int(y)
        except (TypeError, ValueError):
            return "Give x and y as whole numbers of screen pixels."

    rate, seconds, count = _bounds(rate, seconds)

    def one():
        if fixed:
            u.SetCursorPos(x, y)
        u.mouse_event(down, 0, 0, 0, 0)
        u.mouse_event(up, 0, 0, 0, 0)

    where = f"at {x}, {y}" if fixed else "wherever the pointer is"
    err = _start(f"clicking {where}", where, seconds, one, 1.0 / rate, count)
    if err:
        return err
    return (f"Auto-clicking {where}, about {rate:g} times a second for "
            f"{seconds:g} seconds. Say stop and I stop. Worth knowing: plenty of "
            f"online games ban input automation, so keep it to single-player.")


def hold_key(key: str = "", seconds: float = 5) -> str:
    """Hold a key down — walking forward, charging a shot, holding a door."""
    if not IS_WIN:
        return pc._unsupported("press keys")
    k = (key or "").strip().lower()
    if k not in pc._VK:
        return f"I don't know the key '{key}'. Known: {', '.join(sorted(pc._VK))}."
    try:
        seconds = max(0.2, min(float(seconds or 5), MAX_SECONDS))
    except (TypeError, ValueError):
        seconds = 5.0

    import ctypes
    u = ctypes.windll.user32
    vk = pc._VK[k]
    KEYEVENTF_KEYUP = 0x0002

    with _lock:
        if _running():
            return f"Already {_job['what']}. Say stop first."
        stop = threading.Event()
        state = {"what": f"holding {k}", "target": k, "stop": stop, "done": 0,
                 "started": time.time(), "until": time.time() + seconds, "count": 1}

        def hold():
            # The key must come back up whatever happens — an exception, a stop,
            # a timeout. A key left down is the worst failure this file has.
            u.keybd_event(vk, 0, 0, 0)
            try:
                end = time.time() + seconds
                while time.time() < end and not stop.is_set():
                    time.sleep(0.02)
            finally:
                u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                state["done"] = 1

        t = threading.Thread(target=hold, daemon=True, name="arc-automation")
        state["thread"] = t
        globals()["_job"] = state
        t.start()
    return f"Holding {k} for {seconds:g} seconds. Say stop to let go early."


def key_macro(keys: str = "", repeat: int = 1, gap: float = 0.15) -> str:
    """Press a sequence of keys in order, optionally repeated.

    "one, two, three" or "w a s d" — a rotation, a combo, a sequence of menu
    presses. Held modifiers are not supported: this taps.
    """
    if not IS_WIN:
        return pc._unsupported("press keys")
    seq = [k.strip().lower() for k in (keys or "").replace(",", " ").split() if k.strip()]
    if not seq:
        return "Give me the keys in order, like 'one two three' or 'w a s d'."
    unknown = [k for k in seq if k not in pc._VK]
    if unknown:
        return (f"I don't know {', '.join(unknown)}. Known keys: "
                f"{', '.join(sorted(pc._VK))}.")
    try:
        repeat = max(1, min(int(repeat or 1), 500))
    except (TypeError, ValueError):
        repeat = 1
    try:
        gap = max(0.02, min(float(gap or 0.15), 5.0))
    except (TypeError, ValueError):
        gap = 0.15

    import ctypes
    u = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    step = {"i": 0}
    total = len(seq) * repeat
    seconds = min(total * gap + 1, MAX_SECONDS)

    def one():
        k = seq[step["i"] % len(seq)]
        step["i"] += 1
        vk = pc._VK[k]
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

    err = _start(f"running the macro {' '.join(seq)}", " ".join(seq),
                 seconds, one, gap, total)
    if err:
        return err
    return (f"Running {' '.join(seq)}{f', {repeat} times over' if repeat > 1 else ''}"
            f" — {total} key press(es). Say stop to cut it short.")


def stop_automation() -> str:
    """Stop whatever is repeating. The one word that always works."""
    global _job
    with _lock:
        if not _running():
            _job = None
            return "Nothing was running."
        j = _job
        j["stop"].set()
    j["thread"].join(timeout=2.0)
    done = j["done"]
    _job = None
    return f"Stopped. It got through {done}."


def automation_status() -> str:
    with _lock:
        if not _running():
            return "Nothing is running."
        j = _job
        left = max(0, j["until"] - time.time())
        return (f"Currently {j['what']} — {j['done']} so far, about "
                f"{left:.0f} seconds left. Say stop to end it.")


def running() -> bool:
    """For run.py's banner and anything else that wants to know."""
    with _lock:
        return _running()


TOOLS = [
    {"name": "auto_click",
     "description": (
         "Click the mouse repeatedly on its own, for gaming, idle games, or any "
         "repetitive clicking. Use for 'auto click', 'keep clicking', 'click here "
         "every second', 'clicker'. Rate is clicks per second (default 5, max 40), "
         "seconds is how long (default 30, max 600). With x and y it clicks a fixed "
         "point; without, wherever the pointer is. Always stoppable with "
         "stop_automation."),
     "input_schema": {"type": "object", "properties": {
         "rate": {"type": "number", "description": "Clicks per second"},
         "seconds": {"type": "number", "description": "How long to run"},
         "button": {"type": "string", "description": "left or right"},
         "x": {"type": "number"}, "y": {"type": "number"}},
         "required": []}},

    {"name": "hold_key",
     "description": (
         "Hold a key down for a number of seconds — walking forward in a game, "
         "charging an attack, holding a button. Use for 'hold W', 'keep pressing "
         "forward', 'hold shift for ten seconds'. The key is always released, even "
         "if stopped early."),
     "input_schema": {"type": "object", "properties": {
         "key": {"type": "string"},
         "seconds": {"type": "number"}},
         "required": ["key"]}},

    {"name": "key_macro",
     "description": (
         "Press a sequence of keys in order, optionally repeated — a combo, a "
         "rotation, a run of menu presses. Use for 'press one two three over and "
         "over', 'do W A S D five times'. Keys are space or comma separated; gap is "
         "the pause between presses in seconds."),
     "input_schema": {"type": "object", "properties": {
         "keys": {"type": "string", "description": "Keys in order, e.g. 'one two three'"},
         "repeat": {"type": "integer"},
         "gap": {"type": "number"}},
         "required": ["keys"]}},

    {"name": "stop_automation",
     "description": ("Stop any auto-clicking, held key or macro immediately. Use for "
                     "'stop', 'stop clicking', 'let go', 'cancel that' while something "
                     "is repeating."),
     "input_schema": {"type": "object", "properties": {}, "required": []}},

    {"name": "automation_status",
     "description": "What is currently repeating, and how much of it is left.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
]

_DISPATCH = {"auto_click": auto_click, "hold_key": hold_key, "key_macro": key_macro,
             "stop_automation": stop_automation, "automation_status": automation_status}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"That didn't work: {e}", True
