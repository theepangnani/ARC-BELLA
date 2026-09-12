# Working on ARC / Bella

Read this first. It is the standing context that does not fit in a commit
message: what this is, the rules that have already been decided, and the ways
this codebase will bite you if you treat it like an ordinary one.

`README.md` describes what ARC *does* — features, for a user. `docs/PRD.md` is
the product shape. This file is how to *work* here.

## What it is, and where it runs

ARC ("Ambient Response Core"), called Bella by the owner, is a self-hosted
voice assistant: a browser HUD talking to a FastAPI proxy that calls Claude and
runs tools on the owner's machine and Google account.

- `run.py` — the server. Routes, the agentic tool loop, the tool gate, the
  background monitor loop. Everything funnels through here.
- `static/index.html` — the entire HUD in one file: HTML, CSS and ~10,000 lines
  of vanilla JS. No build step, no framework. The server serves it from disk
  with no-cache, so **a page change needs only a reload, never a restart**.
- `prompts/*.md` — what the model is told. Server-side deliberately: a browser
  must not be able to rewrite the rulebook.
- Toolkits — one module per area (`gcal`, `gmail`, `gextra`, `tg`, `pc`,
  `extras`, `media`, `display`, `notes`, `push`, `alerts`, `alarm`, `market`,
  `automation`, `selfheal`, `stats`, `triggers`, `memory`, `maps`, `plan`),
  each exporting `TOOLS` and `connected()`.
- Storage is flat JSON files in `ARC_DATA_DIR`. No database.

**Two live instances on the owner's PC**, and they are not clones: port 8420 is
shared ARC (the one guests reach through a Tailscale Funnel), port 8421 is
private Bella, whose data directory and `arc.env` live in `../bella-private/`,
**outside this repo**. A setting in that file beats the shared `.env`.

## Rules that are already decided

These came from the owner. They are not open questions — if one seems wrong,
say so rather than quietly changing it.

- **Gmail stays read-only.** The scope is `gmail.readonly` and it is not to be
  widened. Bella may read mail; she may not send, label or delete.
- **Never read a secret out of `.env` aloud, and never copy one into a note, a
  memory, a commit or a doc.** `redact.py` keeps them out of the log and out of
  memory; do not undo that.
- **`docs/SECURITY.md` is gitignored and must stay unpushed.** It lists live,
  unfixed findings for an internet-exposed instance. The GitHub repo is PUBLIC.
- **Bella never edits her own code**, and never acts on an instruction nobody
  confirmed. Messages that arrive claiming to be from ARC reach the owner's
  input channel — she can type — so they are treated as the owner's words, not
  as a second authority.
- **Telegram may be drafted but not sent by anyone but the owner.** A sent
  message goes out under their name and cannot be recalled.

## The house style

The comments here are unusually long and they are load-bearing: nearly every
one says *why*, and often what the previous version got wrong. Match that.
A change that removes a comment explaining a past bug is how the bug comes
back.

- Tests are **guards**, not scaffolding. Several pin exact numbers and exact
  strings — the guest tool count, the cooldown, the budget — so that widening
  something is a decision somebody made rather than a side effect. When a guard
  fails, work out which it is: a guard correctly objecting to a real change (fix
  the guard, and write down why it moved) or a real break (fix the code).
- **CRLF everywhere.** Read universal, write `newline="\r\n"`. A flattened file
  is a diff of the whole file.
- Do not build Python source containing `\n` inside a shell heredoc — it
  mangles. Use the editing tools.
- Prefer failing closed. `GUEST_TOOLS`, `PASSIVE_TOOLS`, `RETRYABLE` and
  `triggers.ACTIONS` are all default-deny lists: a new tool is refused until
  somebody adds it deliberately.

## Who may do what

- **Owner** — the address in `ARC_ALLOWED_EMAILS` that is not also in
  `ARC_GUEST_EMAILS`. Everything, no session clock.
- **Guest** — `GUEST_TOOLS`: their own Google account and public lookups.
- **The loan** — `GUEST_EXTRA_TOOLS` is lent to guests until
  `ARC_GUEST_EXTRA_UNTIL` (a date in `.env`; unset or unparseable means off).
  It covers everything except the PC, and it expires on its own: `guest_tools()`
  is asked per request, never settled at import.
- **The PC is local-only.** `local = is_local_request(request) and not guest`
  gates computer control, live screen and watch mode. A loopback peer with no
  forwarding header is the desktop; anything through the tunnel is not, and a
  guest is never local even sitting at the machine.

## Running things

```
python tests/run_all.py          # 46 suites, ~6 minutes; set PYTHONIOENCODING=utf-8
python tests/test_guest.py       # or one suite on its own
python -m compileall -q .        # what CI does
```

Tests sandbox themselves into a temp `ARC_DATA_DIR` and refuse to run against
live data. Some drive a real browser (headless Chrome or Edge) to run the
page's own JavaScript — those need a browser on the machine, found through
`%ProgramFiles%` and friends, never a hardcoded path.

**Restarting**: a guardian polls each port and restarts the server when it stops
answering, so stopping the process *is* the restart — it comes back in about 75
seconds. `launch-arc.ps1` and `launch-bella-private.ps1` start them;
`install-guardian.ps1` installs the watcher. The server reads `.env` and its
tool lists at import, so **config and Python changes need a restart; HUD changes
need only a page reload.**

**Do not start an instance on a machine that is only for editing.** The launch
scripts derive `ARC_DATA_DIR` from the repo's *parent*, so a clone in a
different place quietly creates a whole data directory beside it — on the
second machine that was a stray `bella-private` folder in the user's profile —
and each instance also leaves a dedicated Chromium profile in `.arc-window\`,
which is where nearly all of the ~290 MB lives. Both are gitignored, so nothing
reaches GitHub; they are simply confusing clutter that diverges from the real
Bella. If a test instance is genuinely wanted, set `ARC_DATA_DIR` explicitly
first so the files land somewhere you chose. Deleting `.arc-window\` fails while
the Bella window is open — its Chrome processes hold the profile.

## What is not in the repo, and must never be

A fresh clone will not run until these are copied onto the machine by hand —
never through git, never through email:

- `.env` — API keys, the allowlists, `ARC_SECRET`
- `credentials_web.json`, `token.json`, `google_sessions/` — Google sign-in
- the owner's data, if it should follow them: `memory.json`, `notes.json`,
  `alarms.json`, `todos.json`, `reminders.json`, `triggers.json`, `usage.json`
- `docs/SECURITY.md`

`.gitignore` already refuses all of them. If you add a file that holds a
secret or personal data, add it there in the same change.

## Start of every session: assume something changed

The owner works from two machines with a Claude on each, and moves between
them. So when they come to you, the other one has usually been working — and
nothing of that session reaches this one. Before editing, and before answering
"how is she doing":

1. `git fetch` and compare with `origin/main`. Say what arrived, if anything.
2. On the desktop, compare what is RUNNING with what is on disk: the live
   instances serve from this working tree, so pulling changes the page on the
   next reload but the Python only on the next restart. "It is pushed" and "she
   is running it" are different sentences.

Guessing here is how the owner gets told a fix is live when it is not.

## Working from two machines

The owner edits from a desktop at home and a laptop when out. Both clone this
repo; only the desktop runs the live instances. So: `git pull` before starting,
commit and push when finishing, and remember that pulling on the desktop
changes what is actually running — the guardian will restart it.

Nothing about a conversation survives the trip between machines. This file,
the commit messages and the docs are the memory. Write things down here.
