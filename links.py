#!/usr/bin/env python3
"""Linked accounts beyond Google: Spotify, Microsoft, GitHub, Notion.

The owner asked for "Spotify, Instagram, Snapchat and all the other possible
connectors". This is the part every one of them shares: how an account gets
linked, where its token lives, how the token is refreshed, and which services
cannot be linked at all and why.

HOW A LINK HAPPENS. Two flows, both without a client secret, so nothing new
that could leak has to sit in .env beyond a public client id:

  · "redirect" (Spotify): authorisation code with PKCE. The page asks
    /api/links/<id>/start, opens the service's own sign-in, and the service
    sends the browser back to /oauth/link/<id>/callback. The state is kept here,
    single-use, ten minutes, and bound to the account that started it — a
    callback arriving for somebody else's session links nothing.
  · "device" (Microsoft, GitHub): the page shows a short code and a web
    address; the person enters the code on any device they are signed in on,
    and the page polls until the service says yes. No redirect URI to
    register, which is why it suits an instance reached through a tunnel.
  · "token" (Notion): an internal-integration token the owner puts in .env.
    Owner only, because it is the owner's workspace.

WHERE A TOKEN LIVES. links/<service>/<hash of the address>.json in the data
directory, one file per person per service. The directory is gitignored, never
backed up by selfheal (a restored token would resurrect a link somebody
deliberately removed) and never in the export. No token is ever printed, put in
a tool result, or sent to the page: the page learns only linked/not linked and
the account's display name.

WHAT A LINK CAN DO is decided by the scopes below, which ask only for READING
wherever reading is enough — Microsoft mail is Mail.Read, for the same reason
Gmail is gmail.readonly — and by the consent gate for the few actions there
are (Spotify's play and pause). Widening a scope is the owner's decision.
"""

import base64
import hashlib
import os
import secrets
import threading
import time
from pathlib import Path

import httpx

import storefile
import whose

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
LINKS_DIR = DATA_DIR / "links"
TIMEOUT = 10

SERVICES = {
    "spotify": {
        "name": "Spotify", "flow": "redirect", "env": "SPOTIFY_CLIENT_ID",
        "auth_url": "https://accounts.spotify.com/authorize",
        "token_url": "https://accounts.spotify.com/api/token",
        "scopes": ["user-read-playback-state", "user-read-currently-playing",
                   "user-read-recently-played", "user-top-read",
                   "playlist-read-private", "user-library-read",
                   # The one write: pressing play, pause and skip on their own
                   # player. Gated by consent like every other action.
                   "user-modify-playback-state"],
        "setup": ("Create an app at developer.spotify.com, add this instance's "
                  "/oauth/link/spotify/callback as a redirect URI, and put its "
                  "client id in .env as SPOTIFY_CLIENT_ID."),
    },
    "microsoft": {
        "name": "Microsoft (Outlook, OneDrive)", "flow": "device", "env": "MS_CLIENT_ID",
        "device_url": "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": ["offline_access", "User.Read", "Mail.Read", "Calendars.Read", "Files.Read"],
        "setup": ("Register an app in the Azure portal (App registrations), allow "
                  "public client flows, and put its application id in .env as MS_CLIENT_ID."),
    },
    "github": {
        "name": "GitHub", "flow": "device", "env": "GITHUB_CLIENT_ID",
        "device_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        # Read-only on purpose: GitHub has no read-only scope for private
        # repositories ('repo' can push), so private code stays out until the
        # owner decides otherwise.
        "scopes": ["read:user", "notifications"],
        "setup": ("Create an OAuth app under GitHub Settings, Developer settings, tick "
                  "'Enable Device Flow', and put its client id in .env as GITHUB_CLIENT_ID."),
    },
    "notion": {
        "name": "Notion", "flow": "token", "env": "NOTION_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("Create an internal integration at notion.so/my-integrations, share "
                  "the pages Bella may read with it, and put its secret in .env as NOTION_TOKEN."),
    },
    # Asked for next ("monday and stuff"): the work tools people actually keep
    # their lives in. Each is a personal token in .env, owner only, because
    # every one of these services ties OAuth to a client SECRET, and a secret
    # is exactly what this file has been written to avoid. Read-only toolkits.
    "monday": {
        "name": "monday.com", "flow": "token", "env": "MONDAY_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("In monday.com, open your avatar, Developers, My access tokens, copy "
                  "your personal token, and put it in .env as MONDAY_TOKEN."),
    },
    "todoist": {
        "name": "Todoist", "flow": "token", "env": "TODOIST_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("In Todoist, Settings, Integrations, Developer, copy the API token, "
                  "and put it in .env as TODOIST_TOKEN."),
    },
    "trello": {
        "name": "Trello", "flow": "token", "env": "TRELLO_TOKEN", "env_extra": ["TRELLO_KEY"],
        "owner_only": True, "scopes": [],
        "setup": ("Create a Power-Up at trello.com/power-ups/admin for its API key, "
                  "generate a read-only token from it, and put them in .env as "
                  "TRELLO_KEY and TRELLO_TOKEN."),
    },
    "asana": {
        "name": "Asana", "flow": "token", "env": "ASANA_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("In Asana, open the developer console (My settings, Apps, Developer "
                  "apps), create a personal access token, and put it in .env as ASANA_TOKEN."),
    },
    "clickup": {
        "name": "ClickUp", "flow": "token", "env": "CLICKUP_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("In ClickUp, Settings, Apps, generate your personal API token, and "
                  "put it in .env as CLICKUP_TOKEN."),
    },
    "linear": {
        "name": "Linear", "flow": "token", "env": "LINEAR_API_KEY", "owner_only": True,
        "scopes": [],
        "setup": ("In Linear, Settings, Security & access, Personal API keys, create a "
                  "read-only key, and put it in .env as LINEAR_API_KEY."),
    },
    "airtable": {
        "name": "Airtable", "flow": "token", "env": "AIRTABLE_TOKEN", "owner_only": True,
        "scopes": [],
        "setup": ("At airtable.com/create/tokens, create a token with data.records:read "
                  "and schema.bases:read only, and put it in .env as AIRTABLE_TOKEN."),
    },
    "slack": {
        "name": "Slack", "flow": "token", "env": "SLACK_USER_TOKEN", "owner_only": True,
        "scopes": ["search:read", "channels:history", "channels:read", "groups:history",
                   "groups:read", "im:history", "im:read", "users:read"],
        "setup": ("Create a Slack app at api.slack.com/apps, add ONLY these user token "
                  "scopes: search:read, channels:history, channels:read, groups:history, "
                  "groups:read, im:history, im:read, users:read — install it to your "
                  "workspace, and put the User OAuth Token in .env as SLACK_USER_TOKEN."),
    },
    "dropbox": {
        "name": "Dropbox", "flow": "redirect", "env": "DROPBOX_CLIENT_ID",
        "auth_url": "https://www.dropbox.com/oauth2/authorize",
        "token_url": "https://api.dropboxapi.com/oauth2/token",
        # Without offline access Dropbox hands out a four-hour token and no
        # refresh token, and the link dies by teatime.
        "auth_extra": {"token_access_type": "offline"},
        "scopes": ["account_info.read", "files.metadata.read", "files.content.read"],
        "setup": ("Create an app at dropbox.com/developers/apps (Scoped access), tick "
                  "only account_info.read, files.metadata.read and files.content.read, "
                  "add this instance's /oauth/link/dropbox/callback as a redirect URI, "
                  "and put the App key in .env as DROPBOX_CLIENT_ID."),
    },
}

# Asked for, and not possible, with the honest reason. Shown in the sheet so
# the answer to "why isn't Instagram there" is on the screen, not a guess.
UNAVAILABLE = [
    {"id": "instagram", "name": "Instagram",
     "why": ("Instagram closed its API for personal accounts in December 2024. Only "
             "Business and Creator accounts can be reached, through a Meta app that "
             "Meta has to review, and even then not your DMs.")},
    {"id": "snapchat", "name": "Snapchat",
     "why": "Snapchat has no way for an app to read your snaps, chats or stories."},
    {"id": "whatsapp", "name": "WhatsApp",
     "why": ("WhatsApp only offers an API for business phone numbers, not a way to "
             "read or send from your own account.")},
    {"id": "tiktok", "name": "TikTok",
     "why": ("TikTok's API needs an app TikTok approves, and gives your own videos "
             "and profile — not your feed or messages.")},
    {"id": "imessage", "name": "iMessage",
     "why": "Apple offers no API for iMessage, and it cannot be reached from Windows."},
    {"id": "messenger", "name": "Facebook Messenger",
     "why": "Messenger's API is for businesses answering customers, not for your own chats."},
    {"id": "discord", "name": "Discord",
     "why": ("Discord only lets bots in, to servers that add them. Reading your own DMs "
             "or servers as you is against its rules and gets accounts banned.")},
    {"id": "netflix", "name": "Netflix",
     "why": "Netflix closed its public API in 2014. Bella can open it for you, not see into it."},
]

_pending: dict = {}      # state -> {service, who, verifier, redirect, at}
_pending_lock = threading.Lock()
PENDING_SECONDS = 600


def _client_id(sid: str) -> str:
    return (os.getenv(SERVICES[sid]["env"]) or "").strip()


def configured(sid: str) -> bool:
    """The owner has done the one-time setup on this instance."""
    return (sid in SERVICES and bool(_client_id(sid))
            and all((os.getenv(e) or "").strip() for e in SERVICES[sid].get("env_extra", ())))


def extra(sid: str, env: str) -> str:
    """A second setting a token service needs (Trello's API key). Only names
    listed in the service's env_extra can be read this way."""
    if env not in SERVICES.get(sid, {}).get("env_extra", ()):
        raise KeyError(env)
    return (os.getenv(env) or "").strip()


def _path(sid: str) -> Path:
    who = whose.current().encode("utf-8")
    return LINKS_DIR / sid / (hashlib.sha256(who).hexdigest()[:24] + ".json")


def _load(sid: str) -> dict:
    p = _path(sid)
    try:
        got = storefile.read(p, dict)
    except storefile.Unreadable:
        return {}
    return got if isinstance(got, dict) else {}


def _save(sid: str, data: dict) -> None:
    p = _path(sid)
    with storefile.lock(p):
        storefile.write(p, data)


def linked(sid: str) -> bool:
    s = SERVICES.get(sid)
    if not s:
        return False
    if s["flow"] == "token":
        return configured(sid) and (not s.get("owner_only") or whose.is_owner())
    return configured(sid) and bool(_load(sid).get("access_token"))


def account(sid: str) -> str:
    if SERVICES.get(sid, {}).get("flow") == "token":
        return "this instance's integration" if linked(sid) else ""
    return str(_load(sid).get("account") or "") if linked(sid) else ""


def unlink(sid: str) -> bool:
    p = _path(sid)
    with storefile.lock(p):
        try:
            p.unlink()
            return True
        except FileNotFoundError:
            return False


def _store_token(sid: str, tok: dict, account_name: str = "") -> None:
    old = _load(sid)
    data = {
        "access_token": tok["access_token"],
        # A refresh may or may not hand back a new refresh token; keep the old
        # one when it does not, or the link dies at the next expiry.
        "refresh_token": tok.get("refresh_token") or old.get("refresh_token", ""),
        "expires_at": (time.time() + int(tok["expires_in"]) - 60) if tok.get("expires_in") else 0,
        "scope": tok.get("scope", ""),
        "account": account_name or old.get("account", ""),
        "linked_at": old.get("linked_at") or time.time(),
    }
    _save(sid, data)


# ---- redirect flow (PKCE) ---------------------------------------------------

def start_redirect(sid: str, redirect_uri: str, bind: str = "") -> str:
    s = SERVICES[sid]
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _pending_lock:
        for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_SECONDS]:
            del _pending[k]
        _pending[state] = {"service": sid, "who": whose.current(), "bind": bind,
                           "verifier": verifier, "redirect": redirect_uri, "at": now}
    q = httpx.QueryParams({
        "client_id": _client_id(sid), "response_type": "code",
        "redirect_uri": redirect_uri, "code_challenge_method": "S256",
        "code_challenge": challenge, "scope": " ".join(s["scopes"]), "state": state,
        **s.get("auth_extra", {}),
    })
    return "%s?%s" % (s["auth_url"], q)


def finish_redirect(sid: str, state: str, code: str, post=None, bind: str = "") -> str:
    """Exchange the code. Returns '' on success, or what went wrong. A state
    that doesn't match — wrong service, expired, used, another account, another
    browser — stores nothing, and is spent either way."""
    with _pending_lock:
        p = _pending.pop(state or "", None)
    # "verifier": a device-flow handle passed as a state is not a redirect
    # sign-in, and used to reach a KeyError below.
    if (not p or p["service"] != sid or "verifier" not in p
            or time.time() - p["at"] > PENDING_SECONDS):
        return "That sign-in link has expired or was already used. Start again."
    if p["who"] != whose.current() or p.get("bind", "") != bind:
        return "That sign-in was started somewhere else. Start again from this screen."
    if not code:
        return "The sign-in came back without a code. Start again."
    post = post or httpx.post
    r = post(SERVICES[sid]["token_url"], data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect"],
        "client_id": _client_id(sid), "code_verifier": p["verifier"],
    }, timeout=TIMEOUT)
    if r.status_code != 200:
        return "%s refused the sign-in (%s)." % (SERVICES[sid]["name"], r.status_code)
    tok = r.json()
    if not tok.get("access_token"):
        return "%s sent back no token." % SERVICES[sid]["name"]
    _store_token(sid, tok)
    return ""


# ---- device flow (RFC 8628) -------------------------------------------------

def start_device(sid: str, post=None, bind: str = "") -> dict:
    s = SERVICES[sid]
    post = post or httpx.post
    r = post(s["device_url"], data={"client_id": _client_id(sid), "scope": " ".join(s["scopes"])},
             headers={"Accept": "application/json"}, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError("%s would not start a sign-in (%s)" % (s["name"], r.status_code))
    d = r.json()
    handle = secrets.token_urlsafe(18)
    with _pending_lock:
        _pending[handle] = {"service": sid, "who": whose.current(), "bind": bind,
                            "device_code": d["device_code"],
                            "at": time.time(), "expires": time.time() + int(d.get("expires_in", 900)),
                            "interval": int(d.get("interval", 5)), "last": 0.0}
    # Only what the person needs to see. The device code itself never leaves.
    return {"handle": handle, "user_code": d["user_code"],
            "verification_uri": d.get("verification_uri") or d.get("verification_url", ""),
            "interval": int(d.get("interval", 5))}


def poll_device(sid: str, handle: str, post=None, bind: str = "") -> str:
    """'linked', 'waiting', or a sentence saying what went wrong."""
    with _pending_lock:
        p = _pending.get(handle or "")
    if (not p or p["service"] != sid or p["who"] != whose.current()
            or p.get("bind", "") != bind):
        return "That sign-in isn't running any more. Start again."
    if time.time() > p["expires"]:
        with _pending_lock:
            _pending.pop(handle, None)
        return "The code expired before it was entered. Start again."
    if time.time() - p["last"] < p["interval"]:
        return "waiting"
    p["last"] = time.time()
    s = SERVICES[sid]
    post = post or httpx.post
    r = post(s["token_url"], data={
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": _client_id(sid), "device_code": p["device_code"],
    }, headers={"Accept": "application/json"}, timeout=TIMEOUT)
    d = r.json() if r.content else {}
    err = d.get("error")
    if err in ("authorization_pending",):
        return "waiting"
    if err == "slow_down":
        p["interval"] += 5
        return "waiting"
    if err or not d.get("access_token"):
        with _pending_lock:
            _pending.pop(handle, None)
        return "%s said no (%s)." % (s["name"], err or r.status_code)
    with _pending_lock:
        _pending.pop(handle, None)
    _store_token(sid, d)
    return "linked"


# ---- using a link -----------------------------------------------------------

class NotLinked(Exception):
    pass


def token(sid: str, post=None) -> str:
    """A usable access token for this person, refreshed if it has expired."""
    s = SERVICES[sid]
    if s["flow"] == "token":
        if not linked(sid):
            raise NotLinked("%s isn't set up on this instance." % s["name"])
        return _client_id(sid)
    data = _load(sid)
    if not data.get("access_token") or not configured(sid):
        raise NotLinked("%s isn't linked. Link it in Connectors." % s["name"])
    if data.get("expires_at") and time.time() >= data["expires_at"]:
        if not data.get("refresh_token"):
            raise NotLinked("The %s link has expired. Link it again in Connectors." % s["name"])
        post = post or httpx.post
        r = post(s["token_url"], data={
            "grant_type": "refresh_token", "refresh_token": data["refresh_token"],
            "client_id": _client_id(sid),
        }, headers={"Accept": "application/json"}, timeout=TIMEOUT)
        if r.status_code != 200 or not r.json().get("access_token"):
            raise NotLinked("The %s link has lapsed. Link it again in Connectors." % s["name"])
        _store_token(sid, r.json())
        data = _load(sid)
    return data["access_token"]


def set_account(sid: str, name: str) -> None:
    data = _load(sid)
    if data:
        data["account"] = str(name or "")[:80]
        _save(sid, data)


def catalogue() -> list:
    """For the page: every linkable service and every impossible one."""
    out = []
    for sid, s in SERVICES.items():
        owner_only = s.get("owner_only") and not whose.is_owner()
        out.append({"id": sid, "name": s["name"], "flow": s["flow"],
                    "configured": configured(sid) and not owner_only,
                    "linked": linked(sid), "account": account(sid),
                    "setup": "" if configured(sid) else s["setup"]})
    return out
