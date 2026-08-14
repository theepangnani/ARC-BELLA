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

# Tool modules read their configuration (TG_API_ID, ARC_EMAIL_ALLOWLIST, ...)
# from the environment at import time, so they MUST be imported after .env is
# loaded â€” import them earlier and those settings silently read empty.
import gauth
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
TOOLKITS = (gcal, gmail, gextra, tg, pc, extras, media, display, notes, push)
TOOL_OWNER = {t["name"]: kit for kit in TOOLKITS for t in kit.TOOLS}


def all_tools(local: bool = True):
    """Only offer what is actually authorised â€” a signed-in calendar with no
    mail should produce calendar tools, not tools that fail on contact. Computer
    control is offered only to the local desktop, never to a remote (tunnelled)
    caller, so the model isn't tempted to try what it can't do from the phone."""
    kits = [kit for kit in TOOLKITS if kit.connected() and (local or kit is not pc)]
    return [t for kit in kits for t in kit.TOOLS]


def dispatch_tool(name: str, args: dict, local: bool = True) -> tuple[str, bool]:
    kit = TOOL_OWNER.get(name)
    if kit is None:
        return f"No such tool: {name}", True
    if kit is pc:
        return pc.run_tool(name, args, local=local)
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
# Whether to try ElevenLabs before the free Edge voice. Off by default: once its
# free quota is spent, attempting it just adds a 2-3s failed round-trip to every
# reply. Turn on only if the ElevenLabs account has credit.
PREFER_ELEVEN = os.getenv("ARC_PREFER_ELEVEN", "").strip().lower() in ("1", "true", "yes")

# Low latency matters more than raw depth for a voice loop, so the default is
# Haiku 4.5 — it answers in ~1s where Sonnet takes several. Set ARC_MODEL to
# claude-sonnet-5 if you want deeper answers and don't mind the extra wait.
MODEL = os.getenv("ARC_MODEL", "claude-haiku-4-5-20251001")

# This ceiling covers thinking AND the spoken reply together. On Sonnet 5
# thinking is on unless you disable it, so the old 1000 was enough for the
# reasoning and nothing else: the answer came back truncated, or empty, and an
# empty reply is what ARC uses to mean "that wasn't addressed to me". A silent
# assistant that appeared to be working as designed. Spoken replies are short,
# so the extra headroom costs nothing on a normal turn.
MAX_TOKENS = int(os.getenv("ARC_MAX_TOKENS", "8000"))

# How hard to think. 'low' keeps the voice loop snappy; raise to medium/high
# only if you want more considered answers at the cost of a slower reply.
EFFORT = os.getenv("ARC_EFFORT", "low")

# The 'effort' output control exists on Sonnet/Opus but NOT on Haiku, which
# rejects the request outright if it's sent. Gate it on the model in use.
SUPPORTS_EFFORT = "haiku" not in MODEL.lower()

# Tool calls take extra round trips. This bounds a runaway loop without
# clipping legitimate work â€” look at the calendar, then act on it, is two.
MAX_TOOL_ROUNDS = int(os.getenv("ARC_MAX_TOOL_ROUNDS", "6"))

# Hosting platforms (Render, Railway, Fly) hand you a PORT and expect you to
# listen on every interface. Locally we stay on loopback so nothing on your
# network can reach it.
CLOUD = bool(os.getenv("PORT"))
PORT = int(os.getenv("PORT") or os.getenv("ARC_PORT", "8420"))
# Loopback by default so nothing on your network can reach it. Set
# ARC_HOST=0.0.0.0 to let other devices on your Wi-Fi (a phone) open it â€” do
# that ONLY with ARC_PASSWORD set, or anyone on the network can use your key.
HOST = os.getenv("ARC_HOST", "").strip() or ("0.0.0.0" if CLOUD else "127.0.0.1")

# --- access control ------------------------------------------------------
# A public URL is found by bots within days. Without this, anyone who finds
# the address spends your Anthropic credit.
PASSWORD = os.getenv("ARC_PASSWORD", "").strip()

# Signs the login cookie. Set ARC_SECRET in production so sessions survive a
# restart; otherwise we generate a throwaway one and everyone logs in again.
SECRET = os.getenv("ARC_SECRET", "").strip() or secrets.token_hex(32)
SESSION_DAYS = int(os.getenv("ARC_SESSION_DAYS", "30"))
COOKIE = "arc_session"

# Per-visitor limits, and a whole-deployment ceiling. These are the brakes
# that stop one bad afternoon becoming a large bill.
RATE_PER_MIN = int(os.getenv("ARC_RATE_PER_MIN", "12"))
RATE_PER_HOUR = int(os.getenv("ARC_RATE_PER_HOUR", "120"))
DAILY_CAP = int(os.getenv("ARC_DAILY_CAP", "600"))

# Rough per-million-token prices so we can show a running daily spend and stop a
# runaway loop from quietly draining a paid key. Defaults are ~Haiku 4.5 rates;
# override if you switch models. DAILY_COST_CAP is a generous safety ceiling —
# normal use costs pennies a day, so hitting it means something's looping.
PRICE_IN = float(os.getenv("ARC_PRICE_IN_PER_MTOK", "1.0"))
PRICE_OUT = float(os.getenv("ARC_PRICE_OUT_PER_MTOK", "5.0"))
DAILY_COST_CAP = float(os.getenv("ARC_DAILY_COST_CAP", "5.0"))    # dollars; 0 = no cap

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
    # Phone-push loop: watch for reminders coming due and push them to the owner's
    # phone even with no browser open. Only runs when ntfy is configured; a single
    # owner topic means pushes never reach a signed-in visitor. Best-effort — any
    # error is swallowed so a flaky network can't take the server down.
    async def _push_loop():
        import anyio
        while True:
            try:
                due = await anyio.to_thread.run_sync(extras.due_for_push)
                for r in due:
                    label = r.get("label", "").strip() or "Reminder"
                    await anyio.to_thread.run_sync(
                        lambda m=label: push.send(m, title="ARC reminder", tags="alarm_clock"))
            except Exception:
                pass
            await asyncio.sleep(30)

    app.state.push_task = (asyncio.create_task(_push_loop())
                           if push.configured() else None)
    yield
    if app.state.push_task:
        app.state.push_task.cancel()
    await app.state.http.aclose()
    if app.state.claude:
        await app.state.claude.close()


# With a password set we're deployed, and the auto-generated API docs would
# hand a stranger the full shape of the service. Keep them for local work.
_docs = None if os.getenv("ARC_PASSWORD", "").strip() else "/docs"
app = FastAPI(title="ARC", lifespan=lifespan,
              docs_url=_docs, redoc_url=None,
              openapi_url=None if _docs is None else "/openapi.json")


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def _sign(raw: str) -> str:
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    """A signed expiry stamp. No database, no session store."""
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{exp}.{_sign(exp)}"


def token_valid(token: str) -> bool:
    if not token or "." not in token:
        return False
    exp, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(exp)):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def authed(request: Request) -> bool:
    if not PASSWORD:          # no password set = local use, wide open
        return True
    return token_valid(request.cookies.get(COOKIE, ""))


def require_auth(request: Request):
    if not authed(request):
        raise HTTPException(401, "Not signed in.")


# --------------------------------------------------------------------------
# per-user Google: each browser gets a random signed session id, and that id
# names a token file under google_sessions/. So two people signed into ARC link
# their OWN Google accounts and never see each other's mail or calendar.
# --------------------------------------------------------------------------
GOOGLE_DIR = ROOT / "google_sessions"
GOOGLE_DIR.mkdir(exist_ok=True)
SID_COOKIE = "arc_sid"


def _sid_sign(sid: str) -> str:
    return hmac.new(SECRET.encode(), ("sid:" + sid).encode(), hashlib.sha256).hexdigest()


def read_sid(request: Request):
    """The validated session id from the cookie, or None. Signed so nobody can
    forge one and reach someone else's token."""
    raw = request.cookies.get(SID_COOKIE, "")
    if "." not in raw:
        return None
    sid, sig = raw.rsplit(".", 1)
    if sid and hmac.compare_digest(sig, _sid_sign(sid)):
        return sid
    return None


def make_sid():
    """A fresh, unguessable session id plus its signed cookie value."""
    sid = secrets.token_urlsafe(24)
    return sid, f"{sid}.{_sid_sign(sid)}"


def google_path(sid: str) -> Path:
    # token_urlsafe yields only [A-Za-z0-9_-]; safe as a filename.
    return GOOGLE_DIR / (f"{sid}.json" if sid else "none.json")


def apply_session_google(request: Request):
    """Point every Google call in THIS request at the signed-in browser's own
    token. Missing/unconnected sessions get a path with no file, so the Google
    tools simply read as 'not connected' — never a fallback to anyone else's."""
    sid = read_sid(request)
    gauth.set_active_token_path(google_path(sid))
    return sid


def public_base_url(request: Request) -> str:
    """The externally-visible origin of this request, honouring the funnel's
    forwarding headers so the OAuth redirect_uri matches what Google will call
    back (…ts.net over the tunnel, localhost on the desktop)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
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
_day = {"stamp": time.strftime("%Y-%m-%d"), "count": 0, "tok_in": 0, "tok_out": 0}


def _day_cost() -> float:
    return _day["tok_in"] / 1e6 * PRICE_IN + _day["tok_out"] / 1e6 * PRICE_OUT
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
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
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
        _day.update(stamp=today, count=0, tok_in=0, tok_out=0)
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


def check_login_rate(request: Request):
    """
    Stops anyone brute-forcing the password. Only *failed* attempts count, and
    a success wipes the slate â€” otherwise everyone sharing a home or office
    connection gets locked out together over somebody's typo.
    """
    ip = client_ip(request)
    now = time.time()
    sweep(now)
    q = _logins[ip]
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= 10:
        raise HTTPException(429, "Too many attempts. Wait ten minutes.")


def note_login_failure(request: Request):
    _logins[client_ip(request)].append(time.time())


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
form{width:100%}
.field{position:relative}
input{width:100%;padding:14px 15px;background:rgba(4,7,12,.9);color:var(--ink);
 border:1px solid var(--line);font:inherit;font-size:14px;outline:none;text-align:center;
 letter-spacing:.22em;border-radius:10px;transition:border-color .15s,box-shadow .15s}
input::placeholder{color:var(--dim);letter-spacing:.16em}
input:focus{border-color:var(--ice);box-shadow:0 0 0 3px rgba(95,217,255,.12)}
button{width:100%;margin-top:11px;padding:14px;background:linear-gradient(180deg,#7fe4ff,#43caf0);
 color:#04121a;border:0;font:inherit;font-size:12px;font-weight:600;letter-spacing:.2em;
 text-transform:uppercase;cursor:pointer;border-radius:10px;transition:filter .15s,transform .05s}
button:hover{filter:brightness(1.08)}
button:active{transform:translateY(1px)}
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
  <form id="f">
    <div class="field">
      <input type="password" id="p" placeholder="Enter passphrase" autofocus autocomplete="current-password" aria-label="passphrase">
    </div>
    <button type="submit">Enter ARC</button>
    <div class="err" id="e"></div>
  </form>
  <div class="trust">
    <span>🔒 Self-hosted &amp; private</span>
    <span>🛡 Encrypted connection</span>
    <span>⚡ Powered by Claude</span>
    <span>✳ No sign-up</span>
  </div>
  <div class="foot">Want your own ARC? <a href="mailto:theepang@gmail.com?subject=I%27d%20like%20my%20own%20ARC">Request access →</a></div>
</div>
<script>
document.getElementById("f").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const e = document.getElementById("e");
  e.textContent = "";
  try {
    const r = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: document.getElementById("p").value })
    });
    if (r.ok) { location.href = "/"; return; }
    const d = await r.json().catch(() => ({}));
    e.textContent = d.detail || "Incorrect passphrase.";
  } catch (_) { e.textContent = "Could not reach the server."; }
});
</script></body></html>"""


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

let tickers;
try{tickers=JSON.parse(localStorage.getItem("arc.tickers")||"null");}catch(_){}
if(!Array.isArray(tickers)||!tickers.length) tickers=["AAPL","TSLA","NVDA","BTC-USD"];
async function pollMkts(){
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
    return {
        "claude": bool(ANTHROPIC_KEY),
        "calendar": gcal.connected(),
        "email": gmail.connected(),
        "contacts_drive": gextra.connected(),
        "telegram": tg.connected(),
        # Computer control is real only for the local desktop; over the tunnel
        # the phone gets everything else but not shell/system control.
        "computer": pc.connected() and is_local_request(request),
        # A natural server voice is always available now (free Edge neural TTS,
        # ElevenLabs on top when it has credit). The UI keys off this flag to
        # use server audio instead of the browser's robot voice.
        "elevenlabs": True,
        "model": MODEL,
    }


@app.post("/api/chat")
async def chat(request: Request, _=Depends(require_auth)):
    """Proxy to the Messages API. The key never reaches the browser."""
    check_rate(request)
    if not ANTHROPIC_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set. Add it to .env and restart.")

    payload = await read_json(request)
    messages = payload.get("messages") or []
    system = payload.get("system") or ""
    use_search = bool(payload.get("search"))
    see_screen = bool(payload.get("see_screen"))
    # Consent: only a genuine, user-authorised turn may run action tools. The
    # client sends this true only for turns the user themselves drove and ok'd;
    # background/automated calls (e.g. watch-mode glances) never set it, so they
    # can never act. See PASSIVE_TOOLS / _is_acting.
    allow_actions = bool(payload.get("allow_actions"))

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

    if not isinstance(system, str) or len(system) > 40_000:
        raise HTTPException(400, "System prompt is missing or too long.")

    # --- what ARC can actually do this turn -------------------------------
    # Computer control only for the local desktop, never over the tunnel.
    local = is_local_request(request)
    apply_session_google(request)   # use THIS signed-in user's own Google account
    tools = all_tools(local)
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

    # The UI's OFF / AUTO / ALWAYS switch used to be sent and then ignored by
    # Thinking is the single biggest source of reply latency, so for a voice
    # loop it is OFF by default and only turns on when the user explicitly asks
    # (the THINKING switch set to ON/ALWAYS). Most spoken questions don't need
    # it; the ones that do can opt in.
    want_think = payload.get("think")
    thinking_on = want_think is True or str(want_think).lower() in ("on", "always", "adaptive", "true")
    thinking = {"type": "adaptive"} if thinking_on else {"type": "disabled"}

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
    if see_screen and local and pc.connected():
        shot = pc.screenshot()
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

    searched = False
    used: list[str] = []
    blocked_actions: list[str] = []
    tokens_in = tokens_out = 0
    reply = ""
    claude = request.app.state.claude

    for _ in range(MAX_TOOL_ROUNDS):
        kwargs = dict(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=convo,
            thinking=thinking,
        )
        # The effort control is a Sonnet/Opus feature; Haiku rejects it. Only
        # send it on models that accept it.
        if SUPPORTS_EFFORT:
            kwargs["output_config"] = {"effort": EFFORT}
        if tools:
            kwargs["tools"] = tools

        try:
            resp = await claude.messages.create(**kwargs)
        except anthropic.APIConnectionError as e:
            raise HTTPException(502, f"Could not reach the Anthropic API: {e}")
        except anthropic.RateLimitError:
            raise HTTPException(429, "Rate limited by the Anthropic API. Try again shortly.")
        except anthropic.APIStatusError as e:
            print(f"{C_RED}  ! anthropic {e.status_code}: {str(e.message)[:300]}{C_OFF}")
            raise HTTPException(e.status_code, str(e.message)[:400])

        u = resp.usage
        tokens_in += getattr(u, "input_tokens", 0) or 0
        tokens_out += getattr(u, "output_tokens", 0) or 0

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
            out, failed = dispatch_tool(call.name, dict(call.input or {}), local=local)
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

    print(
        f"{C_DIM}  â€º {tokens_in} in / {tokens_out} out"
        f"{'  [searched]' if searched else ''}"
        f"{('  [' + ', '.join(used) + ']') if used else ''}{C_OFF}"
    )

    _day["tok_in"] += tokens_in
    _day["tok_out"] += tokens_out

    return JSONResponse({
        "reply": reply,
        "searched": searched,
        "thought": thinking["type"] == "adaptive",
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
        resp = await claude.messages.create(
            model=MODEL,
            max_tokens=500,
            system=sys_prompt,
            messages=[{"role": "user", "content": user_msg}],
            thinking={"type": "disabled"},
        )
    except anthropic.APIConnectionError as e:
        raise HTTPException(502, f"Could not reach the Anthropic API: {e}")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Rate limited by the Anthropic API.")
    except anthropic.APIStatusError as e:
        raise HTTPException(e.status_code, str(e.message)[:400])

    u = resp.usage
    _day["tok_in"] += getattr(u, "input_tokens", 0) or 0
    _day["tok_out"] += getattr(u, "output_tokens", 0) or 0

    new_note = " ".join(b.text for b in resp.content if b.type == "text").strip()[:1600]
    return JSONResponse({"note": new_note or note, "cost_today": round(_day_cost(), 4)})


async def _eleven_tts(http, text: str):
    """ElevenLabs audio, or None if it's not configured / out of quota / errors.
    Kept as a nicety: if the key has credit its voice is used; the moment it
    doesn't, we fall through to the free Edge voice instead of going silent."""
    if not (ELEVEN_KEY and ELEVEN_VOICE):
        return None
    try:
        r = await http.post(
            f"{ELEVEN_URL}/{ELEVEN_VOICE}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": ELEVEN_KEY, "content-type": "application/json"},
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
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


# Voices the picker offers. Whitelisted so a client can't pass arbitrary values.
EDGE_VOICES = {
    "en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-GB-RyanNeural",
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
    "en-AU-NatashaNeural", "en-IE-EmilyNeural",
}


async def _edge_tts(text: str, voice: str = ""):
    """Microsoft Edge neural voice. Free, no key, no quota — the default."""
    v = voice if voice in EDGE_VOICES else TTS_VOICE
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
        audio = await _eleven_tts(request.app.state.http, text)
    if audio is None:
        try:
            audio = await _edge_tts(text, voice)
        except Exception as e:
            raise HTTPException(502, f"Text-to-speech failed: {str(e)[:200]}")
    if not audio:
        raise HTTPException(502, "Text-to-speech produced no audio.")
    return Response(content=audio, media_type="audio/mpeg")


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
    try:
        return JSONResponse({"due": extras.due_reminders()})
    except Exception:
        return JSONResponse({"due": []})


@app.get("/api/push/status")
async def push_status(request: Request, _=Depends(require_auth)):
    """Whether phone push (ntfy) is set up on this server, for the UI to reflect."""
    return JSONResponse({"configured": push.configured()})


@app.post("/api/push/test")
async def push_test(request: Request, _=Depends(require_auth)):
    """Send a test notification to the owner's phone."""
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
    try:
        import anyio
        res = await anyio.to_thread.run_sync(pc.screen_changed)
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)})
    return JSONResponse({"ok": True, **res})


@app.post("/api/login")
async def login(request: Request):
    check_login_rate(request)
    if not PASSWORD:
        return JSONResponse({"ok": True})

    payload = await read_json(request)
    given = (payload.get("password") or "").strip()

    # constant-time compare, so timing gives nothing away
    if not hmac.compare_digest(given, PASSWORD):
        note_login_failure(request)
        raise HTTPException(401, "Incorrect passphrase.")

    clear_login_failures(request)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE, make_token(),
        max_age=SESSION_DAYS * 86400,
        httponly=True,          # JavaScript can't read it
        secure=CLOUD,           # HTTPS only once deployed
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


# --- per-user Google sign-in (web redirect flow) ---------------------------

@app.get("/oauth/google/start")
async def google_start(request: Request, _=Depends(require_auth)):
    """Send the signed-in user to Google's consent screen to link THEIR account."""
    if not gauth.web_available():
        raise HTTPException(500, "Per-user Google isn't set up (credentials_web.json is missing).")
    sid = read_sid(request)
    fresh_cookie = None
    if not sid:
        sid, fresh_cookie = make_sid()
    redirect_uri = public_base_url(request) + "/oauth/callback"
    is_https = redirect_uri.startswith("https://")
    try:
        url = gauth.web_auth_url(redirect_uri, state=_sid_sign(sid))
    except Exception as e:
        raise HTTPException(500, f"Could not start Google sign-in: {e}")
    resp = RedirectResponse(url, status_code=302)
    if fresh_cookie:
        resp.set_cookie(SID_COOKIE, fresh_cookie, max_age=SESSION_DAYS * 86400,
                        httponly=True, secure=is_https, samesite="lax", path="/")
    return resp


@app.get("/oauth/callback")
async def google_callback(request: Request, _=Depends(require_auth)):
    """Google sends the user back here with a code; trade it for this user's
    tokens and store them under their session id."""
    sid = read_sid(request)
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if request.query_params.get("error"):
        return RedirectResponse("/?google=denied", status_code=302)
    if not sid or not code or not state or not hmac.compare_digest(state, _sid_sign(sid)):
        return RedirectResponse("/?google=error", status_code=302)
    redirect_uri = public_base_url(request) + "/oauth/callback"
    try:
        import anyio
        token_json = await anyio.to_thread.run_sync(lambda: gauth.web_exchange(redirect_uri, code))
    except Exception as e:
        print(f"{C_RED}  ! google exchange failed: {str(e)[:200]}{C_OFF}")
        return RedirectResponse("/?google=error", status_code=302)
    try:
        google_path(sid).write_text(token_json, encoding="utf-8")
    except Exception:
        return RedirectResponse("/?google=error", status_code=302)
    return RedirectResponse("/?google=connected", status_code=302)


@app.get("/oauth/google/status")
async def google_status(request: Request, _=Depends(require_auth)):
    """What (if anything) this browser's Google account is, for the UI."""
    sid = apply_session_google(request)   # points gauth at this session's token
    p = google_path(sid)
    connected = bool(sid) and p.exists()
    email = ""
    if connected:
        try:
            import anyio
            email = await anyio.to_thread.run_sync(lambda: gauth.account_email(p))
        except Exception:
            email = ""
    return JSONResponse({
        "web_available": gauth.web_available(),
        "connected": connected,
        "email": email,
        "calendar": gcal.connected(),
        "gmail": gmail.connected(),
        "contacts_drive": gextra.connected(),
    })


@app.post("/oauth/google/disconnect")
async def google_disconnect(request: Request, _=Depends(require_auth)):
    """Unlink this browser's Google account (delete its stored token)."""
    sid = read_sid(request)
    if sid:
        try:
            google_path(sid).unlink(missing_ok=True)
        except Exception:
            pass
    return JSONResponse({"ok": True})


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
    if PASSWORD and not authed(request):
        path = request.url.path
        # PWA metadata and icons are not sensitive and must be fetchable so the
        # app stays installable and shows its icon even at the login screen.
        public = (
            path in ("/api/login", "/api/logout", "/favicon.ico",
                     "/sw.js", "/manifest.webmanifest")
            or path.startswith("/static/icon")
            or path in ("/static/arc-logo.svg", "/static/arc.ico")
        )
        if not public:
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return HTMLResponse(LOGIN_HTML, status_code=401)
            return JSONResponse({"detail": "Not signed in."}, status_code=401)
    return await call_next(request)


@app.get("/sw.js")
async def service_worker():
    # Served from root, not /static, so the worker's scope covers the whole app.
    return FileResponse(ROOT / "static" / "sw.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(ROOT / "static" / "manifest.webmanifest",
                        media_type="application/manifest+json")


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


@app.get("/display")
async def display_page(request: Request):
    """The second-screen dashboard. Open it and drag it to your second monitor."""
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return HTMLResponse(DISPLAY_HTML, headers=_NO_CACHE)


@app.get("/api/display")
async def api_display(request: Request, _=Depends(require_auth)):
    """Current second-screen content (the /display page polls this)."""
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
WINDOW_PROFILE = ROOT / ".arc-window"


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


def banner(port: int):
    ok = lambda b: f"{C_CYAN}online{C_OFF}" if b else f"{C_AMBER}not configured{C_OFF}"
    lock = f"{C_CYAN}passphrase{C_OFF}" if PASSWORD else f"{C_AMBER}OPEN â€” anyone can use your key{C_OFF}"
    print(f"""
{C_CYAN}  ARC{C_OFF} {C_DIM}Â· ambient response core{C_OFF}

  {C_DIM}reasoning{C_OFF}   {ok(bool(ANTHROPIC_KEY))} {C_DIM}({MODEL}, effort {EFFORT}){C_OFF}
  {C_DIM}voice{C_OFF}       {ok(bool(ELEVEN_KEY and ELEVEN_VOICE))} {C_DIM}({'elevenlabs' if ELEVEN_KEY and ELEVEN_VOICE else 'browser fallback'}){C_OFF}
  {C_DIM}google{C_OFF}      {ok(gauth.connected())} {C_DIM}({'calendar + mail' if gauth.connected() else 'run python gauth.py to connect'}){C_OFF}
  {C_DIM}telegram{C_OFF}    {ok(tg.connected())} {C_DIM}({'your account' if tg.connected() else 'run python tg_login.py to connect'}){C_OFF}
  {C_DIM}computer{C_OFF}    {ok(pc.connected())} {C_DIM}({'full control â€” localhost only' if pc.connected() else 'disabled (deployed)'}){C_OFF}
  {C_DIM}tools{C_OFF}       {C_CYAN}{len(all_tools())}{C_OFF} {C_DIM}live{C_OFF}
  {C_DIM}access{C_OFF}      {lock}
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
    if PASSWORD and not os.getenv("ARC_SECRET"):
        print("  ! ARC_SECRET is not set â€” everyone will be signed out on each restart.")
    if not PASSWORD:
        print("  ! ARC_PASSWORD is not set. Anyone who finds this URL can spend your credit.")
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

    banner(PORT)

    threading.Timer(1.2, lambda: open_window(PORT)).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", **_UVICORN_PROXY)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C_DIM}  core offline{C_OFF}\n")
        sys.exit(0)
