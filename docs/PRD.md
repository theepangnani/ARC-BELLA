# ARC — Product Requirements Document

**Product:** ARC (Ambient Response Core), in-product persona **Bella**
**Repository:** `theepangnani/ARC-BELLA`
**Status:** Live, single-owner, self-hosted
**Last updated:** 2026-08-23

> This document describes what ARC is and what it is meant to be. The
> [README](../README.md) is the setup guide; this is the scope, the reasoning,
> and the boundaries. Security posture is assessed separately in
> [SECURITY.md](SECURITY.md).

---

## 1. Problem and vision

Commercial voice assistants require you to send your microphone, your calendar,
and your mail to someone else's servers, and to accept whatever capabilities the
vendor decides to ship. ARC inverts that: **a voice assistant you run yourself**,
where the model is rented but the data, the keys, and the capability set are
yours.

> "A voice assistant you run yourself. Browser front end, FastAPI proxy, Claude
> for reasoning, optional ElevenLabs for a voice that doesn't sound like a
> browser." — `README.md:1-5`

The product promise, as stated on the login page and public homepage:

> "Your own voice-first AI. It listens, sees your screen, runs your calendar and
> mail, and keeps an eye on the markets — all on your terms." — `run.py:583-584`

Three commitments follow from that and constrain every design decision:

1. **Keys never reach the browser.** The FastAPI layer exists primarily as a
   proxy so the Anthropic key stays server-side.
2. **Data stays on the machine.** Flat JSON files and browser `localStorage`.
   No database, no cloud sync, no telemetry.
3. **Reaching off the page is a deliberate escalation.** Timers are pure
   browser. Calendar touches one Google account. Shell commands touch the whole
   machine. Each step up carries a stronger gate.

## 2. Users

**Primary and only user: the owner.** ARC is single-tenant by design. There is
no sign-up, no user table, no roles.

Two instances exist by design (`static/index.html:1228`):

| Instance | Port | Data directory | Purpose |
|---|---|---|---|
| **ARC** (shared) | 8420 | repo root | Everyday assistant, the one that gets tunnelled to the phone |
| **Bella** (private) | 8421 | `../bella-private/` | Separate reminders, notes, watchlists, Google account, and app icon |

Isolation is achieved entirely through `ARC_DATA_DIR` (`run.py:48`); both
instances run the same code from the same checkout.

**Access surfaces:** the desktop Chromium app window, a phone PWA over an HTTPS
tunnel, and a second screen at `/display`.

## 3. Capabilities

### 3.1 Voice pipeline

Speech-to-text is the **browser's Web Speech API** — which makes Chrome or Edge
a hard requirement and is the single largest architectural constraint in the
product. Other browsers fall back to the text box.

| Capability | Behaviour |
|---|---|
| Wake word | User-editable, default `bella`; "Always listening" toggle |
| Follow-up window | ~16 s of wake-word-free follow-ups after a reply |
| Push-to-talk | Hold the ring, or Space when focused. A press that *moves* is a 3D drag and cancels the recording |
| Barge-in | Tap the ring while it is speaking |
| Transcript cleanup | Strips stutters, doubled words, leading filler — deliberately conservative, so *"i had had enough"* survives |
| N-best alternatives | Keeps 4 recogniser candidates and passes the runners-up to Claude as a private hint. The single biggest accuracy win for accents |
| Noise gate | Claps, coughs and lone fragments dropped; saying it twice forces it through |
| Recogniser recovery | Chrome's speech recogniser dies silently; the client rebuilds it and reports `MIC RECONNECTING…` rather than appearing deaf |
| TTS | Server-side. Microsoft `edge-tts` neural voices by default (free, 8 whitelisted), ElevenLabs `eleven_turbo_v2_5` first if configured, browser synthesis as final fallback |
| Auto-ducking | Lowers system volume while listening and speaking (desktop only) |

**Mic state is reported honestly.** The hint under the ring reflects the
recogniser's real state — `SAY "BELLA"`, `LISTENING…`, `MIC RECONNECTING…`,
`SAY "BELLA" FIRST`, `TAP TO RETRY MIC` — never an intention. A fault must never
be indistinguishable from silence.

**Room mode** exists because browsers give no way to tell who is speaking.
Rather than guess, it makes misfiring impossible: the wake word is required for
every turn, must be sentence-initial, speaker continuity is not assumed between
turns, and nothing is written to memory because it cannot tell whose facts they
are.

**Staying quiet** — the model may emit `[[silent]]` and say nothing at all, for
half-sentences, one side of someone else's phone call, thinking aloud, or a
television. Explicitly *not* merely because a message is short or badly
transcribed: a garbled question is still a question. When unsure it answers, on
the grounds that a needless reply is a smaller failure than ignoring someone.
The transcript still records that it heard.

### 3.2 Reasoning

An agentic tool loop, `ARC_MAX_TOOL_ROUNDS` = 6 (`run.py:189`, loop at
`run.py:894`). Handles `pause_turn` resumption for server-side tools and the
`refusal` stop reason explicitly.

- **Brain switch** — Smart (`claude-sonnet-5`) / Fast
  (`claude-haiku-4-5-20251001`), switchable at runtime per device.
- **Thinking** — Fast / Auto / Deep, with a client-side heuristic deciding
  whether a given utterance needs it.
- **Web search** — Anthropic's server-side `web_search` tool, max 3 uses per
  turn, toggleable.
- **Personas** — ARC (unflappable British butler), Bella (best friend), Coach.
- **Spend meter** — per-day token accounting surfaced live in the HUD as
  `SPEND TODAY`, with a hard `ARC_DAILY_COST_CAP`.

### 3.3 Tools (76 across 18 modules, plus one server-side)

| Module | Tools |
|---|---|
| `gcal` | `list_events`, `create_event`, `move_event`, `cancel_event` |
| `gmail` | `search_email`, `read_email` *(read-only)* |
| `gextra` | `find_contact`, `find_drive`, `read_drive` *(read-only)* |
| `tg` | `tg_list_chats`, `tg_read_chat`, `tg_draft_message`, `tg_send_pending` |
| `pc` | `open_app`, `list_apps`, `list_windows`, `focus_window`, `close_window`, `message_app`, `open_website`, `find_files`, `read_file`, `open_file`, `system_control`, `brightness`, `wifi`, `power_mode`, `screenshot`, `list_monitors`, `media`, `clipboard`, `keyboard`, `mouse_control`, `prepare_command`, `run_prepared` |
| `extras` | `weather`, `add_todo`, `list_todos`, `complete_todo`, `set_reminder`, `list_reminders`, `cancel_reminder`, `stock`, `news` |
| `media` | `youtube`, `spotify` |
| `display` | `show_on_display`, `clear_display` |
| `notes` | `add_note`, `list_notes`, `delete_note` |
| `push` | `notify_phone` |
| `alerts` | `set_price_alert`, `list_price_alerts`, `clear_price_alert` |
| `alarm` | `set_alarm`, `list_alarms`, `cancel_alarm`, `snooze_alarm`, `dismiss_alarm` |
| `market` | `market_outlook`, `market_compare` |
| `automation` | `auto_click`, `hold_key`, `key_macro`, `stop_automation`, `automation_status` |
| `selfheal` | `self_check`, `self_repair`, `export_everything` |
| `stats` | `usage_report` |
| `triggers` | `add_trigger`, `list_triggers`, `clear_trigger` |
| `memory` | `list_memory`, `forget` |

Plus `voices.py`, which is not a toolkit — it is the catalogue of every neural voice Microsoft publishes, fetched and cached, so §3.6b can promise a language without anyone hand-writing a voice table.

Plus `web_search`, which runs on Anthropic's side rather than ours.

**Capability presence is derived, not configured.** `all_tools()`
(`run.py:87-93`) exposes only toolkits whose `connected()` returns true, and
drops `pc` entirely for non-local callers. A missing integration means ARC is
told it has no such tool and says so, rather than hallucinating success.

Separately, the client parses **inline directives** out of the reply and strips
them before speech: `[[remember: …]]`, `[[timer: …]]`, `[[alarm: …]]`,
`[[canceltimer: …]]`, `[[market: …]]`, `[[silent]]`.

### 3.4 Integrations

- **Google** — Calendar (read/write), Gmail (**read-only**), Contacts and Drive
  (read-only). One sign-in, one consent screen, via `gauth.py`. Calendar is the
  only Google scope with write access, deliberately: a calendar edit is visible
  and reversible, and it stays behind the consent gate. Natural-language dates
  are resolved against the device clock and timezone, so the user never speaks
  an ISO timestamp.
- **Telegram** — via Telethon, as the *user's own account*, not a bot. Real
  conversations with real people.
- **Weather** — Open-Meteo, no API key.
- **Markets** — Yahoo Finance quotes and symbol search, proxied server-side for
  CORS. Draggable, voice-editable watchlist panel.
- **News** — Google News RSS.
- **Phone push** — ntfy.

### 3.5 Vision

- **Live screen** — a screenshot attached to every message when enabled.
  Defaults to **every monitor as its own full-detail image**, not the primary
  alone: attaching only screen one meant ARC answered confidently about the
  wrong screen whenever the user was working on the other, which is worse than
  not seeing at all. The control cycles off → all screens → primary, the last
  being the way to take back the per-turn image cost when it isn't wanted.
- **Multi-monitor** — `list_monitors` enumerates via `EnumDisplayMonitors`,
  primary first. `screenshot` takes `'each'` (one image per screen, full
  detail) or `'all'` (stitched). `'each'` is preferred for anything that must
  be *read*: stitched, two screens share a single 1400 px-wide frame, so each
  loses half its detail and picks up the dead space between mismatched
  monitors. A secondary screen commonly sits at **negative** virtual
  coordinates, so every caption carries its monitor's origin, which must be
  added to any position before it is used with `mouse_control`.
- **Watch mode** — a cheap local pixel-diff gate polled every 4 s, escalating to
  a model glance at most once per 20 s, with `no_tools` and `allow_actions:
  false` so it can observe but never act. Each monitor is thumbnailed and
  scored **separately** and the loudest wins: one stitched thumbnail would
  quietly desensitise the gate, since the same change covers proportionally
  fewer pixels of a wider virtual desktop and the fixed threshold then starts
  missing things purely because a second screen was plugged in. The gate and
  the glance always cover the **same** screens (both follow the live-screen
  monitor choice): a gate watching two screens while the glance sees one is
  the worst of both — it wakes the model for a change it cannot find, pays for
  the look, and reports nothing. Changing the choice mid-run re-baselines
  rather than firing. The watch prompt is generated per glance, since the
  static one said "the current screen" — singular — which a model handed two
  images has no way to detect as wrong.
- **Camera** — one-shot frame, transient, never stored.

### 3.6 Proactive behaviour

A 30-second server loop evaluates price alerts, starts any alarm whose moment
has come, and pushes due reminders, crossed alerts and ringing alarms to the
phone. Client-side: a daily briefing at a set time, meeting nudges polled from
the calendar, user-defined routines (`phrase = instruction`), recurring
schedules, and timers.

**Timers are deliberately implemented entirely in the browser** — they keep
working with nothing granted to the server, and survive a page reload.

**Alarms are deliberately the opposite**, because the requirement is different:
a timer has to survive a reload, an alarm has to survive the night. State lives
on the server (`alarms.json`), so an alarm repeats on chosen days, survives a
restart, and is pushed to the phone at **urgent** priority — the only delivery
path with no browser attached to it. Three rules follow from "it must actually
wake someone":

- **The browser poll does not consume it.** Reminders and price alerts are
  handed over once and marked delivered; a ringing alarm keeps being reported
  until it is stopped, so a reload cannot silence one.
- **It never rings late.** An occurrence missed while ARC was off is written
  off after ten minutes and rescheduled, rather than firing on startup.
- **It stops on its own** after fifteen minutes unattended, so a phone is not
  paged indefinitely into an empty house.
- **The phone is re-pushed every two minutes while it rings**, not once. One
  notification is read later; a phone that keeps buzzing is what wakes someone.
  This also repairs a failed push for free — `push.send()` swallows network
  errors and returns `False`, so under one-shot delivery a dropped push meant
  the alarm reached the phone never.
- **The ring does not depend on the microphone.** `initAudio()` returns early on
  mobile and never runs at all if the mic was refused, so `state.audioCtx` is
  null in exactly the two cases that matter most — the phone on the bedside
  table, and the keyboard-only user. The alarm creates its own `AudioContext`,
  unlocked on the first interaction with the page.
- **The ring does not depend on boot.** Every other poll starts after the
  Initialize gate; a page sitting at that gate is precisely the page that has
  been open all night, so the alarm poll starts at page load instead.
- **The ring does not depend on the loop staying alive.** Every one of the
  rules above assumes the 30-second loop is still running, and until §3.10
  nothing checked. A task that raises finishes and is easy to spot; a task
  wedged inside a call that never returns is indistinguishable from a healthy
  one from the outside. So the loop now reports a heartbeat each cycle and a
  separate watchdog restarts it when that stops — see §3.10.

**Speaker mode** turns a phone from a hand-held HUD into a put-down appliance.
Its substance is the **Screen Wake Lock**, re-acquired on `visibilitychange`
because the system drops it whenever the page hides: without it a backgrounded
mobile tab has throttled timers, stopped speech recognition and suspended
audio, which silently disables both the wake word and the alarm. It also arms
the wake word, and works around Android's call-audio routing — an open
microphone puts the device in communication mode, sending output to the
earpiece, so speaker mode `abort()`s capture before speaking rather than
`stop()`ping it, releasing the audio session immediately. A web page cannot
select an output device on mobile; that is the only lever available.

### 3.7 Interface

A single hand-written 262 KB `static/index.html`. **No build step, no npm, no
framework, no bundler** — a deliberate choice that keeps the whole client
readable and editable in place.

Three columns: system readouts (state, hearing, wake word, input level, latency,
model, voice engine, memory, timers, exchanges, spend today), configuration, and
the animated core with transcript and composer.

Appearance: 13 colour themes plus a custom colour picker that derives the whole
HUD palette from one colour, **seven core shapes** (HUD, rings, hex, wave,
sphere, helix, cube), a flat/3D toggle, spin-speed and reactivity sliders. All
persist locally.

The 3D sphere scatters 150 points by Fibonacci spiral (latitude bands would
bunch at the poles), each growing along its own normal. Depth is driven off *z*
rather than perspective — at this scale perspective barely separates front from
back, so the far hemisphere goes thin and dim instead. That is what makes it
read as a solid ball rather than a scatter of dots.

Other surfaces: `/display` (a self-contained second screen with clock, markets
and a spotlight card), an installable PWA with an offline fallback, and public
`/home`, `/privacy`, `/terms` pages that exist so Google's OAuth verification
crawler can reach them.

### 3.8 Memory

- **Long-term memory** — up to 120 durable facts, 200 characters each, in
  browser `localStorage` only, never on a server. Explicitly forbidden from
  storing passwords, keys, health details, or one-off questions. **Forget**
  wipes it.
- **Thread digest** — a rolling ≤1600-character note of the current thread,
  rebuilt server-side every 6 turns.
- **To-do list** — distinct from memory by design: the list is tasks to tick
  off, memory is durable facts.

### 3.9 Teaching

ARC teaches coding and everyday digital skills. Two mechanisms make that
possible in a voice product; the curriculum is the least interesting part.

- **The board** — a `[[board: …]] … [[/board]]` directive renders a monospace,
  copyable card in the transcript and is **never spoken**. It exists because
  the reply pipeline strips `*`, `_`, `#` and backticks as unspeakable, which
  is most of the character set code is written in. Directives are therefore
  extracted *before* that strip, not after. The fence has a closing marker
  rather than a single `[[…]]` because code contains `]]` (`a[b[c]]`) often
  enough to truncate on a closing-bracket match. General-purpose: any content
  where exactness beats sound (a command, a path, a spelling) belongs on it.
- **Persistent progress** — `[[progress: <track> | <step> | started|learning|
  stuck|got-it | note]]`, kept **per named learner** (`[[learner: …]]`) in
  `localStorage`, and injected into the system prompt so a lesson resumes days
  later rather than restarting. Suppressed in room mode, on the same reasoning
  as memory: ARC cannot tell who is speaking, so nothing gets filed against
  the wrong person.

Two twenty-step ladders (**coding**, **digital skills**) act as a spine, not a
syllabus. The `Teach` control cycles off → coding → digital; turning it off
preserves progress. The block contributes **zero tokens** on an ordinary turn
and roughly 1,400 while a track is active.

Pedagogy is specified in the prompt, not left to the model's instincts: show
code rather than speak it, one new idea per turn, the learner does the typing,
every turn ends in a task, check understanding by prediction rather than
"does that make sense?", hints before answers, and screenshot their actual
work before responding to it.

### 3.10 Self-repair

Everything in §3.6 is a promise made to somebody who then walked away. The
failure mode that matters is therefore not an error — it is silence: a loop
that stopped, a data file that will not parse, a voice catalogue that fell back
to English. None of those announce themselves, and each looks exactly like
nothing happening.

`selfheal.py` is the answer, in three parts.

**Notice.** `check()` returns findings at three levels (`ok` / `warn` /
`broken`) over: the background loop's heartbeat, every personal data file,
whether backups exist and how old they are, disk space, audit-log size, a
stuck auto-clicker, live sessions, the voice catalogue, the Anthropic key, the
Google links, and optional libraries. Each finding carries either the id of a
repair or nothing, and "nothing" is the interesting case.

**Repair, with a copy taken first.** Snapshots run every six hours and at boot:
six kept per file, and **only ever taken from a file that parses**, so damage
can never overwrite the copy needed to undo it. Restores walk back to the
newest snapshot that *reads*, rather than trusting the newest. A damaged file
is renamed into `backups/`, never deleted.

Two repairs happen without being asked, and only two. At **boot**, a damaged
file is set aside and restored — boot is the one moment nothing is mid-write,
which is what makes an unparseable file unambiguous there and merely suspicious
later. And a **stalled background loop is restarted** by a watchdog task that
deliberately lives outside the loop it supervises. Everything else waits to be
asked, twice: the panel button checks on the first press and repairs on the
second, and only what the first press printed on screen.

**Refuse, out loud.** The boundaries are the design, not an omission:

| It will not | Because |
|---|---|
| Edit its own source | Code that rewrites itself cannot be reviewed; "it repaired itself" and "it broke itself unreadably" look identical from outside |
| Touch `.env`, `credentials*.json`, `token.json` | Nothing whose job is tidying data files should hold write access to the secrets |
| Install or download anything | A repair that fetches code off the internet is a supply chain |
| Roll back `sessions.json` | Restoring an old copy resurrects sign-ins somebody revoked; a damaged store is emptied instead |
| Delete anything a person wrote | The damaged copy may be nine tenths intact, and that judgement is the owner's |
| Free disk by deleting files | Nobody wants that initiative from an assistant |

An empty Anthropic balance, a full disk, an expired Google refresh token and a
missing library are reported with the specific action for the user and **no
repair offered** — the alternative is fixing something adjacent and reporting
success, which is the failure mode this whole section exists to prevent.

`self_check` is passive (no consent gate); `self_repair` is an action and is
gated, and neither is offered to guests. `GET /api/selfcheck` and
`POST /api/selfrepair` exist as routes as well as tools, because the moment you
most want to ask "what is wrong with you" is the moment the model may be the
thing that is wrong. Every repair, asked-for or automatic, appends to
`repairs.log`.

The prevention half shipped with it: `notes.py` and `extras.py` truncated their
files before rewriting them, so an interruption left half a file that every
loader read as *empty* — and the next save then overwrote the remains. Both now
write to one side and `os.replace()` into position, matching `alerts.py`,
`alarm.py` and `session.py`.

### 3.11 Who owns the prompt

For most of ARC's life the system prompt was a template literal in
`index.html` and travelled up with every request. With exactly one user — the
person who wrote it — that was fine; nobody edits their own assistant's rulebook
in devtools to cheat themselves.

It stops being fine the moment anyone else signs in. **A prompt the client sends
is a prompt the client can change**: delete the ask-first safety lock, drop the
guest restrictions, hand yourself a different persona. The server had nothing to
compare an edited copy against, so every rule expressed in that prompt was a
request, politely worded. This was recorded as half of the client-enforced
consent gap in §7.

The rulebook now lives in `prompts/main.md` and `prompts/watch.md`, read by
`prompt.py`, and the request carries only a **name** — `main` or `watch`, with
anything else falling back to `main`, so a name can never become a path. The
server puts its own text first and the browser's second, which means a client
can **add** to the instructions and can never remove them. Send an empty
`system` from devtools and ARC is still ARC.

What the page still contributes is exactly what varies per turn: persona, mode,
language, the date, timers, what is on screen, consent state, lesson progress.
That reordering is itself an improvement — the chosen persona now lands *after*
the default character rather than before it, so it overrides rather than being
overridden.

**The same move pays for itself.** A prefix that no longer varies per request
can be cached, and cache reads bill at roughly a tenth of the input rate. The
base prompt is ~12,000 tokens and was re-sent in full on every turn *and* on
each of the up to six tool rounds within one turn — measured at $0.036 a send,
$0.0036 cached, so a six-round turn saves about $0.19. The block carries
`cache_control: {type: ephemeral}` only when it clears `MIN_CACHE_CHARS`
(4,000), because the API silently declines to cache a shorter prefix and a
breakpoint that never hits is worse than none.

Cached tokens are accounted separately (`tok_cache_read`, `tok_cache_write` at
0.1× and 1.25×). Folded into ordinary input they would have made `SPEND TODAY`
about five times too high and tripped `ARC_DAILY_COST_CAP` long before the money
was spent.

`ARC_MAX_SYSTEM_CHARS` drops from 96,000 to **24,000** as a result. It had been
rising to accommodate ARC's own prompt; now it bounds only what a caller may
add, which is what a limit like that is for.

## 4. Architecture

```
Browser (Web Speech STT, HUD, timers, memory)
    │  HTTPS  (tunnel)  /  http://localhost:8420
    ▼
FastAPI (run.py) ── auth gate ── rate limits ── agentic tool loop
    │                                              │
    ├── Anthropic API (proxied; keys server-side)   │
    ├── edge-tts / ElevenLabs                       │
    └── tool modules ───────────────────────────────┘
            gcal · gmail · gextra · tg · pc · extras · media
            display · notes · push · alerts
                    │
            flat JSON under DATA_DIR
```

**Why the proxy exists:** the Anthropic API deliberately sends no CORS headers
to browser origins, so a page cannot call it directly. The proxy also keeps the
key off the client, which matters the moment this stops being localhost.

**No database.** Everything is JSON files under `DATA_DIR` (`todos.json`,
`reminders.json`, `notes.json`, `price_alerts.json`, `alarms.json`, `token.json`,
`google_sessions/`) plus ~26 `localStorage` keys. Concurrency is guarded by an
`RLock`, with an explicit rule that the lock is never held across a network
fetch.

**No WebSockets, no SSE.** Everything polls: alarms every 5 s (oversleeping is
the one failure with a hard deadline), reminders and alerts every 20 s,
meeting nudges every 60 s, screen watch every 4 s, display every 2 s, markets
every 60 s. Streaming replies over SSE is the largest known perceived-latency
win and remains unbuilt.

**Deployment is Windows-desktop-first.** VBS/PowerShell launchers run a hidden
`pythonw run.py` and open a chromeless Chromium `--app` window with a dedicated
profile. The core stays up after the window closes. There is no Dockerfile, no
compose file, and no systemd unit. Public reach is via a Cloudflare quick tunnel
or a permanent Tailscale Funnel.

## 5. Security model

Full assessment in [SECURITY.md](SECURITY.md). The model in principle:

### 5.1 Authentication requirements

These are specified, not incidental — ARC is reachable from the public internet
and everything below sits behind this one gate.

| # | Requirement | Implementation |
|---|---|---|
| A1 | Identity comes from **Google OAuth 2.0 / OIDC**. No passwords, no shared secrets, nothing to leak or guess. | `/auth/login` → Google → `/oauth/callback` |
| A2 | **Google authenticating someone is not authorisation.** Access is limited to an explicit allowlist, checked *after* Google returns. | `ARC_ALLOWED_EMAILS`, default the owner's address |
| A3 | The user **re-consents on every sign-in**. No silent re-auth, no "remember me". | `prompt=consent` on every authorization request |
| A4 | A session ends when the person **leaves** — closing the tab or app signs you out, with a short grace window so a refresh survives. | `pagehide` → `POST /api/leave`; `ARC_SESSION_LEAVE_GRACE_SECONDS` |
| A4b | If they wander off without closing anything, an idle timeout catches it. | `ARC_SESSION_IDLE_MINUTES`, 30 |
| A4c | **The owner is exempt from every clock** — A4, A4b and A6 all apply to guests only. A timeout guards against a session outliving the person using it (a borrowed laptop, a stolen cookie); the owner's own machine is not that case, and re-authenticating on it half-hourly is cost without benefit. Revocation (A7) is what ends an owner session. | `ARC_OWNER_SESSION_UNLIMITED`, default on; `session.set_unlimited(ALLOWED_EMAILS - GUEST_EMAILS)` |
| A5 | "Unused" means the person, not the browser. Background polling must not hold a session open. | `BACKGROUND_PATHS`; `validate(touch=False)` |
| A5b | **Exception:** a set alarm is an instruction that this page must still be able to make a noise hours from now, which it cannot do signed out. While one is armed, and only for its own poll, the idle clock is held off. | `ARC_ALARM_KEEPS_SESSION`, default on; `alarm.armed()` |
| A6 | An optional **absolute cap** is available for a hard stop regardless of use, off by default, clamped to 4h when on. Guests only — see A4c. | `ARC_SESSION_MAX_HOURS`, `0` = off |
| A7 | Sessions are **revocable**. | Server-side store; deleting `sessions.json` signs everyone out |
| A7b | Revocation must not depend on a clock. Removing an address from the allowlist ends its sessions at the next request, and one account may hold only a bounded number of live sessions — both matter because A4c means an owner session has no clock to fall back on. | Allowlist re-check in `current_session()`; `ARC_MAX_SESSIONS_PER_ACCOUNT`, default 8, least-recently-used evicted |
| A8 | Tool access must not outlive the session that granted it. | The session's Google token is deleted with it; orphaned token files are cleared at startup |
| A9 | ARC **fails closed** — it refuses to start rather than come up ungated. | `auth_preflight()` on missing credentials, empty allowlist, or `open` mode on a public bind |
| A10 | Google scopes are **read-only except Calendar**. ARC must not be able to modify the mail account. | `gmail.readonly`; `gmail.compose` and `gmail.modify` both withheld |

| A11 | Signing in and being trusted are **separate questions**. An account can be admitted without inheriting the owner's reach. | `ARC_GUEST_EMAILS` — see 5.1.1 |

Standard OAuth hardening comes with it: PKCE (S256), an OIDC nonce, and a
CSRF `state`, all carried in a signed ten-minute cookie so a half-finished
sign-in survives a restart and abandoned attempts accumulate nothing.

#### 5.1.1 Guest accounts

A11 exists because the allowlist answers only "may this person in", and the
honest answer is often "yes, but not to everything". Demoing ARC to someone
otherwise required granting them the owner's Telegram and memory.

A guest is an address in `ARC_GUEST_EMAILS`. It is unioned into
`ARC_ALLOWED_EMAILS`, so listing someone as a guest is the entire change. The
tier is read from the **server-side session record only** — the client says
nothing about its own privilege level.

The rule: *your own Google account, and public facts.* Nothing owned by the
owner. That yields 13 tools rather than 45 — calendar, mail, drive and contacts
(safe because `apply_session_google` points every Google call at the signed-in
browser's own token, so the owner's account is unreachable rather than merely
forbidden), plus weather, stocks, news and web search.

Withheld in full: Telegram, notes and memory, todos, reminders, alarms, price alerts,
phone push, the second screen, and all computer control. A guest is also forced
non-local, so live screen and `pc` tools are off regardless of the socket.

Enforced in three places, on the principle that a filter the client can shape is
not a control:

1. `all_tools(local, guest)` — the model is never offered them
2. `dispatch_tool(..., guest)` — checked again where the work would happen
3. `deny_guest()` on the REST routes that reach owner data outside the tool
   loop — `/api/reminders/due` and `/api/alerts/due` (which *consume* on read,
   so a guest polling them would silently eat the owner's), `/api/alarms/due`,
   `/api/alarms/snooze` and `/api/alarms/dismiss` (a guest must not be able to
   see when the owner gets up, still less turn the alarm off), `/api/push/test`,
   `/api/display`

`GUEST_TOOLS` is default-deny, matching `PASSIVE_TOOLS`: a capability added
later is refused to guests until someone decides otherwise. `/api/health`
reports the reduced capability so the HUD cannot advertise what the server will
refuse, and the system prompt gains a guest preamble so the model declines
cleanly instead of promising and failing.

**Why idle rather than a fixed cap.** A4 was originally a hard four-hour cap.
It was replaced because it punished exactly the wrong person: someone using ARC
all afternoon got thrown out mid-sentence, while a browser abandoned on an
unlocked laptop stayed signed in until the cap happened to elapse. Tying the
session to use inverts that — it survives as long as you're there and ends
shortly after you leave, which is what "log off" already means to everyone.

A5 is the part that makes A4 true rather than decorative. The HUD polls
`/api/screen-watch` every four seconds and several other endpoints on slower
timers; while every request refreshed `last_seen`, the only way to go idle was
to close the tab. Background paths are now served without arguing that somebody
is present, so walking away is enough.

**Accepted cost.** An unattended session can live for a long time provided
something keeps genuinely using it, and the `/display` second screen — which
nobody interacts with — will drop out after the idle window unless the cap is
configured differently for that use. Revocation (A7) remains the immediate
answer for anything suspicious.

### 5.2 The rest of the model

- **Consent gate** — default-deny. Only an explicit passive allowlist runs
  unprompted; anything that acts needs a fresh affirmation.
- **Mail is read-only** — ARC holds `gmail.readonly` and nothing else. It cannot
  draft, send, reply, label, archive or delete. Mail is an input to ARC and
  never an output. This is the strongest single control in the product: the
  inbox is the main prompt-injection vector, and withholding the scope means an
  injected instruction has no mechanism to act on, rather than a mitigation
  layered over one that does.
- **Two-step sends for Telegram** — the one remaining outbound channel. Nothing
  can be sent that was not first drafted and then approved aloud, with an hourly
  cap and an audit log. It speaks *as the user* to real people, so a send cannot
  be unsent.
- **Least privilege on scopes** — Calendar is the only writable Google scope.
  Gmail, Contacts and Drive are read-only, and `gmail.modify` is not requested,
  so ARC has no way to hide its own tracks.
- **Computer control is localhost-only** and hard-disabled on any deployed
  instance. Shell commands are two-step and every one is logged with its exit
  code.
- **Prompt injection is treated as a first-class risk.** Your inbox is text
  written by strangers, and a message can address the assistant rather than you.
  Message bodies are fenced and declared data-never-instructions. This is a
  mitigation, not a control — **read what it drafted before approving it.**

## 6. Non-goals

- **Instagram, WhatsApp, SMS/iMessage.** Not for want of trying: Instagram
  retired its personal-media API at the end of 2024 and covers only
  Business/Creator accounts; WhatsApp has no personal-account API; Windows
  exposes no SMS or iMessage API. The only routes are scraping or fake-client
  automation, which violate the terms and get accounts banned. Telegram is the
  one mainstream messenger that publishes a client API and permits this.
- **Multi-tenancy.** Google identities are genuinely per-session, but notes,
  reminders, alerts, display, Telegram and computer control are global. ARC is
  not a product for other people to log into.
- **A native mobile app.** The PWA is the mobile story; the microphone requires
  real Chrome/Edge, which is also why this is not an Electron `.exe`.
- **Offline operation.** Reasoning is a rented API.
- **Purchases or anything irreversible without a human yes.**

### 3.6 Market analysis, and what it refuses to be

`market_outlook` answers "where is this going" with the only honest thing there
is: a **random walk with the instrument's own realised volatility**, giving a
probability band for a horizon. The central estimate is today's price, because
that is what the evidence supports; the information is in the width of the
band. Trend, momentum and RSI are reported as description of the past.

Three refusals are product requirements, not tone:

| # | Requirement | Where it is held |
|---|---|---|
| M1 | Never a price target, and never "it will". | Tool output carries the caveat; the system prompt forbids the phrasing; `tests/test_market.py` greps the output for forecast and advice language |
| M2 | Never a buy or sell recommendation, however hard the user pushes. | System prompt: "it is their money and their call", and ARC says it is not qualified |
| M3 | The uncertainty is spoken, not buried. | The odds are part of the sentence — "about two thirds of the time between X and Y" — rather than a disclaimer appended after a confident number |

This is also where the persona and the feature meet: §3.7's calibration rules
are what make M1–M3 natural rather than a special case ARC has to remember.

### 3.6b Languages

ARC is not an English product with translations bolted on: the recogniser, the
voice and the model all follow one setting, and the default follows the
device. 142 locales, ~75 languages.

| # | Requirement | Where it is held |
|---|---|---|
| L1 | The recogniser listens in the user's language. Getting this wrong is invisible — it returns confident nonsense and never errors. | `r.lang = speechLang()`, rebuilt on change |
| L2 | The voice speaks that language natively, never an English voice reading foreign letters. | `voices.for_lang()`; ElevenLabs stands aside when the language is not its voice's |
| L3 | The catalogue is fetched from Microsoft, never hand-written. A hand-written voice table is a list of guesses, and a wrong guess is TTS that fails when somebody speaks. | `voices.py`, cached a week, falls back to the original eight |
| L4 | The model answers in the language, matches a mid-conversation switch, and reads numbers and dates the way that language reads them. | `langBlock()` + the SPOKEN ALOUD rules |
| L5 | Text renders and reads the right way round in every script. | OS font fallbacks; `dir="auto"` per line |
| L6 | Nothing in the speech pipeline assumes Latin script. | `\p{L}\p{N}` throughout — see below |

L6 is the one that bit hardest. Seven filters normalised text with `[^a-z0-9]`,
and in Tamil, Arabic, Chinese, Greek or Hebrew every one of them produced an
empty string. The worst was the noise gate, where empty means "that was a
cough": **every word spoken in those languages was dropped before the model
ever saw it**. Echo detection was dead the same way, on both speech paths.

### 3.7 Calibration

ARC is asked to be excellent across the board and quiet about it. "Humble" here
means **calibrated**, not timid: give the real answer at full strength, let
stated confidence track the evidence, never bluff a name or a number, do not
hedge what is certain, and treat "I don't know" as a complete answer. The
failure this prevents is the expensive one — a confident wrong answer, which is
worse than no answer because it gets acted on.

## 7. Known gaps

| Gap | Detail | Tracked |
|---|---|---|
| ~~**`DEPLOY.md` does not exist**~~ | Closed. Written: tunnel, redirect URIs, consent-screen publishing status, what to verify, and the failures that have actually happened | #4 |
| ~~**No test suite**~~ | Closed. `tests/` holds 30 suites — sessions, guests, alarms, echo suppression, the mobile pass, and a parse check over the inline JavaScript — run on Windows in CI. Still untested: anything needing a real microphone, a second monitor or a phone (see `tests/manual/`) | #5 |
| ~~**Undeclared runtime dependencies**~~ | Closed, and the gap was narrower than recorded: `pc.py` uses `ctypes` and `PIL` for input and capture, not `mss` or `pyautogui`. Only `pycaw` was missing, now declared with a `sys_platform == "win32"` marker so Linux installs skip it | #5 |
| ~~**Duplicated dependency**~~ | Closed in "Tidy requirements.txt"; `edge-tts` appears once | #5 |
| **Unpinned dependencies** | All use `>=`, with no lockfile | #5 |
| **Consent gate is client-enforced** | `allow_actions` is still a boolean the caller sends. The system prompt half of this is CLOSED: the rulebook lives in `prompts/*.md` server-side, the client may only append, and its contribution is capped at 24,000 chars | #3 |
| ~~**No security headers**~~ | Closed. CSP, nosniff, frame denial, referrer and permissions policy on every response, HSTS over HTTPS; `ARC_SECURITY_HEADERS=0` disables. `'unsafe-inline'` for script stays — the HUD is one inline file with no build step — so the policy's value is in `connect-src`, `img-src` and `frame-ancestors`, not in script control | #2 |
| **No streaming** | `/api/chat` is request/response with a 25 s client abort | — |
| **Half-built multi-user** | Google identity is per-session, and **memory is now per-account too**; notes, reminders, alerts, display, Telegram and computer control are still global | — |
| **Second screen expires** | `/display` sits behind the session gate. Now moot for the owner, whose session has no clock (A4c), but it returns for anyone who sets `ARC_OWNER_SESSION_UNLIMITED=0` | — |
| **A restarted loop can leak its predecessor's thread** | If the old background loop is wedged inside a call that never returns, cancelling its task does not free the thread underneath — Python offers no way to. The replacement runs regardless, so alarms resume; the stuck thread ends when it ends. The watchdog gives up after three restarts that complete no cycle, rather than repeating for ever | §3.10 |
| **Second Bella not covered by the boot repair** | `selfheal` reads `ARC_DATA_DIR`, so each instance repairs its own files. Correct, but it means a private Bella that is never started is never checked or backed up | — |
| **Private instance needs its own redirect URI** | Bella on 8421 cannot sign in until `http://localhost:8421/oauth/callback` is registered in the Google console | — |

## 8. Roadmap

- **Offline wake word** — Picovoice Porcupine, trained on a custom phrase, no
  tab required.
- **Better ears** — faster-whisper (`small.en`, int8) locally, which beats the
  Web Speech API on accents and noise.
- **Speaker identification** — impossible in-browser; needs server-side
  diarization (AssemblyAI, Deepgram) or Azure Speaker Recognition for real names
  against enrolled voiceprints.
- **Streaming** — `/api/chat` over SSE, speaking the first sentence before the
  rest arrives. The biggest perceived-latency win available.
- **More hands** — the tool loop is generic; Home Assistant's REST API is the
  shortest next step.
