#!/usr/bin/env python3
"""
ARC — Ambient Response Core
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
import socket
import secrets
import hashlib
import threading
import webbrowser
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response, FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "").strip()

# Low latency matters more than raw depth for a voice loop. Sonnet 5 is the
# sweet spot; drop to claude-haiku-4-5 if you want it snappier and cheaper.
MODEL = os.getenv("ARC_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.getenv("ARC_MAX_TOKENS", "1000"))

# Hosting platforms (Render, Railway, Fly) hand you a PORT and expect you to
# listen on every interface. Locally we stay on loopback so nothing on your
# network can reach it.
CLOUD = bool(os.getenv("PORT"))
PORT = int(os.getenv("PORT") or os.getenv("ARC_PORT", "8420"))
HOST = "0.0.0.0" if CLOUD else "127.0.0.1"

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

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech"

C_DIM, C_CYAN, C_AMBER, C_RED, C_OFF = "\033[2m", "\033[96m", "\033[93m", "\033[91m", "\033[0m"


# --------------------------------------------------------------------------
# lifespan
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=10.0))
    yield
    await app.state.http.aclose()


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
# rate limiting  (in-memory; resets when the process restarts)
# --------------------------------------------------------------------------

_hits = defaultdict(deque)
_day = {"stamp": time.strftime("%Y-%m-%d"), "count": 0}
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
    # Behind a host's proxy the real address is in this header.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(request: Request):
    today = time.strftime("%Y-%m-%d")
    if _day["stamp"] != today:
        _day.update(stamp=today, count=0)
    if _day["count"] >= DAILY_CAP:
        raise HTTPException(429, "Daily limit reached for this deployment. Try again tomorrow.")

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
    a success wipes the slate — otherwise everyone sharing a home or office
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


LOGIN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARC</title><style>
*{box-sizing:border-box}
body{margin:0;height:100vh;display:grid;place-items:center;background:#04070c;
 font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;color:#5fd9ff}
form{width:min(320px,88vw);text-align:center}
h1{font-size:22px;letter-spacing:.42em;margin:0 0 6px;font-weight:400}
p{color:#1c6d8f;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;margin:0 0 26px}
input{width:100%;padding:12px 14px;background:rgba(4,7,12,.9);color:#dbe9f5;
 border:1px solid #12293c;font:inherit;font-size:13px;outline:none;text-align:center;letter-spacing:.2em}
input:focus{border-color:#5fd9ff}
button{width:100%;margin-top:10px;padding:12px;background:#5fd9ff;color:#04070c;
 border:0;font:inherit;font-size:11px;letter-spacing:.18em;text-transform:uppercase;cursor:pointer}
.err{color:#ff7d96;font-size:11px;margin-top:14px;min-height:16px;letter-spacing:.04em;text-transform:none}
</style></head><body>
<form id="f">
  <h1>ARC</h1>
  <p>ambient response core</p>
  <input type="password" id="p" placeholder="passphrase" autofocus autocomplete="current-password">
  <button type="submit">Enter</button>
  <div class="err" id="e"></div>
</form>
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
    e.textContent = d.detail || "Incorrect.";
  } catch (_) { e.textContent = "Could not reach the server."; }
});
</script></body></html>"""


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get("/api/health")
async def health(request: Request, _=Depends(require_auth)):
    """The UI calls this on boot so it can show what's actually wired up."""
    return {
        "claude": bool(ANTHROPIC_KEY),
        "elevenlabs": bool(ELEVEN_KEY and ELEVEN_VOICE),
        "model": MODEL,
    }


@app.post("/api/chat")
async def chat(request: Request, _=Depends(require_auth)):
    """Proxy to the Messages API. The key never reaches the browser."""
    check_rate(request)
    if not ANTHROPIC_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY is not set. Add it to .env and restart.")

    payload = await request.json()
    messages = payload.get("messages") or []
    system = payload.get("system") or ""
    use_search = bool(payload.get("search"))

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

    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
    }
    if use_search:
        body["tools"] = [{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
        }]

    try:
        r = await request.app.state.http.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=body,
        )
    except httpx.RequestError as e:
        raise HTTPException(502, f"Could not reach the Anthropic API: {e}")

    if r.status_code != 200:
        detail = r.text[:400]
        print(f"{C_RED}  ! anthropic {r.status_code}: {detail}{C_OFF}")
        raise HTTPException(r.status_code, detail)

    data = r.json()

    # A response with web search in play holds several block types. Pull the
    # spoken text out by type, never by position.
    reply = " ".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()

    searched = any(
        b.get("type") in ("server_tool_use", "web_search_tool_result")
        for b in data.get("content", [])
    )

    usage = data.get("usage", {})
    print(
        f"{C_DIM}  › {usage.get('input_tokens', '?')} in / "
        f"{usage.get('output_tokens', '?')} out"
        f"{'  [searched]' if searched else ''}{C_OFF}"
    )

    return JSONResponse({"reply": reply, "searched": searched})


@app.post("/api/tts")
async def tts(request: Request, _=Depends(require_auth)):
    """
    Optional. Returns real audio from ElevenLabs so the assistant stops
    sounding like a browser. Without keys the UI falls back to speechSynthesis.
    """
    if not (ELEVEN_KEY and ELEVEN_VOICE):
        raise HTTPException(503, "ElevenLabs is not configured.")

    payload = await request.json()
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "No text supplied.")
    if len(text) > 5000:
        raise HTTPException(400, "Too much text for one utterance.")

    try:
        r = await request.app.state.http.post(
            f"{ELEVEN_URL}/{ELEVEN_VOICE}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": ELEVEN_KEY, "content-type": "application/json"},
            json={
                "text": text,
                # turbo keeps round-trip under ~400ms, which is the difference
                # between a conversation and a transaction
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {
                    "stability": 0.45,       # lower = more expressive
                    "similarity_boost": 0.8,
                    "style": 0.15,
                    "use_speaker_boost": True,
                },
            },
        )
    except httpx.RequestError as e:
        raise HTTPException(502, f"Could not reach ElevenLabs: {e}")

    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text[:300])

    return Response(content=r.content, media_type="audio/mpeg")


@app.post("/api/login")
async def login(request: Request):
    check_login_rate(request)
    if not PASSWORD:
        return JSONResponse({"ok": True})

    payload = await request.json()
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


@app.get("/")
async def index(request: Request):
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/static/index.html")
async def index_direct(request: Request):
    """Same gate on the direct path, so it can't be walked around."""
    if not authed(request):
        return HTMLResponse(LOGIN_HTML, status_code=401)
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


# --------------------------------------------------------------------------
# launcher
# --------------------------------------------------------------------------

def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def banner(port: int):
    ok = lambda b: f"{C_CYAN}online{C_OFF}" if b else f"{C_AMBER}not configured{C_OFF}"
    lock = f"{C_CYAN}passphrase{C_OFF}" if PASSWORD else f"{C_AMBER}OPEN — anyone can use your key{C_OFF}"
    print(f"""
{C_CYAN}  ARC{C_OFF} {C_DIM}· ambient response core{C_OFF}

  {C_DIM}reasoning{C_OFF}   {ok(bool(ANTHROPIC_KEY))} {C_DIM}({MODEL}){C_OFF}
  {C_DIM}voice{C_OFF}       {ok(bool(ELEVEN_KEY and ELEVEN_VOICE))} {C_DIM}({'elevenlabs' if ELEVEN_KEY and ELEVEN_VOICE else 'browser fallback'}){C_OFF}
  {C_DIM}access{C_OFF}      {lock}
  {C_DIM}interface{C_OFF}   {C_CYAN}http://localhost:{port}{C_OFF}

  {C_DIM}ctrl-c to shut down{C_OFF}
""")


def serve_cloud():
    """Started by the hosting platform, not by you."""
    if PASSWORD and not os.getenv("ARC_SECRET"):
        print("  ! ARC_SECRET is not set — everyone will be signed out on each restart.")
    if not PASSWORD:
        print("  ! ARC_PASSWORD is not set. Anyone who finds this URL can spend your credit.")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main():
    if CLOUD:
        serve_cloud()
        return

    if not ANTHROPIC_KEY:
        print(f"\n{C_AMBER}  No ANTHROPIC_API_KEY found.{C_OFF}")
        print(f"  {C_DIM}Copy .env.example to .env and paste your key from"
              f" console.anthropic.com{C_OFF}\n")

    port = PORT
    while not port_free(port) and port < PORT + 12:
        port += 1
    if port != PORT:
        print(f"{C_DIM}  port {PORT} busy, using {port}{C_OFF}")

    banner(port)

    threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C_DIM}  core offline{C_OFF}\n")
        sys.exit(0)
