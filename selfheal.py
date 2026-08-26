#!/usr/bin/env python3
"""
Self-repair — ARC fixing ARC, and being straight about the rest.

Something that runs unattended on your own machine breaks in ways nobody is
watching for. The power goes at the wrong moment and notes.json is half a file.
A background loop dies at three in the morning and the alarm set for seven
simply never rings — no error, no message, just silence where a noise should
have been. The voice cache goes stale and every reply comes back in one of
eight English voices. None of that announces itself. The only symptom is an
assistant that quietly does less than it used to, which is exactly the kind of
failure a person is least likely to catch.

So this module does three things, in this order:

  1. Notices. check() looks at the things that actually break rather than the
     things that are easy to count.
  2. Fixes what it can fix, with a copy taken first.
  3. Says plainly what it cannot. An empty Anthropic balance, a dead Google
     refresh token, a full disk — those need a person, and pretending
     otherwise would be worse than not trying.

What it will never do, deliberately:

  · It does not edit its own source. A program that rewrites its own code
    cannot be reviewed, and from the outside "it repaired itself" and "it
    broke itself in a way nobody can read" are the same sentence.
  · It does not touch .env, credentials*.json or token.json. Nothing whose job
    is tidying data files should have write access to the secrets.
  · It does not install packages or download anything. A repair that fetches
    code off the internet is a supply chain, not a repair.
  · It does not delete anything a person wrote without copying it first.

That last one is the whole design. Restoring is only possible if something was
saved, so the snapshots come first and the repairs second. Without that order,
"repair" means "replace your notes with an empty list", which is data loss with
a reassuring name on it.
"""

import io
import json
import os
import shutil
import time
import threading
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
BACKUPS = DATA_DIR / "backups"
REPAIR_LOG = DATA_DIR / "repairs.log"

_lock = threading.RLock()

# The personal files ARC owns: what an empty one looks like, and what to call
# it out loud. Anything here can be snapshotted and restored.
DATA_FILES = {
    "notes.json":        ([], "notes"),
    "todos.json":        ([], "to-do list"),
    "reminders.json":    ([], "reminders"),
    "alarms.json":       ([], "alarms"),
    "price_alerts.json": ([], "price alerts"),
}

# Never copied into backups/, each for its own reason:
#
#   sessions.json  — live sign-ins. Restoring an older copy would resurrect
#                    sessions somebody deliberately revoked, so a corrupt store
#                    is emptied (everyone signs in again, which is a nuisance)
#                    rather than rolled back (which is a security hole).
#   voices.json    — a cache. The fix is to fetch it again, not to keep a copy
#                    of what Microsoft was offering last year.
#   .env, credentials*.json, token.json, google_sessions/ — secrets. Copying a
#                    credential into a backup folder to protect it from
#                    corruption is not a trade worth making.
KEEP_OUT = {".env", "credentials.json", "credentials_web.json", "token.json",
            "sessions.json", "voices.json"}

KEEP_SNAPSHOTS = 6          # per file; roughly a day of six-hourly saves
LOG_MAX_BYTES = 4 * 1024 * 1024
DISK_LOW_MB = 200
SNAPSHOT_EVERY = 6 * 3600

# The background loop ticks every 30 seconds. Two and a half minutes of silence
# is not a slow cycle, it is a loop that has died or wedged — and that loop is
# what rings the alarms.
STALL_SECONDS = 150


# --- heartbeat --------------------------------------------------------------
# A task that raises is easy to spot: it finishes. A task blocked forever
# inside a call it will never come back from looks perfectly healthy from the
# outside — same object, not done, never running. The only way to tell those
# apart is to have it say so each time round, and to notice when it stops.

_beat = {"at": 0.0, "cycles": 0, "started": 0.0, "restarts": 0}


def beat() -> None:
    """Called at the end of every background cycle."""
    with _lock:
        if not _beat["started"]:
            _beat["started"] = time.time()
        _beat["at"] = time.time()
        _beat["cycles"] += 1


def started() -> None:
    """Called when the loop is created, before its first cycle finishes."""
    with _lock:
        _beat["started"] = time.time()


def beat_age() -> float:
    """Seconds since the loop last finished a cycle. -1 means never."""
    with _lock:
        if not _beat["at"]:
            return -1.0
        return time.time() - _beat["at"]


def beats() -> dict:
    with _lock:
        return dict(_beat)


# --- hooks ------------------------------------------------------------------
# Two repairs need things only run.py holds: the background task itself, and
# the orphan-token prune that knows about the session store. Rather than import
# run.py from here — a cycle, and it would drag the whole server into a test
# that wants one function — run.py hands them over at startup.

_hooks: dict = {}


def register(name: str, fn) -> None:
    _hooks[name] = fn


def _hook(name: str):
    fn = _hooks.get(name)
    if not fn:
        return None
    try:
        return fn()
    except Exception as e:
        return "failed: %s" % e


# --- log --------------------------------------------------------------------

def log(line: str) -> None:
    """Every repair is written down.

    Something that changes files while nobody is looking has to leave a record,
    or the first question after a surprise — "what happened to my notes?" — has
    no answer at all.
    """
    try:
        REPAIR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with io.open(REPAIR_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except Exception:
        pass


def history(limit: int = 20) -> list:
    try:
        lines = io.open(REPAIR_LOG, encoding="utf-8").read().splitlines()
    except Exception:
        return []
    return lines[-limit:]


# --- snapshots --------------------------------------------------------------

def _read_json(path: Path):
    """(data, error). The error is a sentence, not a traceback."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as e:
        return None, "not valid JSON (line %d)" % getattr(e, "lineno", 0)
    except Exception as e:
        return None, str(e)[:120]


def _stamp() -> str:
    """A name that sorts chronologically and does not collide.

    Milliseconds matter here: two saves in the same second would land on the
    same filename, and the second would silently replace the first — losing a
    copy at the one moment a copy is worth having.
    """
    now = time.time()
    return "%s-%03d" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)),
                        int(now * 1000) % 1000)


def _snapshots(name: str) -> list:
    # Sorted by the timestamp in the name rather than by mtime: a restore is a
    # copy, and copying can reset mtime on some filesystems.
    try:
        return sorted(BACKUPS.glob(name + ".*.bak"), key=lambda p: p.name,
                      reverse=True)
    except Exception:
        return []


def snapshot(force: bool = False) -> list:
    """Copy the personal files somewhere a repair can reach them.

    Two rules do most of the work. A file is only ever snapshotted if it parses
    — copying a damaged file over the good copies would destroy the one thing
    that makes recovery possible, and would do it at precisely the moment
    recovery was needed. And identical content is not copied twice, because
    this runs on a timer, and six copies of a file nobody touched would push
    out the copy you actually wanted.
    """
    made = []
    with _lock:
        try:
            BACKUPS.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ["could not create %s: %s" % (BACKUPS, e)]

        stamp = _stamp()
        for name in DATA_FILES:
            # KEEP_OUT is a guard, not a comment. If someone ever adds
            # sessions.json to DATA_FILES, this is what stops a live sign-in
            # store being copied into a backup directory.
            if name in KEEP_OUT:
                continue
            src = DATA_DIR / name
            if not src.exists():
                continue
            _, err = _read_json(src)
            if err:
                continue                     # damaged: leave the good copies alone
            try:
                body = src.read_bytes()
            except Exception:
                continue
            have = _snapshots(name)
            if have and not force:
                try:
                    if have[0].read_bytes() == body:
                        continue             # nothing has changed since last time
                except Exception:
                    pass
            try:
                (BACKUPS / ("%s.%s.bak" % (name, stamp))).write_bytes(body)
                made.append(name)
            except Exception:
                continue
            for old in _snapshots(name)[KEEP_SNAPSHOTS:]:
                try:
                    old.unlink()
                except Exception:
                    pass
    return made


def _restore(name: str):
    """Newest snapshot that parses. Returns (path, data), or (None, None).

    Newest that PARSES, not simply newest: if damage arrived a while ago it may
    have been copied before anyone noticed, and walking back until something
    reads is the difference between a restore and a second copy of the problem.
    """
    for snap in _snapshots(name):
        data, err = _read_json(snap)
        if not err:
            return snap, data
    return None, None


def _stamp_of(snap) -> str:
    try:
        raw = snap.name.split(".")[-2]
        return "%s-%s-%s at %s:%s" % (raw[0:4], raw[4:6], raw[6:8],
                                      raw[9:11], raw[11:13])
    except Exception:
        return "earlier"


# --- checks -----------------------------------------------------------------
#
# A finding is a dict, so the same object goes out of the API and into a spoken
# sentence. level is ok / warn / broken; fix names an entry in REPAIRS, or is
# empty when the answer is a person rather than a function.

def _f(fid, level, what, detail="", fix=""):
    return {"id": fid, "level": level, "ok": level == "ok",
            "what": what, "detail": detail, "fix": fix}


def _check_writable():
    probe = DATA_DIR / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return _f("writable", "ok", "Data folder is writable")
    except Exception as e:
        # Nothing below can help with this one. Every repair here writes.
        return _f("writable", "broken", "I cannot write to my data folder",
                  "%s — %s. Nothing I save will survive until that is fixed."
                  % (DATA_DIR, e))


def _check_data():
    out = []
    for name, (empty, label) in DATA_FILES.items():
        path = DATA_DIR / name
        if not path.exists():
            continue                  # never used the feature; not a fault
        data, err = _read_json(path)
        if err:
            snap, _ = _restore(name)
            detail = "%s is %s." % (name, err)
            detail += (" I have a good copy from %s to put back." % _stamp_of(snap)
                       if snap else
                       " I have no earlier copy, so I would have to start it empty.")
            out.append(_f("data", "broken", "Your %s file is damaged" % label,
                          detail, fix="data"))
        elif not isinstance(data, type(empty)):
            out.append(_f("data", "broken", "Your %s file is the wrong shape" % label,
                          "%s holds a %s where a %s belongs."
                          % (name, type(data).__name__, type(empty).__name__),
                          fix="data"))
    return out or [_f("data", "ok",
                      "Your notes, reminders and alarms all read cleanly")]


def _check_backups():
    live = [n for n in DATA_FILES if (DATA_DIR / n).exists()]
    if not live:
        return _f("backups", "ok", "Nothing to back up yet")
    have = [n for n in live if _snapshots(n)]
    missing = [n for n in live if n not in have]
    if missing:
        return _f("backups", "warn", "Some files have never been backed up",
                  "No copy of: %s. Without one, damage to them is permanent."
                  % ", ".join(missing), fix="backups")
    newest = max(s[0].stat().st_mtime for s in (_snapshots(n) for n in have))
    age = (time.time() - newest) / 3600
    if age > 48:
        return _f("backups", "warn", "Backups are getting old",
                  "The newest copy is %.0f hours old." % age, fix="backups")
    return _f("backups", "ok", "Backed up",
              "%d file%s, newest %.0f hours old."
              % (len(have), "" if len(have) == 1 else "s", age))


def _check_heartbeat():
    age = beat_age()
    if age < 0:
        # Before the first cycle finishes this is normal, so it reads as
        # starting up rather than as a fault. Three minutes later it is a fault.
        began = beats()["started"]
        if began and time.time() - began > STALL_SECONDS:
            return _f("heartbeat", "broken", "My background loop never started",
                      "Alarms, reminders and price alerts are not being checked "
                      "at all.", fix="heartbeat")
        return _f("heartbeat", "ok", "Background loop is starting up")
    if age > STALL_SECONDS:
        return _f("heartbeat", "broken", "My background loop has stopped",
                  "Nothing has ticked for %.0f seconds. While that is true, an "
                  "alarm set for the morning will not ring and reminders will "
                  "not arrive." % age, fix="heartbeat")
    return _f("heartbeat", "ok", "Background loop is running",
              "%d cycles, the last one %.0fs ago." % (beats()["cycles"], age))


def _check_disk():
    try:
        free = shutil.disk_usage(str(DATA_DIR)).free / (1024 * 1024)
    except Exception:
        return None
    if free < DISK_LOW_MB:
        # No repair offered. Deleting things to make room is exactly the sort
        # of initiative nobody wants from an assistant.
        return _f("disk", "broken", "The disk is nearly full",
                  "%.0f MB left. Saves will start failing, and a failed save is "
                  "how files get truncated in the first place." % free)
    return _f("disk", "ok", "Disk has room", "%.1f GB free." % (free / 1024))


def _log_files():
    out = []
    for base in {ROOT, DATA_DIR}:
        try:
            out.extend(p for p in base.glob("*-by-arc*.log") if p.is_file())
        except Exception:
            pass
    return sorted(set(out))


def _check_logs():
    big = [p for p in _log_files() if p.stat().st_size > LOG_MAX_BYTES]
    if big:
        return _f("logs", "warn", "Audit logs have grown large",
                  ", ".join("%s (%.1f MB)" % (p.name, p.stat().st_size / 1048576)
                            for p in big), fix="logs")
    return _f("logs", "ok", "Audit logs are a sensible size")


def _check_automation():
    try:
        import automation
    except Exception:
        return None
    if not automation.running():
        return _f("automation", "ok", "Nothing is auto-clicking")
    return _f("automation", "warn", "Something is still auto-clicking",
              automation.automation_status(), fix="automation")


def _check_sessions():
    try:
        import session
        n = session.count()
    except Exception:
        return None
    return _f("sessions", "ok", "Sign-ins look normal",
              "%d live session%s." % (n, "" if n == 1 else "s"), fix="sessions")


def _check_voices():
    try:
        import voices
        # loaded(), not catalogue(): the latter would go to the network, and a
        # health check that hangs for twenty seconds on a dead connection has
        # become the fault it was looking for.
        cat = voices.loaded()
    except Exception as e:
        return _f("voices", "warn", "The voice list could not be read",
                  str(e)[:120], fix="voices")
    if cat is None:
        return _f("voices", "ok", "Voices not loaded yet",
                  "They load the first time something speaks.")
    if len(cat) <= len(voices.FALLBACK):
        return _f("voices", "warn", "Only the English fallback voices are loaded",
                  "Every other language would be read by an English voice, or "
                  "not at all. Usually it means the list could not be fetched.",
                  fix="voices")
    return _f("voices", "ok", "Voices loaded",
              "%d voices across %d locales." % (len(cat), len(voices.languages())))


def _check_google():
    out = []
    for mod, label in (("gcal", "Calendar"), ("gmail", "Gmail")):
        try:
            up = __import__(mod).connected()
        except Exception:
            continue
        if not up:
            # A refresh token Google has expired cannot be renewed from this
            # side. "Sign in again" is the honest fix; retrying silently for
            # ever is what makes something feel broken.
            out.append(_f(mod, "warn", "%s is not connected" % label,
                          "Sign in again from the account panel to reconnect it."))
    return out


def _check_claude():
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        return _f("claude", "broken", "No Anthropic API key is set",
                  "I cannot think at all without one. Put ANTHROPIC_API_KEY in "
                  ".env and restart me.")
    return _f("claude", "ok", "The Anthropic key is present",
              "Whether it has credit on it, only an actual request can say.")


OPTIONAL = [("PIL", "Screenshots and screen-watching", "pip install pillow"),
            ("segno", "The phone pairing QR code", "pip install segno"),
            ("telethon", "Telegram", "pip install telethon")]


def _check_deps():
    import importlib.util
    out = []
    for mod, what, how in OPTIONAL:
        if importlib.util.find_spec(mod) is None:
            # No auto-install. Downloading and running code because a check
            # failed is not a repair, whatever it gets called.
            out.append(_f("deps", "warn", "%s is unavailable" % what,
                          "The %s library is not installed. Run: %s" % (mod, how)))
    return out


def _probe(fn):
    """Run one check without letting it take the others with it.

    Every probe here touches something that can fail for reasons of its own —
    a disk that answers slowly, a file deleted between the glob and the stat, a
    module that raises on import. Letting one of those end the whole report is
    precisely the failure this module exists to prevent: the health check dies,
    the page gets a 500, and the answer to "what is wrong with you" becomes
    nothing at all rather than the nine things that were perfectly readable.

    So a probe that throws becomes a finding about itself, and the rest run.
    """
    try:
        out = fn()
    except Exception as e:
        return [_f("check", "warn", "One of my own checks failed",
                   "%s raised %s: %s. Everything else below still ran."
                   % (getattr(fn, "__name__", "a check"), type(e).__name__,
                      str(e)[:120]))]
    if out is None:
        return []
    return out if isinstance(out, list) else [out]


CHECKS = (_check_writable, _check_data, _check_heartbeat, _check_backups,
          _check_disk, _check_logs, _check_automation, _check_sessions,
          _check_voices, _check_claude, _check_google, _check_deps)


def check(deep: bool = False) -> list:
    """Everything, worst first. Cheap enough to sit behind a button."""
    found = []
    for fn in CHECKS:
        found += _probe(fn)
    order = {"broken": 0, "warn": 1, "ok": 2}
    return sorted(found, key=lambda f: order.get(f["level"], 3))


# --- repairs ----------------------------------------------------------------

def _fix_data() -> list:
    """Set aside what is damaged, put back the newest copy that reads.

    The damaged file is renamed, never deleted. It may be nine tenths intact
    and worth an hour with a text editor, and that judgement belongs to the
    person whose notes they are.
    """
    done = []
    for name, (empty, label) in DATA_FILES.items():
        if name in KEEP_OUT:
            continue                    # see snapshot(): the guard, not a comment
        path = DATA_DIR / name
        if not path.exists():
            continue
        data, err = _read_json(path)
        if not err and isinstance(data, type(empty)):
            continue
        try:
            BACKUPS.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, BACKUPS / ("%s.damaged-%s" % (name, _stamp())))
        except Exception as e:
            done.append("could not set the damaged %s aside (%s), so I left it "
                        "alone rather than risk losing it" % (name, e))
            continue
        snap, good = _restore(name)
        try:
            if snap is not None:
                path.write_text(json.dumps(good, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                done.append("restored your %s from the copy saved %s (%d item%s) — "
                            "the damaged file is in backups/"
                            % (label, _stamp_of(snap), len(good),
                               "" if len(good) == 1 else "s"))
            else:
                path.write_text(json.dumps(empty), encoding="utf-8")
                done.append("your %s file was unreadable and I had no earlier copy, "
                            "so I started it empty — the damaged one is in backups/ "
                            "if you want to pick through it" % label)
        except Exception as e:
            done.append("could not rewrite %s: %s" % (name, e))
    return done


def _fix_logs() -> list:
    done = []
    for p in _log_files():
        try:
            if p.stat().st_size <= LOG_MAX_BYTES:
                continue
            # Rotated, not truncated. These are the record of what ARC ran and
            # what it sent; deleting them to save disk would throw away the
            # evidence rather than the problem.
            old = p.with_name(p.name + "." + time.strftime("%Y%m%d-%H%M%S"))
            p.rename(old)
            done.append("rotated %s (%.1f MB)"
                        % (p.name, old.stat().st_size / 1048576))
        except Exception as e:
            done.append("could not rotate %s: %s" % (p.name, e))
    return done


def _fix_sessions() -> list:
    done = []
    try:
        import session
        before = session.count()
        session.sweep()
        gone = before - session.count()
        if gone > 0:
            done.append("cleared %d expired sign-in%s"
                        % (gone, "" if gone == 1 else "s"))
    except Exception as e:
        done.append("could not sweep sessions: %s" % e)
    n = _hook("prune_tokens")
    if isinstance(n, int) and n:
        done.append("removed %d Google token%s no session could reach"
                    % (n, "" if n == 1 else "s"))
    return done


def _fix_voices() -> list:
    try:
        import voices
        cat = voices.catalogue(refresh=True)
        return ["refetched the voice list (%d voices, %d locales)"
                % (len(cat), len(voices.languages()))]
    except Exception as e:
        return ["could not refetch the voice list: %s" % e]


def _fix_automation() -> list:
    try:
        import automation
        return [automation.stop_automation().rstrip(".").lower()]
    except Exception as e:
        return ["could not stop the auto-clicker: %s" % e]


def _fix_heartbeat() -> list:
    """Restart the background loop.

    An honest limit: if the old loop is wedged inside a call that never
    returns, cancelling its task does not free the thread underneath — Python
    has no way to. The new loop runs regardless, so alarms start being checked
    again, and the stuck thread ends whenever it ends. That is a leak, and it
    is much the smaller of the two problems.
    """
    r = _hook("restart_monitor")
    if r is None:
        return ["I have no way to restart my background loop from here; "
                "restarting ARC will do it"]
    if isinstance(r, str) and r.startswith("failed"):
        return ["could not restart the background loop (%s)" % r]
    with _lock:
        _beat["restarts"] += 1
        _beat["at"] = 0.0
        _beat["started"] = time.time()
    return ["restarted my background loop, so alarms and reminders are being "
            "checked again"]


def _fix_backups() -> list:
    made = snapshot(force=True)
    return ["saved a copy of %s" % ", ".join(made)] if made else \
           ["there was nothing to back up"]


REPAIRS = {
    "data": _fix_data,
    "backups": _fix_backups,
    "logs": _fix_logs,
    "sessions": _fix_sessions,
    "voices": _fix_voices,
    "automation": _fix_automation,
    "heartbeat": _fix_heartbeat,
}


def repair(ids=None, findings=None) -> list:
    """Run the named repairs, or every repair the current checks call for.

    Only ids in REPAIRS run. That is the same default-deny idiom as the consent
    gate and the guest tool list: a repair added later does nothing until it is
    named here, rather than quietly acquiring the right to change files.
    """
    if ids is None:
        findings = findings if findings is not None else check()
        ids = []
        for f in findings:
            if f["fix"] and not f["ok"] and f["fix"] not in ids:
                ids.append(f["fix"])
    done = []
    for fid in ids:
        fn = REPAIRS.get(fid)
        if not fn:
            done.append("there is nothing I know how to do about '%s'" % fid)
            continue
        with _lock:
            try:
                done.extend(fn() or [])
            except Exception as e:
                done.append("the %s repair itself failed: %s" % (fid, e))
    for line in done:
        log(line)
    return done


# --- startup and watchdog ---------------------------------------------------

def startup() -> list:
    """Run once at boot.

    Boot is the only moment when nothing is in flight, which makes it the one
    time a damaged file is unambiguous — no writer is halfway through it. So
    the set-aside-and-restore runs here by itself, without asking. Waiting for
    someone to press a button would mean the first save of the day overwrites
    the damaged file with an empty one, and then there is nothing to restore.
    """
    done = []
    findings = check()
    if any(f["level"] == "broken" and f["fix"] == "data" for f in findings):
        done += _fix_data()
    done += _fix_logs()
    made = snapshot()
    if made:
        done.append("backed up %s" % ", ".join(made))
    for line in done:
        log("startup: " + line)
    return done


_last_snapshot = [0.0]

# A restart that does not take must not become a restart every two and a half
# minutes for ever. If the loop is put back three times and never completes a
# single cycle in between, the fault is not one a restart reaches, and going on
# would fill repairs.log with hundreds of identical lines a day — burying the
# one line that says what actually happened.
GIVE_UP_AFTER = 3
_futile = [0, -1]              # consecutive useless restarts, cycles at the last


def watch() -> list:
    """Called on a timer while ARC runs. Silent unless it did something.

    Only two things happen here unasked. A background loop that has stopped
    ticking is restarted, because what it does — ringing alarms — is a promise
    somebody was given, and a promise that fails silently is worse than one
    that fails loudly. And the personal files are copied every few hours,
    because a restore is only ever as good as the last copy.

    Damaged files are NOT repaired from here. Mid-run, a file that will not
    parse might be one something is writing this very instant, and racing a
    writer to "fix" a file is how a small problem becomes a total one.
    """
    done = []
    b = beats()
    stalled = beat_age() > STALL_SECONDS or (
        beat_age() < 0 and b["started"] and time.time() - b["started"] > STALL_SECONDS)
    if stalled:
        _futile[0] = _futile[0] + 1 if b["cycles"] == _futile[1] else 1
        _futile[1] = b["cycles"]
        if _futile[0] <= GIVE_UP_AFTER:
            done += _fix_heartbeat()
        elif _futile[0] == GIVE_UP_AFTER + 1:
            # Said once, then never again. A person asking for the repair by
            # hand still gets an attempt — giving up is the watchdog's rule,
            # not a refusal.
            done.append("I have restarted my background loop %d times and it has "
                        "not completed a single cycle, so this is not a fault a "
                        "restart reaches. Alarms and reminders are not running. "
                        "ARC itself needs restarting." % GIVE_UP_AFTER)
    if time.time() - _last_snapshot[0] > SNAPSHOT_EVERY:
        _last_snapshot[0] = time.time()
        made = snapshot()
        if made:
            done.append("backed up %s" % ", ".join(made))
    for line in done:
        log("watchdog: " + line)
    return done


# --- speaking ---------------------------------------------------------------

def describe(findings=None) -> str:
    """The health report as something to say, not a table to read."""
    findings = findings if findings is not None else check()
    bad = [f for f in findings if f["level"] == "broken"]
    warn = [f for f in findings if f["level"] == "warn"]
    if not bad and not warn:
        return ("Everything checks out. "
                + " ".join("%s." % f["what"] for f in findings[:4]))
    out = []
    if bad:
        out.append("Broken: " + " ".join("%s — %s" % (f["what"], f["detail"])
                                         for f in bad))
    if warn:
        out.append("Worth knowing: " + " ".join("%s — %s" % (f["what"], f["detail"])
                                                for f in warn))
    # What each finding needs is already in its own detail line. Repeating it
    # here under a "you need to do this" heading said everything twice and made
    # a short report read like a long one.
    fixable = sorted({f["fix"] for f in bad + warn if f["fix"]})
    stuck = [f for f in bad + warn if not f["fix"]]
    if fixable and stuck:
        out.append("I can fix %s myself; the rest is above and needs you."
                   % ("the first of those" if len(fixable) == 1 else "some of that"))
    elif fixable:
        out.append("I can fix %s myself if you want."
                   % ("that" if len(fixable) == 1 else "all of that"))
    elif stuck:
        out.append("None of that is something I can repair from in here.")
    return " ".join(out)


# --- tool surface -----------------------------------------------------------

def connected() -> bool:
    # ARC can always look at itself.
    return True


def self_check() -> str:
    return describe()


def self_repair(what: str = "") -> str:
    """Fix what is fixable — a named repair, or everything the checks call for."""
    ids = None
    if (what or "").strip():
        want = what.strip().lower()
        ids = [k for k in REPAIRS if k in want]
        if not ids:
            return ("I can repair: %s. I did not recognise '%s', so I have done "
                    "nothing." % (", ".join(sorted(REPAIRS)), what))
    findings = check()
    done = repair(ids, findings=findings)
    if not done:
        return "Nothing needed repairing. " + describe(findings)
    still = [f for f in check() if f["level"] == "broken"]
    out = "Done: " + "; ".join(done) + "."
    if still:
        out += (" Still wrong, and not something I can fix: "
                + " ".join("%s — %s" % (f["what"], f["detail"]) for f in still))
    return out


TOOLS = [
    {"name": "self_check",
     "description": (
         "Check ARC's own health — background loop, personal data files, backups, "
         "disk, voices, sign-ins, connected accounts. Use for 'are you okay', 'is "
         "everything working', 'why didn't my alarm go off', 'you seem broken', "
         "'check yourself', 'diagnose yourself'. Reads only; changes nothing."),
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "self_repair",
     "description": (
         "Repair what ARC can repair about itself: restore a damaged notes / "
         "reminders / alarms file from backup, restart a stopped background loop, "
         "refetch the voice list, clear expired sign-ins, rotate oversized logs, "
         "stop a stuck auto-clicker. Use for 'fix yourself', 'repair yourself', "
         "'sort yourself out'. Never edits code and never installs anything, and "
         "it says what it could not fix. Optionally name one repair: data, "
         "backups, heartbeat, voices, sessions, logs, automation."),
     "input_schema": {"type": "object", "properties": {
         "what": {"type": "string", "description":
                  "Optional single repair: data, backups, heartbeat, voices, "
                  "sessions, logs, automation. Omit to fix whatever needs it."}}}},
]

_DISPATCH = {"self_check": self_check, "self_repair": self_repair}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return "No such tool: %s" % name, True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return "Wrong arguments for %s: %s" % (name, e), True
    except Exception as e:
        # The irony of the self-repair module crashing is not lost on anyone,
        # so it reports rather than propagates.
        return "My self-check itself failed: %s" % e, True
