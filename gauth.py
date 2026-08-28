#!/usr/bin/env python3
"""
One Google sign-in, shared by every tool that needs it.

    python gauth.py       # sign in / re-consent after a scope change

Both gcal.py and gmail.py go through here so there is a single token file and
a single consent screen, rather than one per capability.
"""

import os
import contextvars
from pathlib import Path

# Google often returns the granted scopes in a different order / as a superset
# (especially with include_granted_scopes), which makes oauthlib raise a
# "Scope has changed" error during the token exchange. Relaxing this check is
# the standard fix and is safe: we still verify the specific scopes we need via
# granted_scopes() before using each capability.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

ROOT = Path(__file__).parent.resolve()
# Shared CONFIG (the OAuth client secrets) stays in ROOT so every instance uses
# the same Google app. The owner TOKEN is per-instance DATA, so a second Bella
# (its own ARC_DATA_DIR) signs into its own Google account. Defaults to ROOT.
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
CREDENTIALS = ROOT / "credentials.json"          # desktop client (owner CLI sign-in)
WEB_CREDENTIALS = ROOT / "credentials_web.json"  # web client (per-user browser sign-in)
TOKEN = DATA_DIR / "token.json"                  # the owner's token (CLI fallback)

# Per-request override: when ARC is serving a signed-in user, run.py points this
# at THAT user's own token file, so every gauth call below reads and refreshes
# the right person's Google account instead of one shared token. Unset (None)
# falls back to the owner's TOKEN — used by the gauth.py CLI itself.
_active_path = contextvars.ContextVar("google_token_path", default=None)


def set_active_token_path(path):
    """Point all Google calls in THIS request at a specific user's token file
    (or None to fall back to the owner token)."""
    _active_path.set(str(path) if path else None)


def _tok() -> Path:
    p = _active_path.get()
    return Path(p) if p else TOKEN

# Deliberately the narrowest set that does the job:
#   calendar        read and write events
#   gmail.readonly  read mail — and nothing else
# Notably absent: gmail.compose (drafting and sending) and gmail.modify
# (labelling, archiving, binning). Mail is read-only by design, so ARC cannot
# write to the account at all, and nothing it reads can be mailed back out.
CAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Read only. gmail.compose was granted once, which let ARC create drafts and
# send them; it is deliberately gone. Nothing can now leave the mail account,
# so the two-step draft-then-send dance and its send caps are gone with it —
# there is no send to cap. This also collapses the worst prompt-injection path
# in the product: mail could previously be both the injection vector and the
# exfiltration channel, and now it is only the former.
MAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CONTACTS_SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Identity. Google is not just linked here, it is who you sign in AS — so the
# consent screen has to return an ID token we can verify and read an address
# from. One sign-in covers both: proving who you are, and granting the tools.
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]

SCOPES = IDENTITY_SCOPES + CAL_SCOPES + MAIL_SCOPES + CONTACTS_SCOPES + DRIVE_SCOPES


class NotConnected(Exception):
    """No usable token, or the granted scopes no longer cover what we need."""


def granted_scopes() -> set[str]:
    """What the stored token actually carries.

    Read from the file, not from the Credentials object: passing scopes to
    from_authorized_user_file overwrites creds.scopes with what we *asked*
    for, so checking that compares a list against itself and always passes.
    """
    import json
    try:
        return set(json.loads(_tok().read_text(encoding="utf-8")).get("scopes") or [])
    except Exception:
        return set()


def has(needs) -> bool:
    """Is this particular capability authorised? Calendar can work while mail doesn't."""
    return _tok().exists() and set(needs).issubset(granted_scopes())


def connected() -> bool:
    return _tok().exists()


def ungranted(needs) -> bool:
    """Linked, but THIS capability was not granted.

    The difference matters to the person and not to the code. Google's consent
    screen arrives with every box UNTICKED, and prompt=consent means it arrives
    on every sign-in — so pressing Continue a moment early leaves an account
    that is properly connected and cannot read the calendar.

    Both cases currently look identical from outside: connected() is False and a
    chip goes dark. One of them means "connect Google" and the other means "you
    did, but you left Calendar unticked", and telling somebody to do a thing
    they have already done is how they conclude the app is broken.
    """
    return _tok().exists() and not set(needs).issubset(granted_scopes())


def service(api: str, version: str, needs=None):
    """Build a Google API client, refreshing the token if it has expired.

    `needs` is the scope list this particular call requires — so a token that
    covers calendar but not mail keeps the calendar working instead of taking
    everything down with it.
    """
    needs = needs or SCOPES
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        raise NotConnected(
            "The Google libraries are not installed. Run: pip install -r requirements.txt"
        ) from e

    tok = _tok()
    if not tok.exists():
        raise NotConnected("Google is not connected yet.")

    creds = Credentials.from_authorized_user_file(str(tok), SCOPES)

    # Adding a capability adds a scope, and an old token predates it. Say so
    # plainly — the alternative is a 403 from deep inside the client library.
    granted = granted_scopes()
    missing = [s for s in needs if s not in granted]
    if missing:
        raise NotConnected(
            "This needs permissions you haven't granted yet ("
            + ", ".join(s.rsplit("/", 1)[-1] for s in missing)
            + "). Run: python gauth.py"
        )

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tok.write_text(creds.to_json(), encoding="utf-8")   # persist the fresh access token
        else:
            raise NotConnected("The Google sign-in has lapsed. Please reconnect Google.")

    return build(api, version, credentials=creds, cache_discovery=False)


def connect():
    """Interactive sign-in. Opens a browser. Safe to re-run."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS.exists():
        print(f"\n  Missing {CREDENTIALS.name}.\n")
        print("  console.cloud.google.com -> your project ->")
        print("    APIs & Services -> Library -> enable Google Calendar API and Gmail API")
        print("    Credentials -> Create credentials -> OAuth client ID -> Desktop app")
        print(f"    Download the JSON, rename to credentials.json, put it in {ROOT}\n")
        return False

    if TOKEN.exists():
        TOKEN.unlink()          # a scope change needs fresh consent
        print("  Existing token cleared — re-consenting with the current scopes.")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")

    print(f"\n  Connected. Granted:")
    for s in creds.scopes or []:
        print(f"    · {s.rsplit('/', 1)[-1]}")
    print("\n  Restart ARC.\n")
    return True


# --------------------------------------------------------------------------
# Per-user web sign-in (browser). Unlike connect() above (a desktop CLI flow
# for the owner), this is the redirect flow each visitor uses to link their OWN
# Google account. Requires credentials_web.json (a "Web application" client).
# --------------------------------------------------------------------------

def web_available() -> bool:
    return WEB_CREDENTIALS.exists()


def web_client_id() -> str:
    """The web OAuth client id — the audience an ID token must be issued for."""
    import json
    data = json.loads(WEB_CREDENTIALS.read_text(encoding="utf-8"))
    return (data.get("web") or data.get("installed") or {}).get("client_id", "")


def _web_flow(redirect_uri, state=None, code_verifier=None):
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(
        str(WEB_CREDENTIALS), scopes=SCOPES, redirect_uri=redirect_uri, state=state
    )
    # PKCE. Without this, an authorization code intercepted on the redirect
    # (a shared machine, a logged URL) can be redeemed by whoever holds it.
    # With it, redemption also needs the verifier, which never leaves us.
    flow.autogenerate_code_verifier = False
    flow.code_verifier = code_verifier
    return flow


def web_auth_url(redirect_uri, state, nonce, code_verifier):
    """The Google consent URL to send the user's browser to.

    prompt=consent is deliberate and is the "authenticate every single time"
    requirement: the full permission screen appears on every sign-in rather
    than Google silently waving a returning user through. offline keeps a
    refresh token coming back so the tools still work for the session's life.
    """
    flow = _web_flow(redirect_uri, state, code_verifier)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        nonce=nonce,
    )
    return url


def web_exchange(redirect_uri, code, code_verifier=None):
    """Trade the callback code for tokens.

    Returns (token_json, id_token). The ID token is what proves *who* signed
    in; the credentials JSON is what the calendar/mail tools use afterwards.
    """
    flow = _web_flow(redirect_uri, code_verifier=code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return creds.to_json(), getattr(creds, "id_token", None)


class NotAuthorised(Exception):
    """Google authenticated somebody, but not somebody we accept."""


def verify_id_token(token: str, nonce: str) -> dict:
    """Verify Google's ID token and return its claims.

    Checks the signature against Google's published keys, the audience (this
    app's client id), the issuer, and expiry. We then check the nonce we
    generated — which is what stops an ID token captured from another sign-in
    being replayed here — and that Google actually vouches for the address.
    """
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    if not token:
        raise NotAuthorised("Google returned no ID token.")

    claims = google_id_token.verify_oauth2_token(
        token, google_requests.Request(), web_client_id(), clock_skew_in_seconds=10
    )

    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise NotAuthorised("ID token came from an unexpected issuer.")
    if not nonce or claims.get("nonce") != nonce:
        raise NotAuthorised("ID token nonce did not match this sign-in.")
    if not claims.get("email"):
        raise NotAuthorised("ID token carried no email address.")
    if not claims.get("email_verified"):
        raise NotAuthorised("That Google address is not verified.")
    return claims


def account_email(token_path) -> str:
    """Best-effort: which Google address this token belongs to (for the UI). We
    didn't ask for the userinfo scope, so read it from the Gmail profile, and
    fall back to the primary calendar id (which is the account's email)."""
    prev = _active_path.get()
    try:
        _active_path.set(str(token_path))
        try:
            svc = service("gmail", "v1", MAIL_SCOPES)
            return svc.users().getProfile(userId="me").execute().get("emailAddress", "")
        except Exception:
            svc = service("calendar", "v3", CAL_SCOPES)
            return svc.calendarList().get(calendarId="primary").execute().get("id", "")
    except Exception:
        return ""
    finally:
        _active_path.set(prev)


if __name__ == "__main__":
    connect()
