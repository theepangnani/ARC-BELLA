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
                    that hides what it runs (-EncodedCommand, iex, base64, an
                    interpreter handed its program as text), is refused at
                    prepare AND again at run. Paths are resolved, and paths with
                    wildcards are matched against this folder. Fails closed: a
                    false refusal costs a rephrase, a false pass costs the law.
  · typing        — keyboard, key_macro, hold_key refuse when the window in
                    front runs what is typed (a terminal, Explorer's address
                    bar, an editor with a terminal inside), or is an editor
                    showing this code. Typed text with a line break is read as
                    a command, and focus is asked again after every Enter.
  · clicking      — mouse clicks and auto_click refuse on the same windows: a
                    click is how "Save", "Discard" and "Commit" get pressed.
  · after the fact — every command that does run is bracketed by a snapshot of
                    the code files, and each command's "before" is compared
                    with the last one's "after", for what a detached process
                    wrote late. If one changed anyway, the shell locks for
                    the rest of this process and the owner is told. It cannot
                    un-change it: reverting would also destroy edits the owner
                    was making in an editor at the same moment.

The second pass came from a bug check on the desktop that called check_command
and check_window directly: wildcards in paths, -ec, py -c, git by full path,
VS Code's terminal, a late write. Each is a case in tests/test_codelock.py that
failed against the version before it.

WHAT IT IS HONEST ABOUT. A string check on a shell command is not a sandbox.
Somebody determined enough, at the desktop, saying yes to a command they have
read aloud, can write a command this does not recognise — which is why the
after-the-fact check exists, and why it locks rather than warns. The only
airtight version is ARC running as a Windows account with no write access to
this folder; that is an install choice, not something code can make for itself.
"""

import fnmatch
import glob as _glob
import os
import re
import time
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
    # Missed by the first list, found by the desktop's bug check: the ISE is a
    # PowerShell with an editor attached, py/pyw are the launcher Windows
    # actually associates with .py, node is a REPL, git-bash and sh are bash.
    "powershell_ise.exe", "py.exe", "pyw.exe", "node.exe", "git-bash.exe",
    "sh.exe", "kitty.exe", "hyper.exe", "tabby.exe", "cmder.exe",
}

# Explorer runs what is TYPED into its address bar (F4, Ctrl+L) and owns the
# Win+R Run box, which is a command line with a text field for a face. Neither
# shows up in a title. So typing into Explorer is refused whole.
#
# CLICKING is not. Explorer is also the taskbar, the Start button and the
# desktop ("Program Manager"), and the first version put it in _TERMINALS,
# which refuses clicks too — Claude 1 found that every click on the taskbar
# was refused. A click cannot type a command; a window showing this folder is
# still refused by its title, like any other.
_TYPE_ONLY = {"explorer.exe"}

# Editors with a terminal built in. The window title says "run.py - arc -
# Visual Studio Code" whether the cursor is in the file or in the terminal
# panel underneath it, so nothing outside the editor can tell a keystroke into
# a document from a command into PowerShell. Refused whole, like a terminal.
_IDES = {
    "code.exe", "code - insiders.exe", "codium.exe", "vscodium.exe", "cursor.exe",
    "windsurf.exe", "zed.exe", "idea64.exe", "pycharm64.exe", "webstorm64.exe",
    "rider64.exe", "clion64.exe", "goland64.exe", "devenv.exe", "sublime_text.exe",
    "atom.exe", "fleet.exe", "spyder.exe", "thonny.exe",
}

# Names too common to mean THIS code on their own. "type readme.md" from the
# home folder is somebody's readme, and "README.md - Notepad" is any project's.
# These count only beside a path separator ("static\index.html", "\readme.md")
# in a command, and not at all in a window title — where a title that is ours
# also names the folder, which is refused on its own. index.html is NOT here
# for titles (see _GENERIC_TITLE): it is the whole HUD, and Notepad's title
# cannot say which index.html it has open, so the lock fails closed on it.
_GENERIC = {
    "readme.md", "license", "index.html", "requirements.txt", "requirements-dev.txt",
    ".gitignore", ".gitattributes", "claude.md", "changelog.md", "contributing.md",
    "security.md", "manifest.webmanifest", "ci.yml", "start.sh", "sw.js", "home.html",
    "privacy.html", "terms.html", "deploy.md", "prd.md", ".env.example",
}
_GENERIC_TITLE = _GENERIC - {"index.html", "home.html", "sw.js"}

# A folder name this short is an ordinary word ("arc"), so on its own it counts
# only beside a separator or after a command that changes directory into it.
_SHORT_FOLDER = 4

_WILD = re.compile(r"[*?\[]")


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
#
# PowerShell takes ANY unambiguous prefix of -EncodedCommand — -e, -ec, -en,
# -enc, -encod... — and / as well as -. The first version listed three
# spellings and "-ec" walked past it; so it matches the family, not a list.
_HIDDEN = re.compile(
    r"(?i)((^|\s)[-/](e|ec|en[a-z]*)[:\s]+[\"']?[A-Za-z0-9+/=]{16,}"   # powershell -enc <b64>
    r"|\biex\b|invoke-expression|frombase64string|\[convert\]::"
    r"|certutil\b.*-decode|\bscriptblock\b|add-type\b"
    r"|\bexec\s*\(|\beval\s*\(|\bmshta\b)")
# An interpreter handed its program as an argument or through a pipe. The code
# is then a string the interpreter builds and runs — chr(114)+chr(117)+... is
# run.py without ever spelling it — so reading the command says nothing about
# what it does. Refused whatever the code is: a script in a file is still fine,
# because the file's path goes through every check below.
#
# "py -3 -c", "pythonw -c 1", "python3.12 -X utf8 -c" all got past a pattern
# that wanted "python -c" side by side, so this finds the interpreter as a word
# and an inline-code switch anywhere after it in the same command segment.
_INTERP = r"(py|pyw|python[w]?[0-9.]*|node|deno|bun|ruby|perl|php|lua|tclsh|wish|osascript|jshell)"
_INLINE = re.compile(
    # Only OPTIONS may sit between the interpreter and the switch ("-3",
    # "-X utf8"): "python backup.py -c config.ini" is a script with an option
    # of its own, whose path the checks below read like any other.
    r"(?i)(^|[\s;&|(\"'`\\/])" + _INTERP + r"(\.exe)?[\"']?"
    r"(\s+-(?!-?(c|e|p|r|eval|print|command)(\s|$))\S+(\s+(?!-)[^\s;&|\"']+)?)*"
    r"\s+(-c|-e|-p|-r|--eval|--print|--command|-command|-)(\s|$|[\"'])")
_PIPED = re.compile(
    r"(?i)\|\s*&?\s*[\"']?" + _INTERP[:-1] + r"|powershell|pwsh|cmd|bash|sh|wsl)(\.exe)?[\"']?(\s|$)")
# git rewrites the working tree (checkout, pull, reset, apply, stash) — and a
# pull on the desktop changes what is running. The owner runs git, ARC does not.
# A path separator counts before it: "C:\Program Files\Git\cmd\git.exe checkout ."
# is git, and the first version only looked for a space or a quote.
_GIT = re.compile(r"(?i)(^|[\s;&|(\"'`\\/])git(\.exe)?([\s\"']|$)")
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
    raw = os.path.expandvars(path)
    if _WILD.search(raw):
        return _wild_reaches_root(raw)
    try:
        p = Path(raw).resolve()
    except (OSError, ValueError, RuntimeError):
        return True        # cannot tell where it points, so it may point here
    return p == ROOT or ROOT in p.parents


def _wild_reaches_root(pattern: str) -> bool:
    """Could a path with * ? or [ in it land in this folder?

    The first version resolved the pattern as if the * were a letter, found
    "C:\\dev\\arc-voice-assist*" was no folder of ours, and dropped the path
    before any other check saw it — so "Set-Content C:\\dev\\arc-voice-assist*
    \\arc\\run.py x" was allowed. Two answers now, either of which refuses:

      · by SHAPE — each part of the pattern, matched against the same part of
        this folder's path. "C:\\dev\\ar?" matches C:\\dev\\arc; "C:\\dev\\*"
        matches its parent, which a recursive command walks straight into.
        "C:\\Users\\me\\*.pdf" stops matching at the folder's own name, so it
        is somebody's PDFs and is left alone.
      · by what EXISTS — globbed, and every match resolved, so an 8.3 name or
        a junction with a wildcard in it lands where it really goes.
    """
    norm = os.path.normpath(pattern)
    parts = [x.lower() for x in re.split(r"[\\/]+", norm) if x]
    mine = [x.lower().rstrip("\\") for x in ROOT.parts]
    if parts and mine:
        n = min(len(parts), len(mine))
        if all(fnmatch.fnmatchcase(mine[i], parts[i]) for i in range(n)):
            return True
    try:
        for i, hit in enumerate(_glob.iglob(norm)):
            if i >= 200:
                return True     # too many to check: may be here
            try:
                rp = Path(hit).resolve()
            except (OSError, ValueError, RuntimeError):
                return True
            if rp == ROOT or ROOT in rp.parents:
                return True
    except (OSError, ValueError, re.error):
        return True
    return False


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
    if _INLINE.search(cmd) or _PIPED.search(cmd):
        return (f"{LAW} — that hands an interpreter its program as text, which "
                f"can build any path without spelling it, so I cannot check it "
                f"doesn't touch my code. Put the script in a file and run that.")
    if _GIT.search(cmd):
        return f"{LAW} — git changes code, and git is yours to run, not mine."
    if _PIP.search(cmd):
        return f"{LAW} — installing or removing packages changes the code I run."
    # Tokens, with what stood either side of each: a name beside a separator
    # is a path ("static\index.html", "\dev\arc"), a name on its own may be a
    # word ("echo arc is great").
    tokens = [(m.group(0), low[max(m.start() - 1, 0):m.start()] == "\\"
               or low[m.end():m.end() + 1] == "\\")
              for m in re.finditer(r"[^\s\\\"'`|;&<>,()=]+", low)]
    words = {t for t, _ in tokens}
    pathy = {t for t, sep in tokens if sep}
    cd = re.search(r"(?i)(^|[\s;&|(])(cd|chdir|pushd|sl|set-location|push-location)(\s|$)", low)
    for folder in _folder_names():
        # A short folder name ("arc") is an ordinary word. It counts beside a
        # separator, or in a command that changes directory — "cd arc" from a
        # folder that holds it is the way in.
        if folder in pathy or (folder in words and (len(folder) > _SHORT_FOLDER or cd)):
            return f"{LAW} — that command names ARC's folder ({folder})."
        wild = [t for t in words if _WILD.search(t) and len(_WILD.sub("", t)) >= 2]
        if any(fnmatch.fnmatchcase(folder, t) for t in wild):
            return f"{LAW} — a wildcard in that command matches ARC's folder ({folder})."
    # "every *.py under C:\dev" names no file and no folder of ours, and reaches
    # all of them. A wildcard over a code extension is refused wherever it points.
    # Searched in the WHOLE command, paths included: "del /s C:\dev\*.py" is a
    # path that resolves outside this folder and still reaches into it.
    glob = re.search(r"\*+\.(%s)\b" % "|".join(e.lstrip(".") for e in _CODE_EXT), _norm(cmd))
    if glob:
        return (f"{LAW} — a wildcard over *.{glob.group(1)} files could reach my "
                f"code wherever it points. Name the files instead.")
    names = _names()
    hit = sorted(n for n in names
                 if (n in pathy) or (n in words and n not in _GENERIC))
    if not hit:
        # "codeguard.p?" and "run.[p]y" are run.py and codeguard.py without the
        # spelling. A pattern needs two real characters, so a lone "*" (as in
        # Select-Object *) is not read as naming everything.
        wild = [t for t in words if _WILD.search(t) and len(_WILD.sub("", t)) >= 2]
        hit = sorted(n for n in names if n not in _GENERIC
                     and any(fnmatch.fnmatchcase(n, t) for t in wild))
    if hit:
        return (f"{LAW} — that command names {hit[0]}, which is part of my code. "
                f"If you meant a different file of the same name, give its full "
                f"path, starting with the drive letter.")
    return None


# --- windows that typing or clicking would reach -----------------------------

def check_window(title: str, exe: str = "", typing: bool = True):
    """None if input may go to this window; the refusal if it would reach a
    shell or an editor showing ARC's code. `typing` is False for a click."""
    e = (exe or "").strip().lower()
    t = (title or "").strip()
    low = t.lower()
    if e in _TERMINALS or (typing and e in _TYPE_ONLY):
        return (f"{LAW} — the window in front runs what is typed into it ({e}), "
                f"and typing or clicking there is running commands unchecked.")
    if e in _IDES:
        return (f"{LAW} — the window in front is a code editor with a terminal "
                f"inside it ({e}), and from outside I cannot tell which one the "
                f"keystrokes would reach.")
    if _norm(str(ROOT)) in _norm(t):
        return f"{LAW} — the window in front is showing ARC's own folder."
    # Editors that show the whole path (Notepad++, Sublime) say exactly which
    # file is open: judged by where it resolves, and then taken out, so
    # "C:\site\index.html - Notepad++" is not refused for sharing a name.
    for m in _ABS_PATH.finditer(t):
        if _inside_root(next(g for g in m.groups() if g)):
            return f"{LAW} — the window in front is showing ARC's own folder."
    t = _ABS_PATH.sub(" ", t)
    # Editors title their windows "file - folder - Editor". A segment that IS
    # this folder's name, or a locked file's name, means this code is open.
    segments = {s.strip().lower() for s in re.split(r"\s+[-—–|]\s+|[\\/]", t) if s.strip()}
    segments |= {s.lstrip("●*• ").strip() for s in segments}
    if any(n in segments for n in _folder_names()) or "arc-bella" in segments:
        return f"{LAW} — the window in front has ARC's code open."
    # "README.md - Notepad" is any project's readme; a title that is really
    # ours also names the folder, and was refused just above.
    hit = sorted(n for n in _names() if n in segments and n not in _GENERIC_TITLE)
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


# The code as the last command left it, and when. A command can start something
# that outlives it — "start /b" a script that waits a few seconds, then writes —
# and the first version compared only the moments either side of each command,
# so the write landed between two commands and was already in the next one's
# "before". Now each command's "before" is also compared with the last one's
# "after".
#
# Bounded in time, and that is a trade made on purpose: the owner edits this
# code, and on the desktop git pulls it, while ARC is running. Every such edit
# would otherwise lock the shell at the next command, however much later it
# came. A detached writer that waits longer than this window gets past this
# check — which is the string check's honest limit again, not a new one.
DETACHED_WINDOW = 10 * 60
_last = {"snap": None, "at": 0.0}


def settle(after: dict) -> None:
    """Record the code as a command left it."""
    _last.update(snap=after, at=time.monotonic())


def since_last(before: dict) -> list:
    """What changed between the last command's end and this one's start, if
    the last command ended recently enough to be the likely cause."""
    snap = _last["snap"]
    if snap is None or time.monotonic() - _last["at"] > DETACHED_WINDOW:
        return []
    return changed(snap, before)


def trip(files: list) -> None:
    _tripped.extend(files)


def tripped() -> list:
    return list(_tripped)
