#!/usr/bin/env python3
"""
ARC â€” Ambient Response Core
Local launcher + API proxy.

    python run.py

Serves the UI at http://localhost:8420 and keeps your API keys server-side,
which is what lets the browser talk to Claude at all (the Anthropic API does
not send CORS headers to browser origins by design).
"""

import os
import sys
import time
import hmac
import asyncio
import socket
import secrets
import hashlib
import threading
import subprocess
import webbrowser
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import httpx
import uvicorn
import anthropic
import edge_tts
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response, FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

# Per-instance DATA lives here; shared CONFIG (API keys, OAuth client) stays in
# ROOT. Set ARC_DATA_DIR to run a second, fully separate Bella from the same
# code — its own reminders, notes, alerts, Google sign-ins and app window,
# isolated from the shared instance. Unset = ROOT, so nothing changes by default.
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# App identity. The private Bella (launched with ARC_APP_VARIANT=private) installs
# as its own app with a light-blue-on-black logo and its own name, so it's visibly
# distinct from the shared instance on your taskbar / home screen. Default = shared.
APP_VARIANT = os.getenv("ARC_APP_VARIANT", "").strip().lower()
PRIVATE_APP = APP_VARIANT == "private"

# Which brain a fresh browser starts on (the UI's switch, per-device, can
# override and remember).
#
# Auto for both instances now. The private Bella used to start on Fast to feel
# snappy out of the box, which was the right call when the only alternative was
# paying Sonnet for "what's the time" — but Auto answers those on Haiku too and
# still thinks properly when the question deserves it, so a blanket Fast is
# strictly worse. Leaving it would also have quietly cancelled Auto on the one
# instance most used by voice: the page starts on Auto and then health flips it.
#
# "deep" is deliberately NOT accepted here. A fresh browser landing on Opus
# would spend five times what it needed to on "good morning", and nobody
# choosing it would know they had.
DEFAULT_BRAIN = (os.getenv("ARC_DEFAULT_BRAIN") or "auto").strip().lower()
if DEFAULT_BRAIN not in ("auto", "smart", "fast"):
    DEFAULT_BRAIN = "auto"

# Tool modules read their configuration (TG_API_ID, ARC_TG_ALLOWLIST, ...)
# from the environment at import time, so they MUST be imported after .env is
# loaded â€” import them earlier and those settings silently read empty.
import gauth
import session
import gcal
import gmail
import gextra
import tg
import pc
import extras
import media
import display
import notes
import push

# One place that knows which module owns which tool, so adding a capability is
# one import and one entry rather than a chain of if-statements in the loop.
# pc (computer control) and media only report connected() when NOT deployed.
import alerts
import alarm
import market
import automation
import voices
import selfheal
import prompt
import stats
import triggers
import memory
import router
TOOLKITS = (gcal, gmail, gextra, tg, pc, extras, media, display, notes, push,
            alerts, alarm, market, automation, selfheal, stats, triggers,
            memory)
TOOL_OWNER = {t["name"]: kit for kit in TOOLKITS for t in kit.TOOLS}


# Everything a GUEST_EMAILS account is allowed to touch. Default-deny, the same
# idiom as PASSIVE_TOOLS below: a tool added later is refused to guests until
# someone decides otherwise, rather than silently inheriting access.
#
# The rule behind the list: a guest may use their OWN Google account and look up
# public facts. They may not read or change anything belonging to the owner.
# That rules out the whole of tg (owner's Telegram), notes (owner's memory),
# pc/media (owner's machine), display (owner's second screen), push (owner's
# phone), alerts (owner's watchlist), alarm (the owner's alarm clock), and
# todos/reminders (shared files).
GUEST_TOOLS = {
    # Their own mail, calendar, drive and contacts. Safe because
    # apply_session_google points every Google call at the signed-in browser's
    # own token — a guest literally cannot reach the owner's account here.
    "list_events", "create_event", "move_event", "cancel_event",
    "search_email", "read_email",
    "find_contact", "find_drive", "read_drive",
    # Public lookups. No owner data involved in any of them.
    "weather", "stock", "news", "web_search",
    # Market analysis: public prices in, arithmetic out. Nothing of the
    # owner's is touched, and the honesty is in the tool's own output rather
    # than in who is asking.
    "market_outlook", "market_compare",
}


def all_tools(local: bool = True, guest: bool = False):
    """Only offer what is actually authorised â€” a signed-in calendar with no
    mail should produce calendar tools, not tools that fail on contact. Computer
    control is offered only to the local desktop, never to a remote (tunnelled)
    caller, so the model isn't tempted to try what it can't do from the phone."""
    kits = [kit for kit in TOOLKITS
            if kit.connected() and (local or kit not in (pc, automation))]
    tools = [t for kit in kits for t in kit.TOOLS]
    if guest:
        tools = [t for t in tools if t["name"] in GUEST_TOOLS]
    return tools


def dispatch_tool(name: str, args: dict, local: bool = True,
                  guest: bool = False) -> tuple[str, bool]:
    # Checked again here rather than trusting all_tools() to have withheld it.
    # The tool list is built per turn from a request the client shapes; this is
    # the point where the work would actually happen, so this is where a guest
    # has to be stopped for the refusal to mean anything.
    if guest and name not in GUEST_TOOLS:
        return "Not available on a guest account.", True
    kit = TOOL_OWNER.get(name)
    if kit is None:
        return f"No such tool: {name}", True
    if kit is pc:
        return pc.run_tool(name, args, local=local)
    if kit is automation and not local:
        return ("Auto-clicking and key macros drive the mouse and keyboard of the "
                "machine ARC runs on, so they only work from the desktop app — not "
                "over the phone connection.", True)
    return kit.run_tool(name, args)


# --- consent gate ----------------------------------------------------------
# Tools that only LOOK something up / report — they never change the machine or
# send anything out, so they're always safe to run. Everything NOT in this set
# is treated as an ACTION and, unless the turn is authorised by the user, is
# refused. Default-deny: a new tool is gated until it's explicitly marked safe.
PASSIVE_TOOLS = {
    "find_files", "read_file", "screenshot", "list_monitors",
    "weather", "stock", "news", "web_search",
    "list_reminders", "list_todos", "list_events",
    "read_email", "search_email", "find_contact",
    "read_drive", "find_drive",
    "tg_list_chats", "tg_read_chat",
    # showing info on the user's own second screen is harmless output, not a
    # change to their machine — no consent prompt needed.
    "show_on_display", "clear_display",
    # capturing/reading notes is benign and user-requested; deleting stays gated.
    "add_note", "list_notes",
    # listing price alerts just reads them back; setting/clearing stays gated.
    "list_price_alerts",
    # Same split for alarms: list is a read, and silencing one that is ringing
    # right now is the most explicitly-asked-for thing in the world — stopping
    # to ask "may I turn your alarm off?" while it blares would be absurd.
    # Setting and cancelling stay gated, so ARC can't quietly unset your 7am.
    "list_alarms", "snooze_alarm", "dismiss_alarm",
    # Looking at what is installed or open changes nothing.
    "list_apps", "list_windows",
    # Market analysis is arithmetic on public prices.
    "market_outlook", "market_compare",
    # STOPPING must never need permission. An auto-clicker you have to authorise
    # ARC to switch off is not a feature, it is a hostage situation. Starting one
    # is gated; ending one is not, and neither is asking what is running.
    "stop_automation", "automation_status",
    # Looking at your own health changes nothing. self_repair DOES change
    # files, so it stays gated — "shall I fix that?" is a question worth
    # asking, and an assistant that quietly rewrites your notes file because
    # it decided the file looked wrong is not one anybody asked for.
    "self_check",
    # Reading your own usage record, and reading back the standing rules,
    # change nothing. Setting or clearing a rule does, so those stay gated.
    "usage_report", "list_triggers",
    # Reading back what ARC knows about you changes nothing. Forgetting
    # something DOES, and is irreversible, so it stays gated.
    "list_memory",
}
# Master switch. On by default: ARC will not act without the user's say-so. Set
# ARC_REQUIRE_CONSENT=0 to disable the gate entirely (not recommended).
REQUIRE_CONSENT = os.getenv("ARC_REQUIRE_CONSENT", "1").strip().lower() not in ("0", "false", "no", "off")


def _is_acting(name: str) -> bool:
    return name not in PASSIVE_TOOLS

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

# Microsoft Edge's neural voices — free, no key, no quota. This is the default
# spoken voice so ARC never sounds like a robot and never runs out of credit.
# en-GB-SoniaNeural is a natural British female. Others: en-GB-LibbyNeural,
# en-GB-RyanNeural (male), en-US-AriaNeural. Set ARC_TTS_VOICE to change.
TTS_VOICE = os.getenv("ARC_TTS_VOICE", "en-GB-SoniaNeural").strip()

# Which language the configured ElevenLabs voice actually sounds like. Its
# model is multilingual but its mouth is not: an English voice reading Tamil
# is worse than a Tamil voice reading Tamil, so anything else goes to Edge.
ELEVEN_LANG = os.getenv("ARC_ELEVEN_LANG", "en").strip().lower().split("-")[0]
# Whether to try ElevenLabs before the free Edge voice. Off by default: once its
# free quota is spent, attempting it just adds a 2-3s failed round-trip to every
# reply. Turn on only if the ElevenLabs account has credit.
PREFER_ELEVEN = os.getenv("ARC_PREFER_ELEVEN", "").strip().lower() in ("1", "true", "yes")

# ARC_MODEL is the default brain. Sonnet 5 is nearly as sharp as Opus but stays
# fast enough for a live voice loop, so it's the default. The UI can switch to
# the faster Haiku per-device at runtime (see MODEL_CHOICES / resolve_model),
# so an unset ARC_MODEL no longer silently drops to the weaker model.
MODEL = os.getenv("ARC_MODEL", "claude-sonnet-5")

# The brains the UI's switch offers: "smart" trades a little latency for depth,
# "fast" the reverse, "deep" gives up the voice loop's pace entirely for the
# strongest answer available. Overridable by env. resolve_model() maps whatever
# the client asked for to a real id, falling back to the default for anything
# unrecognised so a bad value can never reach the API.
#
# DEEP IS NEVER CHOSEN AUTOMATICALLY and never offered to a guest. Opus is five
# times Haiku's rate on both input and output — more than that per turn in
# practice, since it thinks for longer and those tokens are billed — and the
# same thinking means a noticeable wait before it says anything, which in a
# spoken conversation reads as a hang. Worth it when you ask for it and a poor
# trade when you did not. See router.py, which only ever returns smart or fast.
MODEL_CHOICES = {
    "smart": os.getenv("ARC_MODEL_SMART", "claude-sonnet-5"),
    "fast":  os.getenv("ARC_MODEL_FAST",  "claude-haiku-4-5-20251001"),
    "deep":  os.getenv("ARC_MODEL_DEEP",  "claude-opus-5"),
}


def thinking_for(model: str, want_on: bool) -> dict:
    """The thinking block to send, given what was asked for and who is answering.

    ARC turns thinking off by default because it is the single biggest source of
    reply latency, and a voice loop is judged on how quickly it starts talking.

    On Opus 5 that trade is not on offer. With thinking disabled, the model
    sometimes writes a tool call into its VISIBLE TEXT instead of emitting a
    tool_use block: nothing errors, the turn succeeds, the tool never runs — and
    ARC reads the tool call out loud. The same setting can leak internal tags
    into the reply, which also get spoken. Both are documented behaviour of that
    model rather than something a rule in the prompt can fix, and both are worse
    here than anywhere else, because the failure is audible and confident.

    So on Opus, thinking is on and `effort` is what controls depth instead. That
    is not a cost regression worth arguing about: deep is the brain the owner
    reached for on purpose, it already runs at a higher effort than the other
    two, and lower effort with thinking ON is both cheaper and safer than
    thinking OFF. Haiku and Sonnet keep the fast path — the failure is not
    documented there, and latency is the whole reason the default exists.
    """
    return {"type": "adaptive"} if (want_on or "opus" in (model or "").lower())         else {"type": "disabled"}

def resolve_model(choice) -> str:
    return MODEL_CHOICES.get(str(choice or "").strip().lower(), MODEL)


def supports_effort(model: str) -> bool:
    # The 'effort' output control exists on Sonnet/Opus but NOT on Haiku, which
    # rejects the request outright if it's sent. Gate it on the model in use.
    return "haiku" not in (model or "").lower()

# This ceiling covers thinking AND the spoken reply together. On Sonnet 5
# thinking is on unless you disable it, so the old 1000 was enough for the
# reasoning and nothing else: the answer came back truncated, or empty, and an
# empty reply is what ARC uses to mean "that wasn't addressed to me". A silent
# assistant that appeared to be working as designed. Spoken replies are short,
# so the extra headroom costs nothing on a normal turn.
MAX_TOKENS = int(os.getenv("ARC_MAX_TOKENS", "8000"))

# Ceiling on what the CLIENT may add to the prompt — persona, mode, language,
# the date, timers, screen, consent, lesson. ARC's own rulebook is not included
# and cannot be: it comes from prompts/main.md, server-side (see prompt.py).
#
# It was 96,000, and had to be, because the 44,282-character base prompt was
# being sent up with every request and the ceiling had to clear it. With the
# base held here, a realistic client contribution is around 7,000 characters,
# so this can go back to being what it was meant to be — a bound on abuse, with
# room to grow, rather than a number that had to keep chasing the prompt.
MAX_SYSTEM_CHARS = int(os.getenv("ARC_MAX_SYSTEM_CHARS", "24000"))

# The API will not cache a prefix shorter than about 1,024 tokens, and gives no
# error when you ask — it just never reports a hit. At roughly 3.7 characters
# per token of English prose that is ~3,800 characters; 4,000 leaves a margin.
MIN_CACHE_CHARS = 4000

# How hard to think. 'low' keeps the voice loop snappy; raise to medium/high
# only if you want more considered answers at the cost of a slower reply.
EFFORT = os.getenv("ARC_EFFORT", "low")

# The deep brain thinks harder as well as being a bigger model — asking Opus a
# question at 'low' spends Opus money for a Sonnet-shaped answer, which is the
# worst of both.
#
# 'high' is now a CHOICE rather than a ceiling. It used to be the highest value
# that could not 400: ARC sent Opus thinking-off, and thinking-off above 'high'
# is refused outright. thinking_for() ended that, so 'xhigh' and 'max' are both
# reachable here. They are not the default because this answers out loud and
# every extra second of thought is a second of silence after the question — set
# ARC_EFFORT_DEEP if you would rather wait and get the better answer.
EFFORT_DEEP = os.getenv("ARC_EFFORT_DEEP", "high")
_NO_THINK_MAX_EFFORT = ("low", "medium", "high")

# Default-model effort support (per-request calls use supports_effort(model)).
SUPPORTS_EFFORT = supports_effort(MODEL)

# Tool calls take extra round trips. This bounds a runaway loop without
# clipping legitimate work â€” look at the calendar, then act on it, is two.
MAX_TOOL_ROUNDS = int(os.getenv("ARC_MAX_TOOL_ROUNDS", "6"))

# Hosting platforms (Render, Railway, Fly) hand you a PORT and expect you to
# listen on every interface. Locally we stay on loopback so nothing on your
# network can reach it.
CLOUD = bool(os.getenv("PORT"))
PORT = int(os.getenv("PORT") or os.getenv("ARC_PORT", "8420"))
# Loopback by default so nothing on your network can reach it. Set
# ARC_HOST=0.0.0.0 to let other devices on your Wi-Fi (a phone) open it â€” only
# ever with ARC_AUTH_MODE left at google, or anyone on the network walks in.
HOST = os.getenv("ARC_HOST", "").strip() or ("0.0.0.0" if CLOUD else "127.0.0.1")

# --- access control ------------------------------------------------------
# A public URL is found by bots within days, and behind this one sit your mail,
# calendar, Drive, Telegram and (on the desktop) a shell. Sign-in is Google
# OAuth against an explicit allowlist: Google proves who you are, the allowlist
# decides whether that is anyone we accept.
#
#   google   the only sane mode for anything reachable off this machine
#   open     no auth at all. Refused unless the bind is loopback, because on
#            any other interface it hands the above to whoever finds the port.
AUTH_MODE = (os.getenv("ARC_AUTH_MODE", "google").strip().lower() or "google")
if AUTH_MODE not in ("google", "open"):
    AUTH_MODE = "google"

# Who may sign in. Google authenticating somebody is not authorisation — this
# is what turns "a real Google account" into "my account". Empty means nobody,
# and we refuse to start rather than fall open.
ALLOWED_EMAILS = {e.strip().lower() for e in
                  os.getenv("ARC_ALLOWED_EMAILS", "theepang@gmail.com").split(",")
                  if e.strip()}

# Signing in is one question; what you may do once in is another. Anyone listed
# here signs in normally and then gets a cut-down ARC: their own Google account
# and public lookups, and nothing that reads or changes the OWNER's life. Being
# listed here implies permission to sign in, so one line adds a guest.
GUEST_EMAILS = {e.strip().lower() for e in
                os.getenv("ARC_GUEST_EMAILS", "").split(",")
                if e.strip()}
ALLOWED_EMAILS |= GUEST_EMAILS

# The owner is not on a timer. Idle and absolute timeouts are there so a
# session cannot outlive the person using it — a phone left on a table, a
# borrowed laptop, a guest's stolen cookie. None of that describes the person
# whose machine this is, and signing in again every thirty minutes on your own
# desktop is a cost with nothing bought by it. Guests keep both clocks.
#
# Set ARC_OWNER_SESSION_UNLIMITED=0 to put the owner back on the same timers as
# everyone else. Either way the session is still revocable: /api/logout, or
# deleting sessions.json, ends it at once.
OWNER_EMAILS = ALLOWED_EMAILS - GUEST_EMAILS
OWNER_UNLIMITED = os.getenv("ARC_OWNER_SESSION_UNLIMITED", "1").strip().lower() \
    not in ("0", "false", "no", "off")
session.set_unlimited(OWNER_EMAILS if OWNER_UNLIMITED else ())

# Pins the OAuth redirect_uri instead of deriving it from forwarding headers a
# client can set. Leave empty on the desktop; set it to the funnel origin
# (https://…ts.net) when tunnelling.
PUBLIC_URL = os.getenv("ARC_PUBLIC_URL", "").strip().rstrip("/")

# Signs the short-lived OAuth state cookie and the Google session id. Set
# ARC_SECRET in production; otherwise we generate a throwaway one and the
# sign-in round trip breaks across a restart.
SECRET = os.getenv("ARC_SECRET", "").strip() or secrets.token_hex(32)
COOKIE = "arc_session"
OAUTH_COOKIE = "arc_oauth"
OAUTH_WINDOW = 600          # seconds a half-finished sign-in stays valid

# Per-visitor limits, and a whole-deployment ceiling. These are the brakes
# that stop one bad afternoon becoming a large bill.
RATE_PER_MIN = int(os.getenv("ARC_RATE_PER_MIN", "12"))
RATE_PER_HOUR = int(os.getenv("ARC_RATE_PER_HOUR", "120"))
DAILY_CAP = int(os.getenv("ARC_DAILY_CAP", "600"))

# Rough per-million-token prices so we can show a running daily spend and stop a
# runaway loop from quietly draining a paid key. Defaults track the default
# model (Sonnet 5); override if you switch. It's an estimate either way — the
# runtime brain switch means a day can mix Sonnet and Haiku turns. DAILY_COST_CAP
# is a generous ceiling — normal use costs pennies, so hitting it means a loop.
PRICE_IN = float(os.getenv("ARC_PRICE_IN_PER_MTOK", "3.0"))
PRICE_OUT = float(os.getenv("ARC_PRICE_OUT_PER_MTOK", "15.0"))
DAILY_COST_CAP = float(os.getenv("ARC_DAILY_COST_CAP", "5.0"))    # dollars; 0 = no cap

# Per-model prices, $ per million tokens, input and output.
#
# One flat pair was honest while one model answered everything. It stopped
# being honest the moment Auto started routing easy turns to Haiku: every one
# of those was recorded at Sonnet's rate, three times what it actually cost.
# The error is invisible — a plausible number is still printed — and it is the
# number the whole paywall gets priced off, so it has to be right.
#
# Matched by prefix, because a real id may carry a date (haiku-4-5-20251001).
#
# SONNET 5 IS $2/$10, NOT $3/$15. It launched at $2/$10 described as
# introductory pricing "through August 31, 2026", so this table was written
# against the $3/$15 it was going to revert to. That reversion was cancelled and
# $2/$10 is simply the price. Sonnet is the default brain and answers most of
# what Auto does not send to Haiku, so getting it wrong overstated the bill by
# half on the majority of turns — the same shape of error as the flat rate this
# table replaced, arriving as a stale constant instead of a missing one.
# Checked against platform.claude.com/docs/en/about-claude/pricing, 2026-08-26.
PRICES = {
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-5":   (2.0, 10.0),
    "claude-opus-4-6":   (5.0, 25.0),
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-opus-4-8":   (5.0, 25.0),
    "claude-opus-5":     (5.0, 25.0),
    "claude-fable-5":    (10.0, 50.0),
}


def prices_for(model) -> tuple:
    """(input, output) $/MTok for a model id.

    An unknown id falls back to the configured pair rather than to zero. A
    model ARC has never heard of costing nothing would be the one mistake that
    matters here: the daily cap exists to stop a runaway loop draining a card,
    and a loop that reports $0.00 is never capped at all.
    """
    m = str(model or "")
    for key in sorted(PRICES, key=len, reverse=True):
        if m.startswith(key):
            return PRICES[key]
    return (PRICE_IN, PRICE_OUT)

# The rate limiter keys off the client's address. X-Forwarded-For is set by a
# real proxy â€” but anyone can send it, so trusting it unconditionally let a
# single client rotate the header and defeat both the request cap AND the
# login lockout (unlimited password guesses). Trust it ONLY when you have
# actually put ARC behind a proxy and set this flag; otherwise use the real
# socket peer, which a client cannot spoof.
TRUST_PROXY = os.getenv("ARC_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes")

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"

C_DIM, C_CYAN, C_AMBER, C_RED, C_OFF = "\033[2m", "\033[96m", "\033[93m", "\033[91m", "\033[0m"


# --------------------------------------------------------------------------
# lifespan
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # httpx stays for ElevenLabs; Claude goes through the official SDK now that
    # there is a tool loop to drive.
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
    app.state.claude = (
        anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, timeout=90.0)
        if ANTHROPIC_KEY else None
    )
    # Anything left in google_sessions/ that no session points at is a live
    # Google refresh token nobody can use and nobody will notice. Clearing it
    # at startup is the one moment we know the store is settled.
    if AUTH_MODE != "open":
        try:
            # Called straight, not through a thread: it is one directory scan
            # of a handful of files, and it runs before the first request.
            n = prune_orphan_google_tokens()
            if n:
                print(f"{C_DIM}  · cleared {n} orphaned google token"
                      f"{'s' if n != 1 else ''}{C_OFF}")
        except Exception:
            pass

    # Self-repair, before anything else reads the data files. Boot is the only
    # moment nothing is halfway through a write, which is what makes a file
    # that will not parse unambiguous here and merely suspicious later. Two
    # capabilities it cannot reach on its own get handed over first.
    selfheal.register("prune_tokens", prune_orphan_google_tokens)
    selfheal.register("restart_monitor", lambda: restart_monitor())
    try:
        for line in selfheal.startup():
            print(f"{C_DIM}  · {line}{C_OFF}")
    except Exception as e:
        print(f"{C_DIM}  · self-check skipped: {e}{C_OFF}")

    # Phone-push loop: watch for reminders coming due and push them to the owner's
    # phone even with no browser open. Only runs when ntfy is configured; a single
    # owner topic means pushes never reach a signed-in visitor. Best-effort — any
    # error is swallowed so a flaky network can't take the server down.
    async def _monitor_loop():
        import anyio
        while True:
            try:
                # Price alerts: evaluate against live quotes every cycle. This runs
                # even without ntfy, because triggered alerts are also spoken in the
                # browser via /api/alerts/due — phone push is a bonus on top.
                await anyio.to_thread.run_sync(alerts.evaluate)

                # Standing rules: "tell me if Tesla drops below 200", "warn me
                # if I've spent five dollars today". Same cadence as the price
                # alerts above and the same shape — it notifies, and cannot
                # buy, sell or spend anything. See triggers.py.
                await anyio.to_thread.run_sync(triggers.evaluate)

                # Alarms: start any whose moment has come and reschedule the
                # repeating ones. Runs unconditionally, like alerts and for the
                # same reason — a ringing alarm is surfaced in the browser via
                # /api/alarms/due whether or not a phone is configured. Cheap
                # and offline, so it costs nothing on the cycles where nothing
                # is due.
                await anyio.to_thread.run_sync(alarm.evaluate)

                if push.configured():
                    # Reminders coming due → phone.
                    due = await anyio.to_thread.run_sync(extras.due_for_push)
                    for r in due:
                        label = r.get("label", "").strip() or "Reminder"
                        await anyio.to_thread.run_sync(
                            lambda m=label: push.send(m, title="ARC reminder", tags="alarm_clock"))
                    # Price alerts that just crossed → phone.
                    for msg in await anyio.to_thread.run_sync(alerts.pending_push):
                        await anyio.to_thread.run_sync(
                            lambda m=msg: push.send(m, title="ARC market alert",
                                                    tags="chart_with_upwards_trend"))
                    # Alarms going off → phone, at URGENT priority. Everything
                    # else here is a notification you read when you next look;
                    # this one has to get through a phone that is face-down and
                    # silenced at 3am, which is exactly what ntfy's priority 5
                    # is for. This is also the only delivery path that survives
                    # the browser being closed, so for waking someone it is the
                    # one that matters — which is why pending_push keeps
                    # returning the same alarm every couple of minutes while it
                    # rings, rather than once like everything above it.
                    for msg in await anyio.to_thread.run_sync(alarm.pending_push):
                        await anyio.to_thread.run_sync(
                            lambda m=msg: push.send(m, title="ARC alarm",
                                                    tags="alarm_clock",
                                                    priority="urgent"))
            except Exception:
                pass
            # Say so, every time round. This loop is what rings alarms, and a
            # loop that has stopped is indistinguishable from a quiet morning
            # unless something is counting. See selfheal.watch().
            selfheal.beat()
            await asyncio.sleep(30)

    home = asyncio.get_running_loop()

    def restart_monitor():
        """Replace the background loop. Called by selfheal when it has stalled.

        Cancelling first is best-effort and deliberately not awaited: the case
        this exists for is a loop wedged inside a call that will not return, so
        waiting for it to acknowledge the cancel would hang the very thing
        trying to rescue it. The new task starts either way.

        Scheduled onto the loop that owns it rather than created inline,
        because a task can only be created from its own event loop and this can
        be called from a worker thread. Creating it inline worked from the
        watchdog and from a route, silently failed anywhere else, and the
        failure was a coroutine that was never awaited — that is, a repair that
        reported success and did nothing.
        """
        def swap():
            old = getattr(app.state, "push_task", None)
            if old and not old.done():
                old.cancel()
            app.state.push_task = asyncio.create_task(_monitor_loop())
        selfheal.started()
        home.call_soon_threadsafe(swap)
        return True

    async def _watchdog_loop():
        """Watches the watcher.

        Separate from the loop it supervises, which is the entire point — a
        heartbeat check living inside the loop that stopped beating would stop
        with it. Left on the event loop rather than given a thread of its own
        because the work is a handful of stat calls a minute; restart_monitor
        is safe to call from either.
        """
        while True:
            await asyncio.sleep(60)
            try:
                for line in selfheal.watch():
                    print(f"{C_DIM}  · {line}{C_OFF}")
            except Exception:
                pass

    # Always run: even with ntfy off, alerts still need evaluating for the browser.
    selfheal.started()
    app.state.push_task = asyncio.create_task(_monitor_loop())
    app.state.watchdog_task = asyncio.create_task(_watchdog_loop())
    yield
    if app.state.watchdog_task:
        app.state.watchdog_task.cancel()
    if app.state.push_task:
        app.state.push_task.cancel()
    # The usage record batches its writes, so the last few turns are still in
    # memory here. Without this, every restart loses them — and a launcher that
    # restarts ARC is exactly what the owner runs several times a day.
    stats.flush()
    await app.state.http.aclose()
    if app.state.claude:
        await app.state.claude.close()


# With a password set we're deployed, and the auto-generated API docs would
# hand a stranger the full shape of the service. Keep them for local work.
_docs = "/docs" if AUTH_MODE == "open" else None
app = FastAPI(title="ARC", lifespan=lifespan,
              docs_url=_docs, redoc_url=None,
              openapi_url=None if _docs is None else "/openapi.json")


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def _sign(raw: str) -> str:
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


# Requests the HUD issues on its own timers, with nobody necessarily there.
# They are served normally on a live session but must NOT refresh the idle
# clock: screen-watch alone polls every four seconds, so if these counted as
# use, a browser left open on a locked laptop would stay signed in for ever and
# "signed in until you stop using it" would mean nothing. Everything not listed
# here — chatting, speaking, loading the page — is taken as a person being
# present. Default is therefore "counts as use"; a new poller has to be added
# here deliberately.
BACKGROUND_PATHS = {
    "/api/health", "/api/session", "/api/reminders/due", "/api/alerts/due",
    "/api/alarms/due", "/api/automation/status",
    "/api/calendar/upcoming", "/api/screen-watch", "/api/stocks",
    "/api/stock-search", "/api/push/status", "/api/display", "/api/voices",
    # Both are timers with nobody necessarily there: the trigger poll runs
    # beside the alert poll, and Arc Watch left open on a second screen would
    # otherwise hold a session alive for ever by refreshing itself.
    "/api/triggers/due", "/api/usage",
    # Announcing departure is the opposite of use. Without this the auth check
    # that runs ahead of the route would refresh the idle clock on the way in,
    # and the goodbye would read as a hello.
    "/api/leave",
}


# An alarm is the one background poll that is also a promise. The idle timeout
# exists so a browser nobody is sitting at stops being signed in; but somebody
# who sets an alarm for 7am has said, in as many words, that this page must
# still be able to make a noise at 7am — and it cannot do that signed out. So
# while an alarm is actually set, the alarm poll counts as use and holds the
# idle clock off; the moment the last one is dismissed, the clock resumes and
# the session times out normally.
#
# This is a real widening of "signed in until you stop using it", which is why
# it is one setting and easy to find. Turn it off and alarms still fire and
# still reach the phone — they just won't ring in a tab left open overnight,
# because that tab will have been signed out by then.
ALARM_KEEPS_SESSION = os.getenv("ARC_ALARM_KEEPS_SESSION", "1").strip().lower()     not in ("0", "false", "no", "off")


def current_session(request: Request, touch: bool | None = None):
    """The live session record for this request, or None.

    Every call re-checks both clocks, so a session that has outlived either is
    gone here rather than at the next login — there is no such thing as a
    request served on a dead session. Whether the idle clock is *refreshed*
    depends on the path: see BACKGROUND_PATHS.
    """
    if AUTH_MODE == "open":
        return {"email": "local", "created": 0, "last_seen": 0}
    path = request.url.path
    if touch is None:
        touch = path not in BACKGROUND_PATHS
        # The one exception, and only while an alarm is genuinely set — see
        # ALARM_KEEPS_SESSION above. Checked last so it can only ever turn a
        # background poll INTO use, never the other way round.
        if not touch and path == "/api/alarms/due" and ALARM_KEEPS_SESSION:
            try:
                touch = alarm.armed()
            except Exception:
                touch = False
    sid = request.cookies.get(COOKIE, "")
    rec = session.validate(sid, touch=touch)
    # The allowlist is the authority; the session record only caches what it
    # said at sign-in. Taking an address out of .env has to end its access at
    # the next request rather than whenever its session happens to lapse —
    # which, for an unlimited one, is never. Cheap: a set lookup per request.
    if rec and (rec.get("email") or "").lower() not in ALLOWED_EMAILS:
        session.revoke(sid)          # takes its Google token with it
        print(f"{C_AMBER}  ! signed out {rec.get('email')} — no longer on the "
              f"allowlist{C_OFF}")
        return None
    return rec


def authed(request: Request) -> bool:
    return current_session(request) is not None


def require_auth(request: Request):
    if not authed(request):
        raise HTTPException(401, "Not signed in.")


def tier(request: Request) -> str:
    """Which kind of account this is: "owner" or "guest".

    The owner account — the address in ARC_ALLOWED_EMAILS that is not also in
    ARC_GUEST_EMAILS — is the top of the tree and has no ceiling of any kind:
    every tool, every toolkit, no session clock, no idle timeout, no cap on
    what it may read or change. Guests get the default-deny list in GUEST_TOOLS
    and nothing else. There is deliberately no middle.

    ONE THING IS STILL WITHHELD FROM THE OWNER, and it is worth being explicit
    that it is not an oversight: computer control and input automation are
    offered only to a LOCAL request, never over the tunnel — see all_tools().
    That gate protects against a stolen session cookie, not against the owner.
    A cookie lifted from a phone would otherwise be a shell on the machine, from
    anywhere in the world, and no password would stand between the two. Say the
    word and it comes off, but it should come off on purpose.

    Stated as a function rather than left implicit because a paid tier will
    need somewhere to slot in, and "what can this account do" should have one
    answer in one place rather than three checks that drift apart.
    """
    return "guest" if is_guest(request) else "owner"


def is_guest(request: Request) -> bool:
    """Whether this request belongs to a cut-down guest account.

    Read from the server-side session record, never from the payload — the
    client decides nothing about its own privilege level. Open mode is the
    owner at their own desk by definition, so it is never a guest.
    """
    if not GUEST_EMAILS or AUTH_MODE == "open":
        return False
    rec = current_session(request)
    return bool(rec) and (rec.get("email") or "").lower() in GUEST_EMAILS


def deny_guest(request: Request):
    """For routes that read or touch the owner's own data outside the tool loop.
    The tool gate does not cover these — they are plain REST endpoints that any
    signed-in session can call directly."""
    if is_guest(request):
        raise HTTPException(403, "Not available on a guest account.")


def cookie_secure(request: Request) -> bool:
    """Whether to mark cookies Secure.

    Derived from the request, not from a PaaS-shaped env var: under cloudflared
    or a Tailscale Funnel there is no PORT set, so anything keyed off that
    concluded "not deployed" and shipped the auth cookie without Secure over a
    public HTTPS origin.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return proto == "https"


# How long the COOKIE may live when there is no absolute cap. Not how long the
# session lives — the server decides that, and a cookie for a session that has
# gone idle is refused on sight. It only has to outlast a session comfortably,
# because the cookie is written once at sign-in and never re-stamped: anything
# near the idle window would log out someone who was still using ARC.
#
# Deriving it from MAX_AGE is what broke sign-in when the cap was turned off —
# Max-Age=0 does not mean "no expiry", it tells the browser to delete the
# cookie at once, so the redirect back from Google arrived with nothing set.
COOKIE_MAX_AGE_UNCAPPED = 30 * 24 * 3600


# Browsers clamp a cookie's lifetime to 400 days no matter what is asked for,
# so this is "as long as it is possible to ask for" rather than an opinion.
# It is only how long the BROWSER keeps its copy; the server decides whether
# the session behind it is still live.
COOKIE_MAX_AGE_UNLIMITED = 400 * 24 * 3600


def set_session_cookie(resp, sid: str, request: Request, email: str = ""):
    # An owner session that never expires server-side must not be undone by the
    # browser throwing the cookie away on the absolute cap's schedule.
    if session.unlimited(email):
        max_age = COOKIE_MAX_AGE_UNLIMITED
    else:
        max_age = int(session.MAX_AGE) if session.MAX_AGE else COOKIE_MAX_AGE_UNCAPPED
    resp.set_cookie(
        COOKIE, sid,
        max_age=max_age,
        httponly=True,                    # JavaScript can't read it
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
    )


# --------------------------------------------------------------------------
# per-session Google: signing in IS linking your Google account, so one sign-in
# yields both the session and the token file the calendar/mail tools use. The
# file is named by the session's storage key rather than the session id itself,
# so a directory listing is not a list of live credentials — and so a session
# expiring can take its token with it.
# --------------------------------------------------------------------------
GOOGLE_DIR = DATA_DIR / "google_sessions"
GOOGLE_DIR.mkdir(parents=True, exist_ok=True)


def google_path_for_key(key: str) -> Path:
    # sha256 hex: [0-9a-f] only, so always safe as a filename.
    return GOOGLE_DIR / (f"{key}.json" if key else "none.json")


def google_path(sid: str) -> Path:
    return google_path_for_key(session.key_for(sid) if sid else "")


# A session's Google token dies with the session, rather than lingering on disk
# after the access it belonged to has expired.
session.set_evict_hook(lambda key: google_path_for_key(key).unlink(missing_ok=True))


def prune_orphan_google_tokens() -> int:
    """Delete token files no session can reach any more.

    The evict hook covers the ordinary path, but not the two ways a token gets
    stranded: the process being killed between writing the token and writing
    the store, and someone deleting sessions.json to sign everybody out — which
    the README recommends, and which on its own leaves every refresh token
    sitting there belonging to nobody.

    Only 64-hex names are considered, so `none.json` (the deliberate dead end
    for requests with no session) is never touched.
    """
    live = session.live_keys()
    gone = 0
    for f in GOOGLE_DIR.glob("*.json"):
        key = f.stem
        if len(key) == 64 and all(c in "0123456789abcdef" for c in key) and key not in live:
            try:
                f.unlink()
                gone += 1
            except Exception:
                pass
    return gone


def apply_session_google(request: Request):
    """Point every Google call in THIS request at the signed-in browser's own
    token. Missing/unconnected sessions get a path with no file, so the Google
    tools simply read as 'not connected' — never a fallback to anyone else's."""
    sid = request.cookies.get(COOKIE, "") if AUTH_MODE != "open" else ""
    if AUTH_MODE == "open" and not sid:
        # Local dev with no session: fall back to the owner's CLI token.
        gauth.set_active_token_path(None)
        return None
    gauth.set_active_token_path(google_path(sid))
    return sid


def apply_session_memory(request: Request) -> str:
    """Point memory at whoever is signed in, for the length of this request.

    Same shape as apply_session_google above, and for the same reason: the
    alternative is threading an address through every call site, including the
    tool dispatcher, which would eventually be forgotten somewhere — and the
    thing that leaks is one person's memory into another's conversation.

    A guest gets their own, which is the point. Their memory is theirs, and the
    owner's is not a window they can see through.
    """
    who = "owner"
    if AUTH_MODE != "open":
        rec = current_session(request) or {}
        who = (rec.get("email") or "").strip().lower() or "owner"
    memory.use(who)
    return who


def public_base_url(request: Request) -> str:
    """The externally-visible origin of this request, for the OAuth redirect_uri.

    ARC_PUBLIC_URL wins when set. Otherwise we fall back to the funnel's
    forwarding headers so the redirect matches what Google will call back
    (…ts.net over the tunnel, localhost on the desktop) — but those headers are
    client-settable, so pin ARC_PUBLIC_URL on anything public. Google rejecting
    an unregistered redirect_uri is the backstop, not the control.
    """
    if PUBLIC_URL:
        return PUBLIC_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    # Only loopback is ever reached over plain http. Anything else got here
    # through the tunnel, which is https-only — and guessing http there builds a
    # redirect_uri that isn't the registered one, so Google refuses the sign-in
    # outright. Don't make that hinge on a forwarding header being present.
    bare = host.split(":")[0].lower()
    if proto != "https" and bare not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        proto = "https"
    return f"{proto}://{host}"


async def read_json(request: Request):
    """Parse the JSON body, turning a malformed one into a clean 400 rather
    than an uncaught 500 (which is what a garbled request would otherwise
    produce on every JSON endpoint)."""
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "Malformed JSON body.")


# --------------------------------------------------------------------------
# rate limiting  (in-memory; resets when the process restarts)
# --------------------------------------------------------------------------

_hits = defaultdict(deque)
_day = {"stamp": time.strftime("%Y-%m-%d"), "count": 0, "tok_in": 0, "tok_out": 0,
        "tok_cache_read": 0, "tok_cache_write": 0, "cost": 0.0}

# Cached tokens are input tokens at a different price. A read is a tenth of the
# input rate, a write a quarter more than it — which is the whole reason the
# base prompt is cached, and also the reason those tokens cannot simply be
# added to tok_in. Counted at full rate, a cached turn would report about five
# times what it cost, and DAILY_COST_CAP would cut ARC off long before the
# money was actually spent.
CACHE_READ_RATE = 0.1
CACHE_WRITE_RATE = 1.25


# Web search is billed per search, not per token: $10 per thousand. It is the
# one line on the bill that does not scale with the model, which means it is
# also the one a cheap-brain turn cannot dilute — three searches on Haiku cost
# more than the Haiku did.
SEARCH_COST = float(os.getenv("ARC_SEARCH_COST", "0.01"))


def turn_cost(model, tok_in=0, tok_out=0, cache_read=0, cache_write=0,
              searches=0) -> float:
    """What one turn cost, at the price of the model that actually answered."""
    p_in, p_out = prices_for(model)
    return (tok_in / 1e6 * p_in
            + cache_read / 1e6 * p_in * CACHE_READ_RATE
            + cache_write / 1e6 * p_in * CACHE_WRITE_RATE
            + tok_out / 1e6 * p_out
            + searches * SEARCH_COST)


def _day_cost() -> float:
    """Today's spend, accumulated per turn rather than recomputed from totals.

    Summed tokens cannot be priced after the fact once more than one model is
    in play: a day of nine Haiku turns and one Opus turn has one token total
    and no single rate that describes it.
    """
    return _day["cost"]
_logins = defaultdict(deque)
_last_sweep = 0.0

# Every address that ever touches the server would otherwise be remembered
# forever. On a public URL that is bots scanning around the clock, and the
# tables grow without limit until the process runs out of memory.
SWEEP_EVERY = 300      # seconds
MAX_TRACKED = 20_000   # emergency ceiling


def sweep(now: float):
    global _last_sweep
    if now - _last_sweep < SWEEP_EVERY:
        return
    _last_sweep = now

    for table, window in ((_hits, 3600), (_logins, 600)):
        for ip in [k for k, q in table.items()
                   if not q or now - q[-1] > window]:
            del table[ip]
        # if something pathological is happening, start over rather than grow
        if len(table) > MAX_TRACKED:
            table.clear()


def client_ip(request: Request) -> str:
    # Only believe the forwarded header when we deliberately sit behind a proxy
    # (ARC_TRUST_PROXY). Otherwise it's attacker-controlled: rotating it would
    # let one client masquerade as thousands and slip every per-IP limit.
    #
    # Take the LAST element, not the first. cloudflared and Tailscale APPEND the
    # peer they saw rather than replacing the header, so the first element is
    # whatever the client sent — spoofable, and rotating it defeated both the
    # rate limits and the login lockout. The last element is the one our own
    # trusted proxy wrote, and is the only entry a remote client cannot choose.
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if parts:
                return parts[-1]
    return request.client.host if request.client else "unknown"


def is_local_request(request: Request) -> bool:
    """True only for the desktop app talking to ARC directly on this machine.

    The tunnel (cloudflared) also runs on this PC, so tunnelled requests reach
    us on 127.0.0.1 too — the socket alone can't tell them apart. What it CAN'T
    fake is the absence of a forwarding header: the desktop app connects with no
    proxy in the path, so no X-Forwarded-For / CF-Connecting-IP. cloudflared
    always adds those, and a remote client can't strip them. So: loopback peer
    AND no forwarding headers == the real local desktop. This is what gates
    computer control on to the desktop and off for the phone."""
    peer = request.client.host if request.client else ""
    if peer not in ("127.0.0.1", "::1"):
        return False
    if request.headers.get("x-forwarded-for") or request.headers.get("cf-connecting-ip"):
        return False
    return True


def check_rate(request: Request):
    today = time.strftime("%Y-%m-%d")
    if _day["stamp"] != today:
        # cost resets with the counts. Left behind, yesterday's spend would
        # still be measured against today's cap — and once it crossed, ARC
        # would refuse to answer every day from then on.
        _day.update(stamp=today, count=0, tok_in=0, tok_out=0,
                    tok_cache_read=0, tok_cache_write=0, cost=0.0)
    if _day["count"] >= DAILY_CAP:
        raise HTTPException(429, "Daily limit reached for this deployment. Try again tomorrow.")
    if DAILY_COST_CAP > 0 and _day_cost() >= DAILY_COST_CAP:
        raise HTTPException(429, "I've hit today's spending safety cap. It resets tomorrow, "
                                 "or raise ARC_DAILY_COST_CAP if this was on purpose.")

    ip = client_ip(request)
    now = time.time()
    sweep(now)
    q = _hits[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= RATE_PER_HOUR:
        raise HTTPException(429, "Hourly limit reached. Give it a little while.")
    if sum(1 for t in q if now - t < 60) >= RATE_PER_MIN:
        raise HTTPException(429, "Slow down a moment.")

    q.append(now)
    _day["count"] += 1


# Failed sign-ins, counted globally as well as per-IP. Per-IP alone was the
# whole of the old defence, and it rested entirely on client_ip() being
# truthful; a distributed attempt, or a spoofing bug like the one above, walked
# straight past it. This ceiling does not care whose address it is.
_login_failures_global = deque()
GLOBAL_LOGIN_FAILURES = 60      # per ten minutes, across everyone


def check_login_rate(request: Request):
    """
    Stops anyone grinding the sign-in endpoint. Only *failed* attempts count,
    and a success wipes the slate â€” otherwise everyone sharing a home or office
    connection gets locked out together over somebody's typo.
    """
    now = time.time()
    sweep(now)

    while _login_failures_global and now - _login_failures_global[0] > 600:
        _login_failures_global.popleft()
    if len(_login_failures_global) >= GLOBAL_LOGIN_FAILURES:
        raise HTTPException(429, "Too many sign-in attempts. Wait ten minutes.")

    q = _logins[client_ip(request)]
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many attempts. Wait ten minutes.")


def note_login_failure(request: Request):
    now = time.time()
    _logins[client_ip(request)].append(now)
    _login_failures_global.append(now)


def clear_login_failures(request: Request):
    _logins.pop(client_ip(request), None)


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARC — your private voice AI</title>
<meta name="description" content="ARC is a private, voice-first AI assistant. It listens, sees your screen, manages your calendar and mail, and watches the markets — self-hosted, so it runs on your own machine.">
<style>
*{box-sizing:border-box}
:root{--bg:#04070c;--ice:#5fd9ff;--ink:#dbe9f5;--dim:#5a86a0;--deep:#1c6d8f;
 --line:#12293c;--up:#8affd4;--err:#ff7d96;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
html,body{margin:0;min-height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--mono);
 min-height:100vh;display:flex;flex-direction:column;align-items:center;
 justify-content:center;padding:36px 20px;overflow-x:hidden;position:relative}
/* ambient glow + faint grid, so the page feels alive like the app */
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background:
   radial-gradient(60% 45% at 50% 32%,rgba(95,217,255,.14),transparent 60%),
   radial-gradient(40% 40% at 50% 100%,rgba(90,255,212,.06),transparent 60%);}
body::after{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.5;
 background-image:linear-gradient(rgba(95,217,255,.05) 1px,transparent 1px),
   linear-gradient(90deg,rgba(95,217,255,.05) 1px,transparent 1px);
 background-size:44px 44px;mask-image:radial-gradient(70% 60% at 50% 40%,#000,transparent 75%)}
.wrap{position:relative;z-index:1;width:min(440px,94vw);text-align:center}
.ring{width:88px;height:88px;margin:0 auto 22px;display:block;
 filter:drop-shadow(0 0 12px rgba(95,217,255,.55))}
.ring .r1{animation:spin 18s linear infinite;transform-origin:50% 50%}
.ring .r2{animation:spin 26s linear infinite reverse;transform-origin:50% 50%}
.ring .core{animation:pulse 3.2s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.55}50%{opacity:1}}
h1{font-size:30px;letter-spacing:.5em;margin:0 0 8px;font-weight:400;
 text-indent:.5em;color:#eaf6ff;text-shadow:0 0 18px rgba(95,217,255,.35)}
.sub{color:var(--deep);font-size:10px;letter-spacing:.34em;text-transform:uppercase;margin:0 0 20px}
.tag{color:#a9cfe2;font-size:14px;line-height:1.6;letter-spacing:.01em;margin:0 auto 24px;max-width:380px}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:0 0 28px}
.chip{font-size:11px;letter-spacing:.03em;color:#bfe3f2;padding:7px 11px;
 border:1px solid var(--line);background:rgba(9,19,32,.5);border-radius:999px;white-space:nowrap}
.signin{display:flex;align-items:center;justify-content:center;gap:11px;width:100%;
 padding:14px;background:linear-gradient(180deg,#7fe4ff,#43caf0);
 color:#04121a;border:0;font:inherit;font-size:12px;font-weight:600;letter-spacing:.14em;
 text-transform:uppercase;cursor:pointer;border-radius:10px;text-decoration:none;
 transition:filter .15s,transform .05s}
.signin:hover{filter:brightness(1.08)}
.signin:active{transform:translateY(1px)}
.signin svg{width:17px;height:17px;flex:none}
.note{color:var(--dim);font-size:11px;line-height:1.6;margin:14px auto 0;max-width:340px;letter-spacing:.02em}
.err{color:var(--err);font-size:12px;margin-top:13px;min-height:16px;letter-spacing:.02em}
.trust{display:flex;flex-wrap:wrap;gap:6px 16px;justify-content:center;margin:26px 0 0;
 color:var(--dim);font-size:10.5px;letter-spacing:.04em}
.trust span{display:inline-flex;align-items:center;gap:5px}
.foot{margin-top:22px;font-size:10.5px;letter-spacing:.03em;color:var(--dim)}
.foot a{color:var(--ice);text-decoration:none;border-bottom:1px solid rgba(95,217,255,.4)}
.foot a:hover{filter:brightness(1.15)}
@media(max-width:420px){h1{font-size:24px}.tag{font-size:13px}}
</style></head><body>
<div class="wrap">
  <svg class="ring" viewBox="0 0 100 100" aria-hidden="true">
    <g fill="none" stroke="#5fd9ff">
      <circle class="r1" cx="50" cy="50" r="44" stroke-width="1.5" stroke-dasharray="6 10" opacity=".7"/>
      <circle class="r2" cx="50" cy="50" r="34" stroke-width="1.5" stroke-dasharray="3 8" opacity=".5"/>
      <circle cx="50" cy="50" r="24" stroke-width="1" opacity=".35"/>
    </g>
    <circle class="core" cx="50" cy="50" r="9" fill="#5fd9ff"/>
  </svg>
  <h1>ARC</h1>
  <div class="sub">Ambient Response Core</div>
  <p class="tag">Your own voice-first AI. It listens, sees your screen, runs your
     calendar and mail, and keeps an eye on the markets — all on your terms.</p>
  <div class="chips">
    <span class="chip">🎙 Just say “Bella”</span>
    <span class="chip">🖥 Sees your screen</span>
    <span class="chip">📅 Your calendar &amp; mail</span>
    <span class="chip">📈 Live markets</span>
  </div>
  <a class="signin" href="/auth/login">
    <svg viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-2.7-.4-3.9H24v7.1h12.1c-.2 1.8-1.6 4.6-4.5 6.4l6.9 5.3c4.1-3.8 6.6-9.4 6.6-14.9z"/>
      <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-6.9-5.3c-1.8 1.3-4.3 2.2-7.6 2.2-5.8 0-10.7-3.8-12.5-9.1l-7.1 5.5C8.1 41.1 15.4 46 24 46z"/>
      <path fill="#FBBC05" d="M11.5 28.5c-.5-1.4-.7-2.9-.7-4.5s.3-3.1.7-4.5l-7.1-5.5C2.9 17 2 20.4 2 24s.9 7 2.4 10z"/>
      <path fill="#EA4335" d="M24 10.6c4.1 0 6.9 1.8 8.5 3.3l6.2-6C34.9 4.5 29.9 2 24 2 15.4 2 8.1 6.9 4.4 14l7.1 5.5c1.8-5.3 6.7-8.9 12.5-8.9z"/>
    </svg>
    Sign in with Google
  </a>
  <div class="err" id="e"></div>
  <p class="note">__SESSION_NOTE__</p>
  <div class="trust">
    <span>🔒 Self-hosted &amp; private</span>
    <span>🛡 Encrypted connection</span>
    <span>⚡ Powered by Claude</span>
    <span>✳ Google sign-in</span>
  </div>
  <div class="foot">Want your own ARC? <a href="mailto:theepang@gmail.com?subject=I%27d%20like%20my%20own%20ARC">Request access →</a></div>
</div>
<script>
// The callback reports failures in the query string rather than in a body,
// because getting here is the end of a redirect chain, not a fetch.
const REASONS = {
  denied:   "You cancelled the Google sign-in.",
  notyou:   "That Google account isn't allowed to use this ARC.",
  expired:  "That sign-in took too long. Try again.",
  error:    "Sign-in failed. Try again.",
  timeout:  "Your session ended. Sign in again.",
  setup:    "Google sign-in isn't configured on this server yet."
};
const why = new URLSearchParams(location.search).get("auth");
if (why && REASONS[why]) document.getElementById("e").textContent = REASONS[why];
</script></body></html>"""

# Says what actually happens, which depends on whether the absolute cap is on.
# Promising "at most N hours" with no cap configured would simply be untrue.
_idle_mins = f"{session.IDLE_AGE / 60:g}"
if OWNER_UNLIMITED and GUEST_EMAILS:
    _session_note = ("Sign-in is limited to the owner's Google account, plus invited guests. "
                     f"The owner stays signed in; guests are signed out after {_idle_mins} "
                     "minutes idle.")
elif OWNER_UNLIMITED:
    _session_note = ("Sign-in is limited to the owner's Google account. You stay signed in "
                     "until you sign out.")
elif session.MAX_AGE:
    _session_note = (f"Sign-in is limited to the owner's Google account. You stay signed in "
                     f"while you're using it, and at most {session.MAX_AGE / 3600:g} hours.")
else:
    _session_note = (f"Sign-in is limited to the owner's Google account. You stay signed in "
                     f"while you're using it, and are signed out after {_idle_mins} minutes idle.")
LOGIN_HTML = LOGIN_HTML.replace("__SESSION_NOTE__", _session_note)


# The second-screen display: a glanceable dashboard meant to sit on a second
# monitor. Big clock, live markets, and a spotlight card for whatever ARC pushes
# with show_on_display. Self-contained; polls /api/display and /api/stocks.
DISPLAY_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARC · Second Screen</title><style>
*{box-sizing:border-box}
:root{--bg:#04070c;--ice:#5fd9ff;--ink:#dbe9f5;--dim:#5a86a0;--deep:#1c6d8f;
 --line:#12293c;--up:#8affd4;--down:#ff7a8a;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--mono);height:100vh;
 overflow:hidden;padding:4vh 4vw;display:flex;flex-direction:column;position:relative;cursor:none}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background:radial-gradient(60% 50% at 50% 30%,rgba(95,217,255,.10),transparent 60%)}
.top{position:relative;z-index:1;display:flex;justify-content:space-between;align-items:flex-start}
.clock{font-size:12vw;line-height:.9;letter-spacing:.02em;color:#eaf6ff;
 text-shadow:0 0 30px rgba(95,217,255,.3);font-weight:400}
.date{font-size:1.5vw;letter-spacing:.28em;text-transform:uppercase;color:var(--deep);margin-top:1.2vh}
.brand{text-align:right;color:var(--deep)}
.brand .n{font-size:1.6vw;letter-spacing:.5em;color:var(--ice)}
.brand .s{font-size:.85vw;letter-spacing:.3em;text-transform:uppercase;margin-top:.5vh}
.mkts{display:flex;gap:2.4vw;margin-top:1.5vh;flex-wrap:wrap}
.mkts .m{font-size:1.3vw;letter-spacing:.04em}
.mkts .sym{color:var(--ice)}
.mkts .pr{color:var(--ink)}
.mkts .up{color:var(--up)}.mkts .down{color:var(--down)}
.spot{position:relative;z-index:1;flex:1;display:flex;flex-direction:column;
 justify-content:center;margin-top:3vh}
.spot .card{border:1px solid var(--line);background:linear-gradient(180deg,rgba(9,19,32,.6),rgba(4,7,12,.4));
 padding:4vh 4vw;border-radius:14px;box-shadow:0 0 40px rgba(4,7,12,.6),inset 0 0 40px rgba(95,217,255,.04)}
.spot .h{font-size:3vw;color:var(--ice);letter-spacing:.02em;margin:0 0 2.5vh;line-height:1.15}
.spot .b{font-size:2vw;color:var(--ink);line-height:1.55;white-space:pre-wrap;
 max-height:52vh;overflow:hidden}
.idle{position:relative;z-index:1;flex:1;display:flex;flex-direction:column;
 align-items:center;justify-content:center;color:var(--deep)}
.idle .ring{width:12vw;height:12vw;margin-bottom:3vh;filter:drop-shadow(0 0 14px rgba(95,217,255,.5))}
.idle .ring circle{animation:spin 20s linear infinite;transform-origin:50% 50%}
.idle .core{animation:pulse 3.2s ease-in-out infinite}
.idle .msg{font-size:1.3vw;letter-spacing:.24em;text-transform:uppercase}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.5}50%{opacity:1}}
.hidden{display:none}
</style></head><body>
<div class="top">
  <div>
    <div class="clock" id="clock">--:--</div>
    <div class="date" id="date"></div>
    <div class="mkts" id="mkts"></div>
  </div>
  <div class="brand"><div class="n">ARC</div><div class="s">Second Screen</div></div>
</div>
<div class="spot hidden" id="spot"><div class="card">
  <h1 class="h" id="spotH"></h1><div class="b" id="spotB"></div>
</div></div>
<div class="idle" id="idle">
  <svg class="ring" viewBox="0 0 100 100" fill="none" stroke="#5fd9ff">
    <circle cx="50" cy="50" r="42" stroke-width="1.5" stroke-dasharray="6 10" opacity=".7"/>
    <circle class="core" cx="50" cy="50" r="8" fill="#5fd9ff" stroke="none"/>
  </svg>
  <div class="msg">Ready · ask ARC to show something here</div>
</div>
<script>
function tick(){
  const d=new Date();
  const hh=String(d.getHours()).padStart(2,"0"), mm=String(d.getMinutes()).padStart(2,"0");
  document.getElementById("clock").textContent=hh+":"+mm;
  document.getElementById("date").textContent=d.toLocaleDateString(undefined,
    {weekday:"long",month:"long",day:"numeric"});
}
setInterval(tick,1000); tick();

let lastTs=-1;
async function pollBoard(){
  try{
    const b=await (await fetch("/api/display")).json();
    if(b.ts===lastTs) return; lastTs=b.ts;
    const has=(b.title&&b.title.trim())||(b.text&&b.text.trim());
    document.getElementById("spot").classList.toggle("hidden",!has);
    document.getElementById("idle").classList.toggle("hidden",!!has);
    document.getElementById("spotH").textContent=b.title||"";
    document.getElementById("spotH").style.display=(b.title&&b.title.trim())?"":"none";
    document.getElementById("spotB").textContent=b.text||"";
  }catch(_){}
}
setInterval(pollBoard,2000); pollBoard();

// Read the watchlist fresh each time: the main HUD writes arc.tickers when you
// add/remove a market, and this second-screen window must pick that up rather
// than hold the snapshot it had at load (which is why a just-added ticker never
// appeared here before).
function readTickers(){
  let t;
  try{t=JSON.parse(localStorage.getItem("arc.tickers")||"null");}catch(_){}
  return (Array.isArray(t)&&t.length)?t:["AAPL","TSLA","NVDA","BTC-USD"];
}
let tickers=readTickers();
async function pollMkts(){
  tickers=readTickers();
  try{
    const d=await (await fetch("/api/stocks?symbols="+encodeURIComponent(tickers.join(",")))).json();
    const q=(d&&d.quotes)||[]; let html="";
    q.forEach(s=>{
      const price=s.price>=1000?Math.round(s.price).toLocaleString():(Math.round(s.price*100)/100);
      const pct=(s.pct==null)?"":(s.pct>=0?"+":"")+s.pct.toFixed(1)+"%";
      const cls=(s.pct==null)?"":(s.pct>=0?"up":"down");
      const sym=String(s.symbol).replace("-USD","");
      html+='<span class="m"><span class="sym">'+sym+'</span> <span class="pr">'+price+
        '</span> <span class="'+cls+'">'+pct+'</span></span>';
    });
    document.getElementById("mkts").innerHTML=html;
  }catch(_){}
}
setInterval(pollMkts,60000); pollMkts();
// Same-origin windows get a 'storage' event the instant the main HUD changes the
// watchlist, so an added/removed market shows here immediately, not up to 60s later.
window.addEventListener("storage",function(e){ if(!e||e.key===null||e.key==="arc.tickers") pollMkts(); });

// F or double-click toggles full screen for a clean second-monitor view.
function toggleFs(){
  const r=document.documentElement;
  if(!document.fullscreenElement){(r.requestFullscreen||r.webkitRequestFullscreen||function(){}).call(r);}
  else{(document.exitFullscreen||document.webkitExitFullscreen||function(){}).call(document);}
}
document.addEventListener("keydown",e=>{if(e.key==="f"||e.key==="F"){e.preventDefault();toggleFs();}});
document.addEventListener("dblclick",toggleFs);
</script></body></html>"""


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get("/api/health")
async def health(request: Request, _=Depends(require_auth)):
    """The UI calls this on boot so it can show what's actually wired up."""
    apply_session_google(request)   # report THIS user's Google, not a shared one
    # A guest's HUD must not advertise what the server will refuse: reporting
    # telegram/computer as online here would light up controls that then fail.
    guest = is_guest(request)
    local = is_local_request(request) and not guest
    return {
        "claude": bool(ANTHROPIC_KEY),
        "calendar": gcal.connected(),
        "email": gmail.connected(),
        "contacts_drive": gextra.connected(),
        "telegram": tg.connected() and not guest,
        "guest": guest,
        # Computer control is real only for the local desktop; over the tunnel
        # the phone gets everything else but not shell/system control.
        "computer": pc.connected() and local,
        # How many screens are attached, so the UI can offer a per-monitor
        # choice only when there is actually a choice to make.
        "monitors": len(pc._list_monitors()) if local else 0,
        # A natural server voice is always available now (free Edge neural TTS,
        # ElevenLabs on top when it has credit). The UI keys off this flag to
        # use server audio instead of the browser's robot voice.
        "elevenlabs": True,
        "model": MODEL,
        # The brains the UI's switch can pick between, and which one a fresh
        # browser should start on (private = fast, shared = smart). "deep" is
        # listed for everyone but only honoured for the owner — the page shows
        # what exists, the server decides who gets it.
        "models": MODEL_CHOICES,
        "default_brain": DEFAULT_BRAIN,
    }


def _claude_error(e) -> HTTPException:
    """Turn a provider error into something a person can act on.

    What used to reach the transcript was the raw payload — a Python dict of
    type, error, type again, message and request_id, wrapped in "Link failure".
    Everything needed to fix it was in there, buried in punctuation, and the one
    sentence that mattered ("your credit balance is too low") read like a crash.

    The full text still goes to the server log. What the user gets is the thing
    to do about it.
    """
    status = getattr(e, "status_code", 500) or 500
    raw = str(getattr(e, "message", "") or e)
    low = raw.lower()
    print(f"{C_RED}  ! anthropic {status}: {raw[:300]}{C_OFF}")

    if "credit balance" in low or "insufficient" in low:
        # Not a fault, and not something ARC can retry its way out of.
        return HTTPException(402, "Your Anthropic account is out of credit, so I "
                                  "can't think until it's topped up. Add credit at "
                                  "console.anthropic.com under Plans & Billing, "
                                  "then say anything to try again.")
    if status == 401 or "authentication" in low or "invalid x-api-key" in low:
        return HTTPException(401, "The Anthropic API key is missing or invalid. "
                                  "Check ANTHROPIC_API_KEY in your .env and restart me.")
    if status == 403:
        return HTTPException(403, "This Anthropic key isn't permitted to use that "
                                  "model. Try the other brain, or check the key's "
                                  "access in the console.")
    if status == 404 and "model" in low:
        return HTTPException(404, "That model doesn't exist for this key. Switch "
                                  "brains, or check ARC_MODEL in your .env.")
    if status == 413 or "too large" in low or "prompt is too long" in low:
        return HTTPException(413, "That was too much to send in one go — usually a "
                                  "very long screen capture or conversation. Clear "
                                  "the transcript and try again.")
    if status == 429:
        return HTTPException(429, "Anthropic is rate-limiting this key. Give it a "
                                  "moment and try again.")
    if status == 529 or "overloaded" in low:
        return HTTPException(529, "Anthropic is overloaded at the moment. Try again "
                                  "in a few seconds.")
    if status >= 500:
        return HTTPException(502, "Anthropic had a problem at their end. Not you, "
                                  "not me — try again shortly.")
    # Anything unrecognised: one line of theirs, trimmed, rather than the payload.
    first = raw.split("{")[0].strip() or raw[:160]
    return HTTPException(status, f"Anthropic refused that request: {first[:200]}")


@app.post("/api/chat")
async def chat(request: Request, _=Depends(require_auth)):
    """Proxy to the Messages API. The key never reaches the browser."""
    check_rate(request)
    if not ANTHROPIC_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set. Add it to .env and restart.")

    payload = await read_json(request)
    messages = payload.get("messages") or []
    # What the CLIENT contributes: persona, mode, language, the date, timers,
    # what is on screen. Not the rulebook — that is prompt.base(), added below,
    # and it goes FIRST so nothing sent from a browser can drop or contradict
    # it. See prompt.py for why that distinction is the whole point.
    extra = payload.get("system") or ""
    prompt_name = payload.get("prompt") or "main"
    use_search = bool(payload.get("search"))
    # Either a bool (legacy: "yes, the primary") or which monitor(s) to attach —
    # "each", "all", "primary", "2". Anything truthy but unrecognised falls
    # through to pc.screenshot's own default.
    see_screen_raw = payload.get("see_screen")
    see_screen = bool(see_screen_raw)
    # Consent: only a genuine, user-authorised turn may run action tools. The
    # client sends this true only for turns the user themselves drove and ok'd;
    # background/automated calls (e.g. watch-mode glances) never set it, so they
    # can never act. See PASSIVE_TOOLS / _is_acting.
    allow_actions = bool(payload.get("allow_actions"))
    # Which brain this turn runs on. The UI's switch sends "smart", "fast" or
    # "auto"; anything unrecognised falls back to the server default.
    #
    # On "auto" the router picks per QUESTION rather than per person — the time
    # and "stop" go to Haiku, anything with reasoning in it goes to Sonnet. It
    # is biased towards the expensive one on purpose: a hard question answered
    # cheaply is a bad answer, an easy question answered expensively costs a
    # fraction of a penny, and those are not comparable. See router.py.
    brain, brain_why, auto_used = router.pick(
        payload.get("model"), messages,
        has_image=bool(payload.get("see_screen") or payload.get("image")))

    # "deep" is the owner's alone. A guest asking for it is not an error worth
    # refusing a whole turn over — they get the ordinary brain and an answer,
    # rather than a 403 and silence. Enforced here rather than in the page,
    # because a switch in a browser is a suggestion.
    if brain == "deep" and tier(request) != "owner":
        brain, brain_why = "smart", "deep is the owner's brain"

    model = resolve_model(brain)

    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "No messages supplied.")

    # Check the shape here rather than paying Anthropic to reject it. Also
    # stops a malformed client turning into a stream of billed 400s.
    if len(messages) > 60:
        raise HTTPException(400, "Too many messages in one request.")
    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            raise HTTPException(400, "Malformed message.")
        if msg.get("role") not in ("user", "assistant"):
            raise HTTPException(400, "Each message needs a role of user or assistant.")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(400, "Each message needs text content.")
        total += len(content)
    if total > 60_000:
        raise HTTPException(400, "Conversation is too long. Clear the log and start again.")

    # This bounds what a CALLER can add, not ARC's own prompt — which is no
    # longer sent from the browser at all and so cannot be bounded here.
    #
    # It used to be both, and the number had to keep rising to accommodate the
    # rulebook itself: the HUD's own prompt was 44,282 characters before the
    # page appended persona, memory, thread digest, timers, screen, consent,
    # voice and lesson blocks, so the ceiling had reached 96,000 and a user with
    # enough accumulated memory would still have started getting rejected on
    # every single message. With the base held server-side, what arrives from
    # the browser is a few thousand characters and the ceiling can go back to
    # bounding abuse, which is its actual job.
    if not isinstance(extra, str):
        raise HTTPException(400, "System prompt is missing.")
    if len(extra) > MAX_SYSTEM_CHARS:
        print(f"{C_RED}  ! client prompt blocks {len(extra)} chars "
              f"(limit {MAX_SYSTEM_CHARS}){C_OFF}")
        raise HTTPException(400, "System prompt is too long.")

    # --- what ARC can actually do this turn -------------------------------
    # Computer control only for the local desktop, never over the tunnel.
    guest = is_guest(request)
    # A guest is never "local", whatever the socket says. Belt and braces: it
    # already takes a loopback peer with no forwarding headers to be local, but
    # this way one check decides computer control, live screen and pc tools
    # together instead of three that could drift apart.
    local = is_local_request(request) and not guest
    apply_session_google(request)   # use THIS signed-in user's own Google account
    apply_session_memory(request)   # ...and THIS user's own memory
    tools = all_tools(local, guest)
    if guest:
        # The personality prompt arrives from the client and describes the full
        # ARC. Without this the model cheerfully offers to text someone or check
        # the owner's notes, then hits a refusal it can't explain. Telling it the
        # shape of the account up front is the difference between a demo that
        # feels deliberate and one that feels broken.
        extra += (
            "\n\nGUEST ACCOUNT — this is not your owner.\n"
            "You are signed in as a guest. You have their own Google account "
            "(calendar, mail, drive, contacts), plus weather, stocks, news and "
            "web search. You do NOT have this machine, its screens, Telegram, "
            "the owner's notes, memory, todos, reminders or phone alerts, and "
            "you must not claim otherwise or offer to use them. If asked for "
            "one, say plainly that it's off on a guest account and move on. "
            "Never repeat anything you were told about the owner personally."
        )
    else:
        # What alarms are set, in the turn context. Without this "is my alarm
        # still on?" costs a tool call and a round trip, and — worse — the model
        # answering "yes, seven o'clock" from the conversation alone would be
        # guessing about the one thing you must never be wrong about.
        try:
            alarm_line = alarm.summary_line()
        except Exception:
            alarm_line = ""
        if alarm_line:
            extra += "\n\n" + alarm_line

    # What ARC knows about this person, added HERE rather than sent up by the
    # browser. Two things follow from that. It is the same memory whichever
    # device you are on — it used to be localStorage, so the phone and the
    # desktop each remembered different things and neither knew it. And a
    # client cannot invent facts about the owner by putting them in the prompt.
    #
    # Room mode is the exception: with several people in earshot ARC cannot
    # tell who is speaking, so nothing personal goes in and nothing comes out.
    if not payload.get("room") and not payload.get("no_tools"):
        try:
            extra += memory.block()
        except Exception:
            pass

    # A passive glance (watch mode) just looks and reports — it must not click,
    # type, or otherwise act on the machine. no_tools strips the toolset for it.
    if bool(payload.get("no_tools")):
        tools = []
        use_search = False
    if use_search:
        # The dated variant matters: _20260209 filters results before they hit
        # the context window. The older _20250305 has no such filtering.
        tools.append({
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 3,
            # Haiku (and any model without programmatic tool-calling) rejects a
            # server tool unless we say it's called directly by the model.
            "allowed_callers": ["direct"],
        })

    # Thinking is the single biggest source of reply latency, so for a voice
    # loop it is OFF by default and only turns on when the user explicitly asks
    # (the THINKING switch set to ON/ALWAYS). Most spoken questions don't need
    # it; the ones that do can opt in.
    #
    # thinking_for() then has the last word, because on Opus that default is not
    # safe to honour — see the note on the helper. The switch still means what it
    # says on the two brains that answer most turns.
    want_think = payload.get("think")
    thinking_on = want_think is True or str(want_think).lower() in ("on", "always", "adaptive", "true")
    thinking = thinking_for(model, thinking_on)

    convo = list(messages)

    def _attach_to_last_user(blocks):
        """Append image/text blocks to the most recent user message, whether its
        content is still a plain string or already a block list."""
        for i in range(len(convo) - 1, -1, -1):
            if convo[i].get("role") == "user":
                c = convo[i]["content"]
                base = [{"type": "text", "text": c}] if isinstance(c, str) else list(c)
                convo[i] = {"role": "user", "content": base + blocks}
                return

    # Live-screen: attach the current desktop screenshot so ARC sees what's on
    # screen right now, no tool call. Desktop-only; never written to disk.
    # Defaults to EVERY monitor separately: attaching only the primary meant
    # ARC answered confidently about the wrong screen whenever the user was
    # working on the other one, which is worse than not seeing at all.
    if see_screen and local and pc.connected():
        # A bare `true` from an older client means what it always meant: the
        # primary. Attaching every monitor is opt-in, because it costs an image
        # per screen on every single message.
        which = see_screen_raw if isinstance(see_screen_raw, str) else "primary"
        shot = pc.screenshot(which)
        if isinstance(shot, list):
            _attach_to_last_user(shot)

    # Camera: the client (phone/webcam) captured a frame and sent it as a data
    # URL. Attach it so ARC can see the real world. Works anywhere (it's the
    # user's own camera), transient — never saved.
    client_image = payload.get("image")
    if isinstance(client_image, str) and client_image.startswith("data:image") and "," in client_image:
        header, b64 = client_image.split(",", 1)
        mt = "image/png" if "png" in header else "image/jpeg"
        if 0 < len(b64) < 8_000_000:
            _attach_to_last_user([{"type": "image",
                                   "source": {"type": "base64", "media_type": mt, "data": b64}}])

    # --- the prompt itself -------------------------------------------------
    # Two blocks, and the split is doing two jobs at once.
    #
    # AUTHORITY: the server's text is first and the browser's is second, so a
    # client can add to the instructions and can never remove them. Send an
    # empty `system` from devtools and ARC is still ARC.
    #
    # COST: the first block is identical on every request — same text, same
    # tools ahead of it — so it is marked cacheable. Cache reads bill at about
    # a tenth of the input rate. That block is roughly twelve thousand tokens
    # and was being sent in full on every turn AND on each of the up-to-six
    # tool rounds inside one turn, which is where most of the money went.
    #
    # Everything volatile — persona, the date, alarms, what is on screen —
    # must stay in the SECOND block. Caching is a prefix match: one changed
    # byte in the first block and nothing after it is cached either.
    base = prompt.base(prompt_name)
    head = {"type": "text", "text": base}
    # Below roughly a thousand tokens the API will not cache a prefix at all,
    # and does not say so — it simply never reports a hit. The main prompt is
    # twelve thousand tokens and caches; the watch brief is under three hundred
    # and never would, and marking it cacheable would be a breakpoint that
    # looks like it is working and isn't. So only ask when it can be granted.
    if len(base) >= MIN_CACHE_CHARS:
        head["cache_control"] = {"type": "ephemeral"}
    system = [head]
    if extra.strip():
        system.append({"type": "text", "text": extra})

    searched = False
    used: list[str] = []
    blocked_actions: list[str] = []
    tokens_in = tokens_out = 0
    cache_read = cache_write = 0
    # Web search is billed per SEARCH as well as per token — $10 per thousand,
    # so a penny each, up to three in a turn. That is more than an entire Haiku
    # turn costs, and none of it was being counted: "what's the weather" was
    # recorded at a tenth of what it really came to. Tokens are not the only
    # thing on the bill and a meter that only knows about tokens is wrong by a
    # margin that grows with exactly the turns Auto sends to the cheap brain.
    searches = 0
    reply = ""
    claude = request.app.state.claude

    for _ in range(MAX_TOOL_ROUNDS):
        kwargs = dict(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=convo,
            thinking=thinking,
        )
        # The effort control is a Sonnet/Opus feature; Haiku rejects it. Only
        # send it on models that accept it — decided per-request, since the brain
        # can be switched at runtime.
        if supports_effort(model):
            effort = EFFORT_DEEP if brain == "deep" else EFFORT
            # Guard the one combination that is a hard 400 rather than a
            # degraded answer: thinking off above 'high' on Opus 5. Belt and
            # braces now that thinking_for() never disables thinking on Opus —
            # it costs nothing and it is the wrong thing to be relying on a
            # second function for if that one is ever changed.
            if thinking["type"] == "disabled" and effort not in _NO_THINK_MAX_EFFORT:
                effort = "high"
            kwargs["output_config"] = {"effort": effort}
        if tools:
            kwargs["tools"] = tools

        try:
            resp = await claude.messages.create(**kwargs)
        except anthropic.APIConnectionError as e:
            raise HTTPException(502, f"Could not reach the Anthropic API: {e}")
        except anthropic.RateLimitError:
            raise HTTPException(429, "Rate limited by the Anthropic API. Try again shortly.")
        except anthropic.APIStatusError as e:
            raise _claude_error(e)

        u = resp.usage
        tokens_in += getattr(u, "input_tokens", 0) or 0
        tokens_out += getattr(u, "output_tokens", 0) or 0
        # Counted apart from input_tokens, because they are not priced like
        # them: a cache read is about a tenth of the input rate and a write
        # about a quarter more. Folding them into tokens_in would make the
        # SPEND TODAY readout roughly five times too high and trip the daily
        # cap early — a spend meter that lies is worse than no spend meter.
        cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        searches += getattr(stu, "web_search_requests", 0) or 0 if stu else 0

        # Safety classifiers can decline. That arrives as a normal 200 with an
        # empty or partial body, so it has to be checked before reading content.
        if resp.stop_reason == "refusal":
            reply = "I can't help with that one, sir."
            break

        searched = searched or any(
            b.type in ("server_tool_use", "web_search_tool_result")
            for b in resp.content
        )

        # Server-side tools hit their own iteration cap and ask to be resumed.
        # Re-send with no new input; the server picks up where it stopped.
        if resp.stop_reason == "pause_turn":
            convo = convo + [{"role": "assistant", "content": resp.content}]
            continue

        calls = [b for b in resp.content if b.type == "tool_use"]
        if not calls:
            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            break

        results = []
        for call in calls:
            # Consent gate: an action tool only runs when the user authorised
            # this turn. Otherwise it's refused (not executed) and ARC is told to
            # ask first — so nothing happens to the machine without a say-so.
            if REQUIRE_CONSENT and _is_acting(call.name) and not allow_actions:
                out = ("NOT AUTHORISED. ARC is in ask-first mode and the user has not approved "
                       "any action this turn. Do NOT run this or any other action. Instead, tell "
                       "the user in one short sentence exactly what you want to do and ask them to "
                       "confirm; only act once they clearly say yes.")
                failed = True
                blocked_actions.append(call.name)
                used.append(call.name + " (needs consent)")
                print(f"{C_DIM}  {C_RED}âš‘{C_OFF} {call.name} {C_DIM}blocked — needs consent{C_OFF}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": out,
                    "is_error": True,
                })
                continue
            out, failed = dispatch_tool(call.name, dict(call.input or {}),
                                        local=local, guest=guest)
            used.append(call.name)
            mark = f"{C_RED}âœ—{C_OFF}" if failed else f"{C_CYAN}âœ“{C_OFF}"
            print(f"{C_DIM}  {mark} {call.name}{C_OFF} {C_DIM}{str(call.input)[:90]}{C_OFF}")
            # A tool can return rich content (a list of blocks, e.g. a
            # screenshot image); pass that straight through. Plain text is
            # capped so one tool can't blow the context window.
            content = out if isinstance(out, list) else out[:8000]
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": content,
                "is_error": failed,
            })

        # Every result goes back in one user turn â€” splitting them teaches the
        # model to stop asking for tools in parallel.
        convo = convo + [
            {"role": "assistant", "content": resp.content},
            {"role": "user", "content": results},
        ]
    else:
        reply = reply or "That turned into more steps than I could finish, sir."

    # Say which brain answered and why. An automatic choice that leaves no
    # trace is one nobody can check, and this one is spending money.
    if auto_used:
        print(f"{C_DIM}  · auto: {brain} — {brain_why}{C_OFF}")
    print(
        f"{C_DIM}  â€º {tokens_in} in / {tokens_out} out"
        f"{'  [searched]' if searched else ''}"
        f"{('  [' + ', '.join(used) + ']') if used else ''}{C_OFF}"
    )

    _day["tok_in"] += tokens_in
    _day["tok_out"] += tokens_out
    _day["tok_cache_read"] += cache_read
    _day["tok_cache_write"] += cache_write
    # Priced at whatever answered, not at whatever the default is.
    spent = turn_cost(model, tokens_in, tokens_out, cache_read, cache_write,
                      searches)
    _day["cost"] += spent
    # What the cache kept: the difference between what those tokens cost as
    # reads and what they would have cost at the full input rate.
    saved = cache_read / 1e6 * prices_for(model)[0] * (1 - CACHE_READ_RATE)

    # And to disk, for Arc Watch. _day is memory only and resets on restart, so
    # until this existed the honest answer to "what did I spend on Tuesday?"
    # was that nobody knew, including ARC. Counts and costs only — no prompts,
    # no replies, nothing anybody said. See stats.py.
    stats.record(
        tok_in=tokens_in, tok_out=tokens_out,
        cache_read=cache_read, cache_write=cache_write,
        cost=spent, saved=saved,
        tools=used, model=model, searched=searched,
        refusal=(reply == "I can't help with that one, sir."))

    return JSONResponse({
        "reply": reply,
        "searched": searched,
        "thought": thinking["type"] == "adaptive",
        "brain": brain,
        "auto": auto_used,
        "why": brain_why,
        "tools": used,
        "blocked": blocked_actions,   # actions ARC wanted but that need the user's ok
        "cost_today": round(_day_cost(), 4),
    })


@app.post("/api/summarize")
async def summarize(request: Request, _=Depends(require_auth)):
    """Maintain a rolling 'thread' digest — the shape of what ARC and the user
    are working through — so continuity survives long after old turns scroll out
    of the sent history. The client sends the current note plus the recent turns;
    we fold them into a fresh, compact note. Cheap: one small, tool-free call."""
    check_rate(request)
    if not ANTHROPIC_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set.")

    payload = await read_json(request)
    note = (payload.get("note") or "").strip()[:4000]
    msgs = payload.get("messages") or []
    if not isinstance(msgs, list):
        raise HTTPException(400, "messages must be a list.")

    # Flatten the recent turns into a plain transcript for the summariser.
    lines = []
    for m in msgs[-40:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        who = "User" if role == "user" else "ARC"
        lines.append(f"{who}: {content.strip()}")
    transcript = "\n".join(lines)[:16000]
    if not transcript:
        return JSONResponse({"note": note})

    sys_prompt = (
        "You maintain a running memory of an ongoing assistant<->user relationship. "
        "Given the PREVIOUS NOTE and the RECENT CONVERSATION, output an updated note that "
        "captures the SHAPE of what they are working through: the user's current goals and "
        "projects, decisions already made, open questions, preferences, and any threads left "
        "unfinished. Keep what still matters, drop what is stale, merge duplicates. "
        "Write terse bullet points, no preamble, under 1200 characters. Output ONLY the note."
    )
    user_msg = f"PREVIOUS NOTE:\n{note or '(none yet)'}\n\nRECENT CONVERSATION:\n{transcript}"

    claude = request.app.state.claude
    try:
        # Same rule as the chat route, and for the same reason — this one is
        # hardcoded to MODEL, so setting ARC_MODEL to an Opus id would otherwise
        # quietly reintroduce the leak here after it had been closed there. It
        # matters more in this route than in that one: what comes back is not
        # spoken and forgotten, it is WRITTEN DOWN and read again every session.
        think = thinking_for(MODEL, False)
        resp = await claude.messages.create(
            model=MODEL,
            # Thinking is spent from the same ceiling as the note itself, so 500
            # would be swallowed whole and the note would come back truncated or
            # empty — and an empty note overwrites a good one.
            max_tokens=500 if think["type"] == "disabled" else 4000,
            system=sys_prompt,
            messages=[{"role": "user", "content": user_msg}],
            thinking=think,
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(502, f"Could not reach the Anthropic API: {e}")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Rate limited by the Anthropic API.")
    except anthropic.APIStatusError as e:
        raise _claude_error(e)

    u = resp.usage
    s_in = getattr(u, "input_tokens", 0) or 0
    s_out = getattr(u, "output_tokens", 0) or 0
    _day["tok_in"] += s_in
    _day["tok_out"] += s_out
    # Small, but it is real money and it is spent without anybody asking for
    # it. Now that the day's cost is accumulated rather than derived from the
    # token totals, a call that adds tokens and no cost is a call that spends
    # invisibly and never counts towards the cap.
    _day["cost"] += turn_cost(MODEL, s_in, s_out)

    new_note = " ".join(b.text for b in resp.content if b.type == "text").strip()[:1600]
    return JSONResponse({"note": new_note or note, "cost_today": round(_day_cost(), 4)})


async def _eleven_tts(http, text: str, lang: str = ""):
    """ElevenLabs audio, or None if it's not configured / out of quota / errors.
    Kept as a nicety: if the key has credit its voice is used; the moment it
    doesn't, we fall through to the free Edge voice instead of going silent.

    The turbo model is multilingual, but one ElevenLabs voice is one accent —
    it will read Tamil in an English mouth. So when a language is set and it is
    not the voice's own, this stands aside and lets Edge answer, where there is
    a native voice for the language. Better a different voice than a wrong one.
    """
    if not (ELEVEN_KEY and ELEVEN_VOICE):
        return None
    base = (lang or "").split("-")[0].lower()
    if base and base != ELEVEN_LANG:
        return None
    try:
        r = await http.post(
            f"{ELEVEN_URL}/{ELEVEN_VOICE}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": ELEVEN_KEY, "content-type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",   # multilingual: ~32 languages
                "language_code": base or None,
                "voice_settings": {
                    "stability": 0.45, "similarity_boost": 0.8,
                    "style": 0.15, "use_speaker_boost": True,
                },
            },
        )
    except httpx.RequestError:
        return None
    # Quota exhausted (401/402) or any other failure → let Edge take over.
    if r.status_code != 200 or not r.content:
        return None
    return r.content


async def _edge_tts(text: str, voice: str = "", lang: str = ""):
    """Microsoft Edge neural voice. Free, no key, no quota — the default.

    The voice is still whitelisted, but the whitelist is now everything
    Microsoft actually publishes (see voices.py) rather than eight English
    names — so ARC can answer in any of a hundred-odd languages, and a client
    still cannot pass an arbitrary string.

    With no voice but a language, the language decides. That is what makes
    replying in Tamil sound like Tamil rather than an English voice reading
    Tamil letters aloud.
    """
    v = voice if voices.is_valid(voice) else ""
    if not v and lang and not voices.same_language(lang, TTS_VOICE):
        # Only go to the catalogue for a language ARC's OWN voice cannot speak.
        # Asking it for English handed back whichever female en-GB voice sorted
        # first alphabetically — Libby, with Maisie (Microsoft's British CHILD
        # voice) next in line — so adding a hundred languages quietly replaced
        # Bella's voice with a child's. ARC_TTS_VOICE is the configured voice
        # and it wins for its own language, every time.
        v = voices.for_lang(lang)
    if not v:
        v = TTS_VOICE
    comm = edge_tts.Communicate(text, v)
    audio = bytearray()
    async for chunk in comm.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            audio += chunk["data"]
    return bytes(audio)


@app.post("/api/tts")
async def tts(request: Request, _=Depends(require_auth)):
    """Return spoken audio so ARC has a natural voice instead of the browser's
    robot one. Uses ElevenLabs when its key has credit, otherwise the free Edge
    neural voice. Always available — no key required."""
    payload = await read_json(request)
    text = (payload.get("text") or "").strip()
    voice = (payload.get("voice") or "").strip()
    lang = (payload.get("lang") or "").strip()
    if not text:
        raise HTTPException(400, "No text supplied.")
    if len(text) > 5000:
        raise HTTPException(400, "Too much text for one utterance.")

    # Edge is the default: free, and ~0.5s vs a failed ElevenLabs round-trip
    # that adds 2-3s of pure latency once its quota is spent. Only try
    # ElevenLabs first if explicitly preferred (ARC_PREFER_ELEVEN) and it still
    # has credit; otherwise go straight to Edge (honouring the picked voice).
    audio = None
    if PREFER_ELEVEN:
        audio = await _eleven_tts(request.app.state.http, text, lang)
    if audio is None:
        try:
            audio = await _edge_tts(text, voice, lang)
        except Exception as e:
            raise HTTPException(502, f"Text-to-speech failed: {str(e)[:200]}")
    if not audio:
        raise HTTPException(502, "Text-to-speech produced no audio.")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/voices")
async def voices_list(request: Request, _=Depends(require_auth)):
    """Every language ARC can hear and speak, and the voices in each.

    Served rather than hard-coded in the page so the picker can only ever offer
    what will actually work — and so a new neural voice appears in ARC without
    anyone editing a list.
    """
    locale = (request.query_params.get("locale") or "").strip()
    if locale:
        return JSONResponse({"locale": locale,
                             "voices": voices.voices_for(locale)})
    return JSONResponse({"languages": voices.languages(),
                         "default": TTS_VOICE})


@app.get("/api/stocks")
async def stocks(request: Request, _=Depends(require_auth)):
    """Proxy for the stock-watcher widget — the browser can't call Yahoo
    directly (CORS), so the server fetches the quotes and hands them back."""
    raw = (request.query_params.get("symbols") or "").split(",")
    syms = [s.strip().upper() for s in raw if s.strip()][:12]

    def _fetch():
        out = []
        for s in syms:
            try:
                q = extras.yahoo_quote(s)
                if q:
                    out.append(q)
            except Exception:
                pass
        return out

    try:
        import anyio
        quotes = await anyio.to_thread.run_sync(_fetch)
    except Exception:
        quotes = []
    return JSONResponse({"quotes": quotes})


@app.get("/api/stock-search")
async def stock_search(request: Request, _=Depends(require_auth)):
    """Resolve a typed name/ticker to a real symbol for the widget's editor,
    so users can type "apple" or "bitcoin" instead of the exact ticker."""
    q = (request.query_params.get("q") or "").strip()
    if not q:
        return JSONResponse({"symbol": None})

    def _resolve():
        try:
            return extras.yahoo_search(q)
        except Exception:
            return None

    try:
        import anyio
        sym = await anyio.to_thread.run_sync(_resolve)
    except Exception:
        sym = None
    return JSONResponse({"symbol": sym})


@app.get("/api/reminders/due")
async def reminders_due(request: Request, _=Depends(require_auth)):
    """The client polls this; any reminder whose time has come is returned once
    (then marked delivered) so ARC can announce it — even after a reload."""
    # Returned ONCE: a guest polling this wouldn't merely see the owner's
    # reminders, it would consume them, and the owner would never be told.
    deny_guest(request)
    try:
        return JSONResponse({"due": extras.due_reminders()})
    except Exception:
        return JSONResponse({"due": []})


@app.get("/api/alerts/due")
async def alerts_due(request: Request, _=Depends(require_auth)):
    """The client polls this; any price alert that has just crossed is returned
    once (then marked delivered) so ARC can speak it — even with no phone set up.
    Cheap: the network fetch happens on the server's monitor loop, not here."""
    # Same one-shot delivery as reminders — a guest polling it eats the owner's.
    deny_guest(request)
    try:
        return JSONResponse({"due": alerts.pending_browser()})
    except Exception:
        return JSONResponse({"due": []})


@app.get("/api/alarms/due")
async def alarms_due(request: Request, _=Depends(require_auth)):
    """Whatever is ringing right now, so the page can make a noise about it.

    Note what this does NOT do: consume. Reminders and price alerts are handed
    over once and marked delivered, because they are messages. An alarm is a
    bell — it keeps being reported until somebody stops it, so a reload (or a
    second device) doesn't silence one. The alarm's own stop_at ends it if
    nobody ever does."""
    deny_guest(request)
    try:
        return JSONResponse({"ringing": alarm.ringing(), "next": alarm.next_up()})
    except Exception:
        return JSONResponse({"ringing": [], "next": None})


@app.post("/api/alarms/snooze")
async def alarms_snooze(request: Request, _=Depends(require_auth)):
    """The Snooze button. Voice has snooze_alarm; this is for a hand at 7am
    that would rather press something than form a sentence."""
    deny_guest(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    try:
        mins = int(float(body.get("minutes") or alarm.DEFAULT_SNOOZE_MIN))
    except (TypeError, ValueError):
        mins = alarm.DEFAULT_SNOOZE_MIN
    return JSONResponse({"stopped": alarm.stop(snooze_minutes=max(1, min(120, mins))),
                         "minutes": mins})


@app.post("/api/alarms/dismiss")
async def alarms_dismiss(request: Request, _=Depends(require_auth)):
    """The Stop button."""
    deny_guest(request)
    return JSONResponse({"stopped": alarm.stop()})


@app.get("/api/automation/status")
async def automation_status_route(request: Request, _=Depends(require_auth)):
    """What the auto-clicker is doing, for the button to reflect."""
    deny_guest(request)
    return JSONResponse({"running": automation.running(),
                         "status": automation.automation_status()})


@app.post("/api/automation/stop")
async def automation_stop_route(request: Request, _=Depends(require_auth)):
    """Stop whatever is repeating.

    Deliberately NOT gated on the request being local. Everything that STARTS
    input automation is desktop-only, but stopping it must work from wherever
    you happen to be holding a screen — the whole point of a stop is that it is
    never the thing that fails.
    """
    deny_guest(request)
    return JSONResponse({"stopped": automation.stop_automation()})


@app.post("/api/automation/click")
async def automation_click_route(request: Request, _=Depends(require_auth)):
    """The Start clicking button.

    A direct route rather than a trip through the model: the pointer is already
    where the user wants it, and a round trip through Claude to press a button
    they have already pressed would be both slower and less predictable. The
    same bounds apply either way — automation.py caps the rate and the
    duration, so nothing here can ask for more than the tool allows.
    """
    deny_guest(request)
    if not is_local_request(request):
        raise HTTPException(403, "Auto-clicking only works from the desktop app.")
    payload = await request.json() if await request.body() else {}
    return JSONResponse({"status": automation.auto_click(
        rate=payload.get("rate", 5), seconds=payload.get("seconds", 120))})


@app.post("/api/chat/remember")
async def memory_remember(request: Request, _=Depends(require_auth)):
    """Store one fact ARC asked to keep, via the [[remember: …]] directive.

    A route rather than a tool because the model emits the directive at the end
    of an ordinary reply — it is not deciding to call something, it is finishing
    a sentence — and the client strips it before speaking either way.
    """
    apply_session_memory(request)
    payload = await read_json(request)
    return JSONResponse({"said": memory.remember(payload.get("fact") or ""),
                         "count": memory.count()})


@app.get("/api/memory")
async def memory_list(request: Request, _=Depends(require_auth)):
    """What ARC knows about whoever is asking — never about anyone else."""
    apply_session_memory(request)
    return JSONResponse({"facts": memory.facts(), "count": memory.count(),
                         "max": memory.MAX_FACTS})


@app.post("/api/memory/forget")
async def memory_forget(request: Request, _=Depends(require_auth)):
    apply_session_memory(request)
    payload = await read_json(request)
    return JSONResponse({"said": memory.forget(payload.get("which") or "")})


@app.post("/api/memory/import")
async def memory_import(request: Request, _=Depends(require_auth)):
    """Take over what a browser had in localStorage — once, ever.

    Memory used to live in the browser, so an established user has months of it
    sitting in one device's storage. Moving the store without this would look
    exactly like ARC forgetting everything about them.

    Refused once this account has any memory at all, which is what makes it
    once: a second device with older, staler facts cannot come along later and
    push them back in on top of the current ones.
    """
    apply_session_memory(request)
    if memory.count():
        return JSONResponse({"imported": 0, "why": "already has memory"})
    payload = await read_json(request)
    items = payload.get("facts") or []
    if not isinstance(items, list):
        raise HTTPException(400, "facts must be a list.")
    n = memory.import_facts(items[:memory.MAX_FACTS])
    if n:
        print(f"{C_DIM}  · took over {n} remembered fact"
              f"{'s' if n != 1 else ''} from a browser{C_OFF}")
    return JSONResponse({"imported": n})


@app.get("/api/usage")
async def usage_route(request: Request, _=Depends(require_auth)):
    """Everything Arc Watch draws. Figures only — never conversations."""
    deny_guest(request)
    days = max(1, min(400, int(request.query_params.get("days") or 30)))
    return JSONResponse({
        "today": stats.day(),
        "series": stats.series(days),
        "totals": stats.totals(days),
        "summary": stats.summary(min(days, 30)),
        # The default model's rates, kept for the readout, plus the whole
        # table — with more than one brain answering, a single pair no longer
        # explains a day's bill on its own.
        "prices": {"in": PRICE_IN, "out": PRICE_OUT,
                   "cache_read": PRICE_IN * CACHE_READ_RATE,
                   "cache_write": PRICE_IN * CACHE_WRITE_RATE,
                   "per_model": {k: {"in": v[0], "out": v[1]}
                                 for k, v in PRICES.items()}},
        "cap": DAILY_COST_CAP,
        "model": MODEL,
    })


@app.get("/api/triggers")
async def triggers_list(request: Request, _=Depends(require_auth)):
    deny_guest(request)
    return JSONResponse({"rules": triggers._load(),
                         "text": triggers.list_triggers()})


@app.get("/api/triggers/due")
async def triggers_due(request: Request, _=Depends(require_auth)):
    """Rules that fired since the browser last asked, so it can speak them."""
    deny_guest(request)
    return JSONResponse({"due": triggers.due()})


@app.get("/watch")
async def arc_watch(request: Request, _=Depends(require_auth)):
    """Arc Watch — what ARC has cost and done, as a page.

    Its own screen rather than another panel in the HUD, for the same reason
    /display is: this is something you leave open on a second monitor and
    glance at, not something you open mid-conversation.
    """
    deny_guest(request)
    return FileResponse(ROOT / "static" / "watch.html")


@app.post("/api/export")
async def export_route(request: Request, _=Depends(require_auth)):
    """Write everything of the owner's into one portable file.

    Guests are refused: this exports the owner's notes, alarms and memory, and
    a visitor writing a copy of all of it to disk is not a thing to allow.
    """
    deny_guest(request)
    payload = await request.json() if await request.body() else {}
    said = await asyncio.to_thread(selfheal.export_all, payload.get("path") or "")
    return JSONResponse({"said": said})


@app.get("/api/selfcheck")
async def selfcheck_route(request: Request, _=Depends(require_auth)):
    """What ARC thinks is wrong with ARC.

    A route as well as a tool, because the moment you most want this is the
    moment the model cannot answer — no credit, no key, a provider outage. A
    button that works when the brain does not is worth more here than tidiness.
    """
    deny_guest(request)
    # Point Google at THIS browser's token before asking whether Google is
    # connected, the same as /api/health. Without it the report describes
    # whichever account the process last touched, and the one person it would
    # mislead is a signed-in guest being told their own mail is fine.
    apply_session_google(request)
    findings = await asyncio.to_thread(selfheal.check)
    return JSONResponse({
        "findings": findings,
        "summary": selfheal.describe(findings),
        "fixable": sorted({f["fix"] for f in findings if f["fix"] and not f["ok"]}),
        "broken": sum(1 for f in findings if f["level"] == "broken"),
        "warnings": sum(1 for f in findings if f["level"] == "warn"),
        "recent": selfheal.history(10),
    })


@app.post("/api/selfrepair")
async def selfrepair_route(request: Request, _=Depends(require_auth)):
    """Fix what can be fixed. Never code, never secrets, always a copy first.

    Guests are refused rather than merely limited: this rewrites the owner's
    notes and alarms from backup and restarts the owner's background loop, and
    none of that is a visitor's to decide.
    """
    deny_guest(request)
    apply_session_google(request)
    payload = await request.json() if await request.body() else {}
    want = payload.get("what") or ""
    ids = [k for k in selfheal.REPAIRS if k in want.lower()] if want.strip() else None

    # In a worker thread, because one of these repairs is not quick: refetching
    # the voice catalogue waits up to twenty seconds on a network that may not
    # answer. Run inline, that is twenty seconds during which the server cannot
    # serve anybody — including the alarm poll. A repair must not become an
    # outage. Safe to move off the loop now that restart_monitor schedules its
    # task rather than creating one wherever it happens to be called.
    def work():
        findings = selfheal.check()
        done = selfheal.repair(ids, findings=findings)
        return done, selfheal.check()

    done, after = await asyncio.to_thread(work)
    return JSONResponse({
        "done": done,
        "findings": after,
        "summary": selfheal.describe(after),
        "still_broken": [f["what"] for f in after if f["level"] == "broken"],
    })


@app.get("/api/push/status")
async def push_status(request: Request, _=Depends(require_auth)):
    """Whether phone push (ntfy) is set up on this server, for the UI to reflect."""
    return JSONResponse({"configured": push.configured()})


@app.post("/api/push/test")
async def push_test(request: Request, _=Depends(require_auth)):
    """Send a test notification to the owner's phone."""
    deny_guest(request)   # it is the OWNER's phone, whoever pressed the button
    if not push.configured():
        return JSONResponse({"ok": False, "reason": "not configured"})
    try:
        import anyio
        ok = await anyio.to_thread.run_sync(
            lambda: push.send("Phone alerts are working. I'll ping you here when a reminder fires.",
                              title="ARC test", tags="white_check_mark"))
    except Exception:
        ok = False
    return JSONResponse({"ok": bool(ok)})


@app.get("/api/calendar/upcoming")
async def calendar_upcoming(request: Request, _=Depends(require_auth)):
    """Timed events starting within ?lead minutes, for the meeting-nudge poller.
    Uses THIS user's own calendar; the client de-dupes what it has announced."""
    apply_session_google(request)
    if not gcal.connected():
        return JSONResponse({"events": []})
    try:
        lead = int(request.query_params.get("lead", "10"))
    except Exception:
        lead = 10
    try:
        import anyio
        events = await anyio.to_thread.run_sync(lambda: gcal.upcoming_events(lead))
    except Exception:
        events = []
    return JSONResponse({"events": events})


@app.post("/api/duck")
async def duck(request: Request, _=Depends(require_auth)):
    """Auto-ducking: the client calls this to lower system volume while ARC is
    listening/replying (so the mic hears over media) and to restore it after.
    Desktop-only — a remote/tunnel caller can't touch this machine's volume."""
    if not is_local_request(request):
        return JSONResponse({"ok": False, "reason": "remote"})
    payload = await read_json(request)
    on = bool(payload.get("on"))
    try:
        # pycaw is a blocking COM call; keep it off the event loop.
        import anyio
        state = await anyio.to_thread.run_sync(lambda: pc.duck(on))
    except Exception as e:
        state = f"error: {e}"
    return JSONResponse({"ok": True, "state": state})


@app.post("/api/screen-watch")
async def screen_watch(request: Request, _=Depends(require_auth)):
    """Watch mode's cheap gate: report whether the screen has changed since the
    last check (local pixel diff, no model). The client polls this and only asks
    ARC to actually look when something moved. Desktop-only — the server grabs
    THIS machine's screen, which a remote caller must never trigger."""
    if not is_local_request(request):
        return JSONResponse({"ok": False, "reason": "remote"})
    payload = await read_json(request)
    if payload.get("reset"):
        try:
            import anyio
            await anyio.to_thread.run_sync(pc.reset_watch)
        except Exception:
            pass
        return JSONResponse({"ok": True, "changed": False, "score": 0.0})
    # Which screens to gate on — must be the same set the glance will be shown,
    # or watch mode wakes for changes it then cannot see.
    which = payload.get("monitor")
    which = which if isinstance(which, str) else "each"
    try:
        import anyio
        res = await anyio.to_thread.run_sync(lambda: pc.screen_changed(monitor=which))
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)})
    return JSONResponse({"ok": True, **res})


# --- sign-in (Google OAuth) ------------------------------------------------
#
# One round trip does both jobs: it proves who you are (the ID token, checked
# against ARC_ALLOWED_EMAILS) and it links the Google account the calendar and
# mail tools then use. There is no separate "log in" and "connect Google" any
# more, because they were always the same act.
#
# The in-flight state — CSRF state, OIDC nonce, PKCE verifier — rides in a
# short-lived signed cookie rather than server memory, so a sign-in survives
# the app restarting mid-flow and nothing accumulates for abandoned attempts.

def _pack_oauth_state(state: str, nonce: str, verifier: str) -> str:
    raw = f"{int(time.time())}:{state}:{nonce}:{verifier}"
    return f"{raw}.{_sign(raw)}"


def _unpack_oauth_state(cookie: str):
    if not cookie or "." not in cookie:
        return None
    raw, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(raw)):
        return None
    parts = raw.split(":", 3)
    if len(parts) != 4:
        return None
    issued, state, nonce, verifier = parts
    try:
        if time.time() - int(issued) > OAUTH_WINDOW:
            return None
    except ValueError:
        return None
    return {"state": state, "nonce": nonce, "verifier": verifier}


def _auth_fail(request: Request, reason: str, log: str = ""):
    """Every failed sign-in leaves by this door: counted, logged once, and told
    to the user only as much as is useful."""
    note_login_failure(request)
    if log:
        print(f"{C_RED}  ! sign-in refused ({reason}): {log[:200]}{C_OFF}")
    resp = RedirectResponse(f"/?auth={reason}", status_code=302)
    resp.delete_cookie(OAUTH_COOKIE, path="/")
    return resp


@app.get("/auth/login")
async def auth_login(request: Request):
    """Start the Google flow. Public by necessity — this is the front door.

    It runs in open mode too. There it grants nothing (access is already
    ungated) and only links the Google account, which is the difference
    between an instance with a calendar and one without.
    """
    check_login_rate(request)
    if not gauth.web_available():
        return RedirectResponse("/?auth=setup", status_code=302)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)        # PKCE, 43-128 chars
    redirect_uri = public_base_url(request) + "/oauth/callback"
    try:
        url = gauth.web_auth_url(redirect_uri, state=state, nonce=nonce,
                                 code_verifier=verifier)
    except Exception as e:
        return _auth_fail(request, "error", f"could not build auth url: {e}")

    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(OAUTH_COOKIE, _pack_oauth_state(state, nonce, verifier),
                    max_age=OAUTH_WINDOW, httponly=True,
                    secure=cookie_secure(request), samesite="lax", path="/")
    return resp


@app.get("/oauth/callback")
async def oauth_callback(request: Request):
    """Google returns the user here. This is where authentication actually
    happens, so it is public — requiring a session to reach the route that
    creates one would be a closed loop."""
    check_login_rate(request)

    if request.query_params.get("error"):
        return _auth_fail(request, "denied")

    pending = _unpack_oauth_state(request.cookies.get(OAUTH_COOKIE, ""))
    if not pending:
        return _auth_fail(request, "expired")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state or not hmac.compare_digest(state, pending["state"]):
        return _auth_fail(request, "error", "state mismatch")

    redirect_uri = public_base_url(request) + "/oauth/callback"
    try:
        import anyio
        token_json, id_token = await anyio.to_thread.run_sync(
            lambda: gauth.web_exchange(redirect_uri, code, pending["verifier"]))
    except Exception as e:
        return _auth_fail(request, "error", f"token exchange failed: {e}")

    # Who Google says this is. Verified against Google's keys, our client id,
    # and the nonce we minted — not read out of the token unchecked.
    try:
        claims = await anyio.to_thread.run_sync(
            lambda: gauth.verify_id_token(id_token, pending["nonce"]))
    except Exception as e:
        return _auth_fail(request, "error", f"id token rejected: {e}")

    email = (claims.get("email") or "").strip().lower()

    # Google authenticating somebody is not authorisation. This is the line.
    if email not in ALLOWED_EMAILS:
        return _auth_fail(request, "notyou", f"{email} is not on the allowlist")

    # Open mode has no sessions to create — this was only ever about linking the
    # account, so the token goes to the instance's own token.json and that is
    # the whole transaction.
    if AUTH_MODE == "open":
        try:
            gauth.TOKEN.write_text(token_json, encoding="utf-8")
        except Exception as e:
            return _auth_fail(request, "error", f"could not store google token: {e}")
        clear_login_failures(request)
        print(f"{C_CYAN}  · google linked: {email}{C_OFF}")
        resp = RedirectResponse("/?google=connected", status_code=302)
        resp.delete_cookie(OAUTH_COOKIE, path="/")
        return resp

    sid = session.create(email, request.headers.get("user-agent", ""))
    try:
        google_path(sid).write_text(token_json, encoding="utf-8")
    except Exception as e:
        session.revoke(sid)
        return _auth_fail(request, "error", f"could not store google token: {e}")

    clear_login_failures(request)
    print(f"{C_CYAN}  · signed in: {email}{C_OFF}")
    resp = RedirectResponse("/", status_code=302)
    set_session_cookie(resp, sid, request, email)
    resp.delete_cookie(OAUTH_COOKIE, path="/")
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    """Ends the session on the server, not merely in this browser."""
    sid = request.cookies.get(COOKIE, "")
    if sid:
        session.revoke(sid)      # also deletes the session's Google token
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.post("/api/leave")
async def leave(request: Request, _=Depends(require_auth)):
    """The page is closing. Sent with navigator.sendBeacon on pagehide, which
    survives the tab going away when a normal fetch would be cancelled.

    Starts the departure countdown rather than revoking outright — a refresh
    fires pagehide too, and logging someone out for pressing F5 would be its own
    kind of broken. Coming back within the grace window cancels it.
    """
    if AUTH_MODE == "open":
        return JSONResponse({"ok": True})
    session.mark_left(request.cookies.get(COOKIE, ""))
    return JSONResponse({"ok": True})


@app.get("/api/session")
async def session_info(request: Request, _=Depends(require_auth)):
    """Lets the HUD warn before the absolute cap lands mid-conversation."""
    if AUTH_MODE == "open":
        return JSONResponse({"email": "local", "auth_mode": "open",
                             "tier": "owner"})
    info = session.describe(request.cookies.get(COOKIE, "")) or {}
    return JSONResponse({**info, "auth_mode": AUTH_MODE,
                         "guest": is_guest(request), "tier": tier(request)})


@app.get("/oauth/google/status")
async def google_status(request: Request, _=Depends(require_auth)):
    """What Google account this instance carries, for the UI."""
    sid = apply_session_google(request)   # points gauth at the right token
    email = ""

    if AUTH_MODE == "open":
        # No sessions here, so the account is the instance's own token.json —
        # linked either through /auth/login or `python gauth.py`.
        connected = gauth.TOKEN.exists()
        if connected:
            try:
                import anyio
                email = await anyio.to_thread.run_sync(
                    lambda: gauth.account_email(gauth.TOKEN))
            except Exception:
                email = ""
    else:
        p = google_path(sid) if sid else None
        connected = bool(p and p.exists())
        # Straight from the verified ID token at sign-in — no API round trip.
        email = (current_session(request) or {}).get("email", "") if connected else ""

    return JSONResponse({
        "web_available": gauth.web_available(),
        "connected": connected,
        "email": email,
        "auth_mode": AUTH_MODE,
        "calendar": gcal.connected(),
        "gmail": gmail.connected(),
        "contacts_drive": gextra.connected(),
    })


@app.post("/oauth/google/disconnect")
async def google_disconnect(request: Request):
    """In google mode the account IS the session, so unlinking means signing
    out. In open mode there is no session — only the link to drop."""
    if AUTH_MODE == "open":
        try:
            gauth.TOKEN.unlink(missing_ok=True)
        except Exception:
            pass
        return JSONResponse({"ok": True, "signed_out": False})

    sid = request.cookies.get(COOKIE, "")
    if sid:
        session.revoke(sid)
    resp = JSONResponse({"ok": True, "signed_out": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Central auth gate.

    Per-route Depends(require_auth) protects the API, but the StaticFiles mount
    serves files BEFORE any route dependency runs â€” which is how
    /static/INDEX.HTML and /static/./index.html handed out the whole app with
    no session. Gate every path here, so the mount (and any asset added later)
    is covered no matter how the URL is cased or dotted. No-op when no password
    is set (local use), since authed() is then true for everyone.
    """
    if AUTH_MODE != "open" and not authed(request):
        path = request.url.path
        # PWA metadata and icons are not sensitive and must be fetchable so the
        # app stays installable and shows its icon even at the login screen.
        public = (
            path in ("/api/logout", "/favicon.ico",
                     "/sw.js", "/manifest.webmanifest")
            # The sign-in round trip itself. /oauth/callback is where a session
            # is created, so gating it on having one would be a closed loop.
            or path in ("/auth/login", "/oauth/callback")
            # Homepage, privacy policy and terms must be reachable WITHOUT
            # signing in — Google's OAuth verification crawler fetches them and
            # they are the public face of the app.
            or path in ("/home", "/privacy", "/terms")
            or path.startswith("/static/icon")
            or path in ("/static/arc-logo.svg", "/static/arc.ico")
        )
        if not public:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(LOGIN_HTML, status_code=401)
            return JSONResponse({"detail": "Not signed in."}, status_code=401)
    return await call_next(request)


# --------------------------------------------------------------------------
# security headers
# --------------------------------------------------------------------------
# Derived from what the page actually loads rather than copied from a checklist:
# Google Fonts for the two typefaces, and two public APIs the browser calls
# directly — reverse geocoding and the weather forecast. Everything else is
# same-origin by design, which is the whole reason the proxy exists.
#
# 'unsafe-inline' for scripts is not a concession, it is the architecture: the
# HUD is one file with a quarter of a megabyte of inline JavaScript and no
# build step. What the policy still buys with it there: no script may be LOADED
# from anywhere else, no data may be SENT anywhere else, the page cannot be
# framed, and a prompt injection that talks ARC into emitting a tracking pixel
# or a beacon has nowhere to send it. There are no <img> tags anywhere in ARC —
# the interface is CSS gradients and SVG — so img-src can stay shut.
#
# tests/test_headers.py reads the external origins back out of index.html and
# fails if one of them is not allowed here, so adding an API to the page and
# forgetting this file is a failing test rather than a silent dead feature.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob:",
    "media-src 'self' data: blob:",
    "connect-src 'self' https://api.open-meteo.com https://api.bigdatacloud.net",
    "worker-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "object-src 'none'",
])

# Only features ARC does not use. Microphone, camera, geolocation and autoplay
# are deliberately absent: listing a feature restricts it, and getting that
# wrong here would take the microphone out with no error anyone could read.
PERMISSIONS_POLICY = ("payment=(), usb=(), serial=(), bluetooth=(), midi=(), "
                      "xr-spatial-tracking=(), local-fonts=()")

SECURITY_HEADERS = os.getenv("ARC_SECURITY_HEADERS", "1").strip().lower() \
    not in ("0", "false", "no", "off")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Defined after require_login so it wraps it, and a 401 is protected too.

    If something here ever breaks the page, ARC_SECURITY_HEADERS=0 turns the
    lot off in one line rather than leaving anyone editing a policy string
    under pressure.
    """
    resp = await call_next(request)
    if not SECURITY_HEADERS:
        return resp
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")           # for anything pre-CSP
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
    # Only over HTTPS. Sent on a plain-HTTP localhost it is ignored at best,
    # and at worst pins a browser to a scheme the desktop instance never uses.
    if cookie_secure(request):
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp


@app.get("/sw.js")
async def service_worker():
    # Served from root, not /static, so the worker's scope covers the whole app.
    return FileResponse(ROOT / "static" / "sw.js", media_type="application/javascript")


# The private Bella serves a light-blue-on-black icon set and its own app name,
# so when installed it sits on the taskbar / home screen as a separate app. These
# explicit routes are defined BEFORE the /static mount, so they take precedence
# over the files of the same path for the private instance only.
_STATIC = ROOT / "static"


@app.get("/manifest.webmanifest")
async def manifest():
    if PRIVATE_APP:
        body = {
            "name": "Bella — Private",
            "short_name": "Bella",
            "description": "Your own private Bella.",
            "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#000000", "theme_color": "#000000",
            "icons": [
                {"src": "/static/icon-blue-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icon-blue-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/static/icon-blue-maskable.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        }
        return JSONResponse(body, media_type="application/manifest+json")
    return FileResponse(_STATIC / "manifest.webmanifest",
                        media_type="application/manifest+json")


# When private, the tab / app icon is the blue-on-black logo; otherwise the
# normal one falls through to the static mount.
def _icon(private_name: str, normal_name: str, media: str):
    name = private_name if PRIVATE_APP else normal_name
    return FileResponse(_STATIC / name, media_type=media)


@app.get("/favicon.ico")
async def favicon():
    return _icon("arc-logo-blue.svg", "arc-logo.svg", "image/svg+xml")


@app.get("/static/arc-logo.svg")
async def logo_svg():
    return _icon("arc-logo-blue.svg", "arc-logo.svg", "image/svg+xml")


@app.get("/static/icon-192.png")
async def icon_192():
    return _icon("icon-blue-192.png", "icon-192.png", "image/png")


@app.get("/static/icon-512.png")
async def icon_512():
    return _icon("icon-blue-512.png", "icon-512.png", "image/png")


@app.get("/static/icon-maskable.png")
async def icon_maskable():
    return _icon("icon-blue-maskable.png", "icon-maskable.png", "image/png")


# ARC changes often and is served from your own machine — a stale cached page
# on a phone is worse than a fresh fetch every time. Force revalidation so a
# reload always lands the newest code.
_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache", "Expires": "0"}


@app.get("/")
async def index(request: Request):
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return FileResponse(ROOT / "static" / "index.html", headers=_NO_CACHE)


@app.get("/home")
async def home_page():
    """Public homepage — describes ARC. Reachable without signing in so
    Google's OAuth verification can crawl it."""
    return FileResponse(ROOT / "static" / "home.html", headers=_NO_CACHE)


@app.get("/privacy")
async def privacy_page():
    """Public privacy policy — required for Google OAuth verification."""
    return FileResponse(ROOT / "static" / "privacy.html", headers=_NO_CACHE)


@app.get("/terms")
async def terms_page():
    """Public terms of service — linked from the consent screen."""
    return FileResponse(ROOT / "static" / "terms.html", headers=_NO_CACHE)


@app.get("/display")
async def display_page(request: Request):
    """The second-screen dashboard. Open it and drag it to your second monitor."""
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return HTMLResponse(DISPLAY_HTML, headers=_NO_CACHE)


@app.get("/api/display")
async def api_display(request: Request, _=Depends(require_auth)):
    """Current second-screen content (the /display page polls this)."""
    deny_guest(request)   # whatever the owner has up on their own wall screen
    return JSONResponse(display.get_board())


@app.get("/static/index.html")
async def index_direct(request: Request):
    """Same gate on the direct path, so it can't be walked around."""
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return FileResponse(ROOT / "static" / "index.html", headers=_NO_CACHE)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# --------------------------------------------------------------------------
# launcher
# --------------------------------------------------------------------------

def port_free(port: int) -> bool:
    # Always probe loopback â€” the server is reachable there whatever it binds
    # to, and connecting to 0.0.0.0 as a client is invalid on Windows.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


# Where Bella's window keeps its own browser profile. A dedicated profile means
# it's a separate window from your everyday browsing, it remembers the
# microphone permission between launches, and it carries its own taskbar entry
# â€” so it reads as its own app, not a tab.
WINDOW_PROFILE = DATA_DIR / ".arc-window"


def _find_browser() -> str:
    """Chrome or Edge, whichever is present. Edge ships with Windows 11, so on
    a stock machine this always finds something."""
    import shutil
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.isfile(p):
            return p
    return shutil.which("chrome") or shutil.which("msedge") or ""


def open_window(port: int):
    """Open Bella as its own chromeless app window. Chromium '--app' mode keeps
    real speech recognition working (a native webview wrapper would not), while
    dropping the address bar, tabs and menus so it looks and behaves like a
    standalone app. Falls back to a normal tab if no Chromium browser is found
    or the launch fails."""
    url = f"http://localhost:{port}"
    exe = _find_browser()
    if not exe:
        print(f"{C_DIM}  no Chrome/Edge found â€” opening in your default browser{C_OFF}")
        webbrowser.open(url)
        return
    try:
        subprocess.Popen([
            exe,
            f"--app={url}",
            f"--user-data-dir={WINDOW_PROFILE}",
            "--window-size=1180,820",
            "--no-first-run",
            "--no-default-browser-check",
        ])
    except Exception as e:
        print(f"{C_DIM}  window launch failed ({e}) â€” opening a normal tab{C_OFF}")
        webbrowser.open(url)


def auth_preflight() -> bool:
    """Refuse to serve rather than fall open.

    An assistant that can read your mail and run your shell must never come up
    ungated because a file was missing. Every failure here is fatal, and says
    exactly what to do about it.
    """
    if AUTH_MODE == "open":
        if HOST not in ("127.0.0.1", "::1", "localhost"):
            print(f"\n{C_RED}  ARC_AUTH_MODE=open is only allowed on a loopback bind.{C_OFF}")
            print(f"  {C_DIM}This instance binds {HOST}, which would hand your mail,{C_OFF}")
            print(f"  {C_DIM}calendar and shell to anyone who finds the port.{C_OFF}")
            print(f"  {C_DIM}Remove ARC_AUTH_MODE, or set ARC_HOST=127.0.0.1.{C_OFF}\n")
            return False
        print(f"{C_AMBER}  ! ARC_AUTH_MODE=open — no sign-in required (localhost only).{C_OFF}")
        return True

    if not gauth.web_available():
        print(f"\n{C_RED}  Google sign-in is not set up — refusing to start.{C_OFF}")
        print(f"  {C_DIM}console.cloud.google.com -> your project -> Credentials ->{C_OFF}")
        print(f"  {C_DIM}Create credentials -> OAuth client ID -> Web application.{C_OFF}")
        print(f"  {C_DIM}Add http://localhost:{PORT}/oauth/callback as a redirect URI,{C_OFF}")
        print(f"  {C_DIM}download the JSON, and save it as credentials_web.json in {ROOT}{C_OFF}\n")
        return False

    if not ALLOWED_EMAILS:
        print(f"\n{C_RED}  ARC_ALLOWED_EMAILS is empty — refusing to start.{C_OFF}")
        print(f"  {C_DIM}Nobody could sign in, and starting without a gate is worse.{C_OFF}")
        print(f"  {C_DIM}Set it in .env, e.g. ARC_ALLOWED_EMAILS=you@gmail.com{C_OFF}\n")
        return False

    if session.CLAMPED:
        print(f"{C_AMBER}  ! ARC_SESSION_MAX_HOURS above {session.MAX_HOURS_CEILING:g}"
              f" is capped at {session.MAX_HOURS_CEILING:g}.{C_OFF}")
    if not os.getenv("ARC_SECRET"):
        print(f"{C_AMBER}  ! ARC_SECRET is not set — a restart mid-sign-in will fail."
              f" Set it to a random 64-hex string.{C_OFF}")
    return True


def banner(port: int):
    ok = lambda b: f"{C_CYAN}online{C_OFF}" if b else f"{C_AMBER}not configured{C_OFF}"
    if AUTH_MODE == "open":
        lock = f"{C_AMBER}OPEN â€” localhost only, no sign-in{C_OFF}"
    else:
        who = ", ".join(sorted(ALLOWED_EMAILS - GUEST_EMAILS))
        if OWNER_UNLIMITED:
            span = "no session timeout"
        elif session.MAX_AGE:
            span = f"{session.MAX_AGE / 3600:g}h max, {session.IDLE_AGE / 60:g}m idle"
        else:
            span = f"signed out after {session.IDLE_AGE / 60:g}m unused"
        lock = f"{C_CYAN}google{C_OFF} {C_DIM}({who}; {span}){C_OFF}"
    # Spelled out on its own line: a guest is a different kind of account, and
    # the whole point is that it should never be mistaken for a second owner.
    guest_line = ""
    if GUEST_EMAILS and AUTH_MODE != "open":
        guest_line = (f"\n  {C_DIM}guests{C_OFF}      {C_AMBER}{', '.join(sorted(GUEST_EMAILS))}{C_OFF} "
                      f"{C_DIM}({len(GUEST_TOOLS)} tools — own google + lookups only"
                      # Guests keep the clocks the owner is exempt from, so say so
                      # here rather than leaving the line above to speak for both.
                      f"{f', {session.IDLE_AGE / 60:g}m idle' if OWNER_UNLIMITED else ''}){C_OFF}")
    print(f"""
{C_CYAN}  ARC{C_OFF} {C_DIM}Â· ambient response core{C_OFF}

  {C_DIM}reasoning{C_OFF}   {ok(bool(ANTHROPIC_KEY))} {C_DIM}({MODEL}, effort {EFFORT}){C_OFF}
  {C_DIM}voice{C_OFF}       {ok(bool(ELEVEN_KEY and ELEVEN_VOICE))} {C_DIM}({'elevenlabs' if ELEVEN_KEY and ELEVEN_VOICE else 'browser fallback'}){C_OFF}
  {C_DIM}google{C_OFF}      {ok(gauth.connected())} {C_DIM}({'calendar + mail' if gauth.connected() else 'run python gauth.py to connect'}){C_OFF}
  {C_DIM}telegram{C_OFF}    {ok(tg.connected())} {C_DIM}({'your account' if tg.connected() else 'run python tg_login.py to connect'}){C_OFF}
  {C_DIM}computer{C_OFF}    {ok(pc.connected())} {C_DIM}({'full control â€” localhost only' if pc.connected() else 'disabled (deployed)'}){C_OFF}
  {C_DIM}tools{C_OFF}       {C_CYAN}{len(all_tools())}{C_OFF} {C_DIM}live{C_OFF}
  {C_DIM}access{C_OFF}      {lock}{guest_line}
  {C_DIM}interface{C_OFF}   {C_CYAN}http://localhost:{port}{C_OFF}

  {C_DIM}ctrl-c to shut down{C_OFF}
""")


# uvicorn parses X-Forwarded-For and OVERWRITES request.client with it by
# default, trusting any localhost peer. That silently re-opened the rate-limit
# and brute-force bypass no matter what client_ip did â€” the spoofed value was
# already in request.client. Only let uvicorn honour the header when we've
# actually declared a proxy in front (ARC_TRUST_PROXY).
_UVICORN_PROXY = dict(
    proxy_headers=TRUST_PROXY,
    forwarded_allow_ips=("*" if TRUST_PROXY else None),
)


def serve_cloud():
    """Started by the hosting platform, not by you."""
    if not auth_preflight():
        raise SystemExit(1)
    if not PUBLIC_URL:
        print("  ! ARC_PUBLIC_URL is not set. Set it to this deployment's origin so the"
              " OAuth redirect can't be steered by a forwarded header.")
    if CLOUD and not TRUST_PROXY:
        print("  ! ARC_TRUST_PROXY is not set. If a proxy/load balancer sits in front,"
              " set it so per-IP limits use the real client address.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", **_UVICORN_PROXY)


def main():
    if CLOUD:
        serve_cloud()
        return

    if not ANTHROPIC_KEY:
        print(f"\n{C_AMBER}  No ANTHROPIC_API_KEY found.{C_OFF}")
        print(f"  {C_DIM}Copy .env.example to .env and paste your key from"
              f" console.anthropic.com{C_OFF}\n")

    # Single instance. If the port is already serving, a core is already up â€”
    # just open a window at it and exit. Starting a SECOND server on another
    # port is what produced duplicate cores, stray ports, and a window talking
    # to a stale process still holding an old API key.
    if not port_free(PORT):
        print(f"{C_DIM}  ARC is already running on {PORT} â€” opening a window{C_OFF}")
        open_window(PORT)
        return

    if not auth_preflight():
        raise SystemExit(1)

    banner(PORT)

    threading.Timer(1.2, lambda: open_window(PORT)).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", **_UVICORN_PROXY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C_DIM}  core offline{C_OFF}\n")
        sys.exit(0)
