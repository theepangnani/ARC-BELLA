#!/usr/bin/env python3
"""
The law: ARC never changes her own code.

The owner's words, when asked whether it should be "never" or "only when I am
signed in": never, locked in code. Not a preference ARC holds, a thing the
server refuses — so it does not depend on the model remembering a rule, on who
is signed in, or on how persuasive a sentence in an email is.

WHY "NEVER" AND NOT "ONLY THE OWNER". A signed-in owner is exactly the session
a prompt injection rides in on: a web page or a mail ARC reads while the owner
is signed in carries the owner's authority as far as any email check can tell.
Code that rewrites itself also cannot be reviewed — "it improved itself" and
"it broke itself unreadably" look identical from outside. Code changes happen
through the owner, an editor and git, where there is a diff and a commit.

WHAT THIS WAS BEFORE. The rule already existed — selfheal refuses to edit
source, and the prompt says "you never edit it, and never offer to" — but only
as words. prepare_command/run_prepared will run any shell command the user says
yes to, and a shell command can rewrite run.py. keyboard and key_macro type into
whatever is in front, and what is in front can be an editor with run.py open or
a terminal. So the rule held exactly as long as the model and the spoken yes
both did.

WHAT IS LOCKED, and where each is checked:
  · the shell     — a command that names this folder, a file in it, or git, or
                    that hides what it runs (-EncodedCommand, iex, base64), is
                    refused at prepare AND again at run. Fails closed: a false
                    refusal costs a rephrase, a false pass costs the law.
  · typing        — keyboard, key_macro, hold_key refuse when the window in
                    front is a terminal (typing into a shell IS the shell, with
                    none of the checks above), or an editor showing this code.
  · clicking      — mouse clicks and auto_click refuse on the same windows: a
                    click is how "Save", "Discard" and "Commit" get pressed.
  · after the fact — every command that does run is bracketed by a snapshot of
                    the code files. If one changed anyway, the shell locks for
                    the rest of this process and the owner is told. It cannot
                    un-change it: reverting would also destroy edits the owner
                    was making in an editor at the same moment.

WHAT IT IS HONEST ABOUT. A string check on a shell command is not a sandbox.
Somebody determined enough, at the desktop, saying yes to a command they have
read aloud, can write a command this does not recognise — which is why the
after-the-fact check exists, and why it locks rather than warns. The only
airtight version is ARC running as a Windows account with no write access to
this folder; that is an install choice, not something code can make for itself.
"""

import os
import re
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

# The sentence every refusal starts with, so the model, the log and the tests
# all recognise the law rather than an ordinary failure.
LAW = "Refused by ARC's code lock: I never change my own code"

# Code, config and instructions — everything that decides what ARC does.
# Deliberately NOT .json: on the shared instance the data directory IS this
# folder, and notes.json, alarms.json and the rest are data ARC is meant to
# write. Folders listed in _CODE_DIRS are locked whole, whatever the extension.
_CODE_EXT = {
    ".py", ".pyw", ".html", ".htm", ".js", ".mjs", ".css", ".md", ".ps1",
    ".psm1", ".vbs", ".bat", ".cmd", ".sh", ".yml", ".yaml", ".toml", ".cfg",
    ".ini", ".txt", ".svg", ".ico", ".webmanifest", ".example",
}
_CODE_NAMES = {"LICENSE", ".gitignore", ".gitattributes"}
_CODE_DIRS = {"static", "prompts", "tests", "docs", ".github"}
# Never descended into: runtime state, caches, a browser profile, the git store
# (which is locked as a whole by name, below, rather than listed file by file).
_SKIP_DIRS = {".git", "__pycache__", "backups", "google_sessions", ".arc-window",
              "node_modules", ".venv", "venv"}

# Programs whose window IS a shell. Typing into one is running commands with
# none of the shell checks, so these are refused whatever their title says.
_TERMINALS = {
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe",
    "conhost.exe", "openconsole.exe", "wsl.exe", "bash.exe", "mintty.exe",
    "alacritty.exe", "wezterm-gui.exe", "putty.exe", "python.exe", "pythonw.exe",
}


def code_files() -> list:
    """Every file the lock covers, as paths. Walked fresh each call: a file
    added since boot is covered from the moment it exists."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel = Path(dirpath).relative_to(ROOT)
        whole = bool(rel.parts) and rel.parts[0] in _CODE_DIRS
        for name in filenames:
            p = Path(dirpath) / name
            if whole or name in _CODE_NAMES or p.suffix.lower() in _CODE_EXT:
                out.append(p)
    return out


def _folder_names() -> list:
    """This folder's name, and its parent's when the parent is ARC's own
    (C:\\dev\\arc-voice-assistant\\arc) rather than somewhere general. A
    parent that is the home folder or a drive root would make every command
    that mentions the user's own files a refusal."""
    names = [ROOT.name.lower()]
    parent = ROOT.parent
    if parent != Path.home() and parent.parent != parent and len(parent.name) > 3:
        names.append(parent.name.lower())
    return [n for n in names if len(n) > 2]


def _names() -> set:
    """The distinctive names a command or a window title would use for this
    code: every locked file's name, and this folder's own path and name."""
    names = {p.name.lower() for p in code_files()}
    names.discard("")
    return names


# --- the shell ---------------------------------------------------------------

# Ways of running something without saying what it is. A check that reads the
# command cannot read these, so a command containing one is refused outright.
_HIDDEN = re.compile(
    r"(?i)(-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{16,}"   # powershell -enc <b64>
    r"|\biex\b|invoke-expression|frombase64string|\[convert\]::"
    r"|certutil\b.*-decode|\bscriptblock\b|add-type\b"
    r"|\bexec\s*\(|\beval\s*\(|python[0-9.]*\s+-c\b|\bnode\s+-e\b)")
# git rewrites the working tree (checkout, pull, reset, apply, stash) — and a
# pull on the desktop changes what is running. The owner runs git, ARC does not.
_GIT = re.compile(r"(?i)(^|[\s;&|(\"'`])git(\.exe)?(\s|$)")
# A package install changes the code ARC runs just as surely as an edit.
_PIP = re.compile(r"(?i)\b(pip3?|uv|poetry|conda)(\.exe)?\s+(install|uninstall|add|remove)\b"
                  r"|-m\s+pip\b")


def _norm(s: str) -> str:
    return (s or "").replace("/", "\\").lower()


# A drive-letter path, quoted or not. Commands name files by path far more
# often than by bare name, and a path is the one thing that can be checked
# properly: resolved, so "..", 8.3 short names (ARC-BE~1) and junctions all
# land on the folder they really mean.
_ABS_PATH = re.compile(r'"([a-z]:[\\/][^"]*)"|\'([a-z]:[\\/][^\']*)\'|([a-z]:[\\/][^\s"\'|;&<>,()]*)', re.I)


def _inside_root(path: str) -> bool:
    try:
        p = Path(os.path.expandvars(path)).resolve()
    except (OSError, ValueError, RuntimeError):
        return True        # cannot tell where it points, so it may point here
    return p == ROOT or ROOT in p.parents


def check_command(command: str):
    """None if the command may run; the refusal, as a sentence, if not."""
    cmd = command or ""
    low = _norm(cmd)
    if _norm(str(ROOT)) in low or _norm(str(ROOT)).replace("\\", "\\\\") in low:
        return f"{LAW} — that command reaches into ARC's own folder."
    for m in _ABS_PATH.finditer(cmd):
        if _inside_root(next(g for g in m.groups() if g)):
            return f"{LAW} — that command reaches into ARC's own folder."
    # Full paths that resolve OUTSIDE this folder are about somebody else's
    # files, so they are taken out before the name checks below — otherwise a
    # README.md in Documents would be refused for sharing a name with ours.
    # Everything else (bare names, relative paths, $HOME\..., %VAR%\...) stays
    # in and is judged by name, which fails closed.
    low = _norm(_ABS_PATH.sub(" ", cmd))
    if _HIDDEN.search(cmd):
        return (f"{LAW} — that command hides what it runs (encoded, eval'd or "
                f"built at run time), so I cannot check it doesn't touch my code.")
    if _GIT.search(cmd):
        return f"{LAW} — git changes code, and git is yours to run, not mine."
    if _PIP.search(cmd):
        return f"{LAW} — installing or removing packages changes the code I run."
    words = set(re.findall(r"[a-z0-9_.\-]+", low))
    for folder in _folder_names():
        if folder in words:
            return f"{LAW} — that command names ARC's folder ({folder})."
    # "every *.py under C:\dev" names no file and no folder of ours, and reaches
    # all of them. A wildcard over a code extension is refused wherever it points.
    # Searched in the WHOLE command, paths included: "del /s C:\dev\*.py" is a
    # path that resolves outside this folder and still reaches into it.
    glob = re.search(r"\*+\.(%s)\b" % "|".join(e.lstrip(".") for e in _CODE_EXT), _norm(cmd))
    if glob:
        return (f"{LAW} — a wildcard over *.{glob.group(1)} files could reach my "
                f"code wherever it points. Name the files instead.")
    hit = sorted(n for n in _names() if n in words)
    if hit:
        return (f"{LAW} — that command names {hit[0]}, which is part of my code. "
                f"If you meant a different file of the same name, give its full "
                f"path, starting with the drive letter.")
    return None


# --- windows that typing or clicking would reach -----------------------------

def check_window(title: str, exe: str = ""):
    """None if input may go to this window; the refusal if it would reach a
    shell or an editor showing ARC's code."""
    e = (exe or "").strip().lower()
    t = (title or "").strip()
    low = t.lower()
    if e in _TERMINALS:
        return (f"{LAW} — the window in front is a terminal ({e}), and typing "
                f"or clicking into one is running commands unchecked.")
    if _norm(str(ROOT)) in _norm(t):
        return f"{LAW} — the window in front is showing ARC's own folder."
    # Editors title their windows "file - folder - Editor". A segment that IS
    # this folder's name, or a locked file's name, means this code is open.
    segments = {s.strip().lower() for s in re.split(r"\s+[-—–|]\s+|[\\/]", t) if s.strip()}
    segments |= {s.lstrip("●*• ").strip() for s in segments}
    if any(n in segments for n in _folder_names()) or "arc-bella" in segments:
        return f"{LAW} — the window in front has ARC's code open."
    hit = sorted(n for n in _names() if n in segments)
    if hit:
        return f"{LAW} — the window in front has {hit[0]} open, which is part of my code."
    return None


# --- after the fact ----------------------------------------------------------

_tripped = []          # the files that changed; non-empty means the shell is locked


def snapshot() -> dict:
    """(size, mtime) per locked file. Cheap enough to take around every
    command: a stat per file, no reading."""
    snap = {}
    for p in code_files():
        try:
            st = p.stat()
            snap[str(p)] = (st.st_size, st.st_mtime_ns)
        except OSError:
            pass
    return snap


def changed(before: dict, after: dict) -> list:
    """Files added, removed or altered between two snapshots, relative names."""
    keys = set(before) | set(after)
    diff = sorted(k for k in keys if before.get(k) != after.get(k))
    return [str(Path(k).relative_to(ROOT)) for k in diff]


def trip(files: list) -> None:
    _tripped.extend(files)


def tripped() -> list:
    return list(_tripped)
