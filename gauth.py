#!/usr/bin/env python3
"""
One Google sign-in, shared by every tool that needs it.

    python gauth.py       # sign in / re-consent after a scope change

Both gcal.py and gmail.py go through here so there is a single token file and
a single consent screen, rather than one per capability.
"""

from pathlib import Path

ROOT = Path(__file__).parent.resolve()
CREDENTIALS = ROOT / "credentials.json"
TOKEN = ROOT / "token.json"

# Deliberately the narrowest set that does the job:
#   calendar        read and write events
#   gmail.readonly  read mail — cannot write or send
#   gmail.compose   create/edit drafts and send them — cannot delete mail
# Notably absent: gmail.modify, which would let ARC label, archive and bin
# things. Nothing asked for that, so it isn't granted.
CAL_SCOPES = ["https://www.googleapis.com/auth/calendar"]
MAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
CONTACTS_SCOPES = ["https://www.googleapis.com/auth/contacts.readonly"]
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
SCOPES = CAL_SCOPES + MAIL_SCOPES + CONTACTS_SCOPES + DRIVE_SCOPES


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
        return set(json.loads(TOKEN.read_text(encoding="utf-8")).get("scopes") or [])
    except Exception:
        return set()


def has(needs) -> bool:
    """Is this particular capability authorised? Calendar can work while mail doesn't."""
    return TOKEN.exists() and set(needs).issubset(granted_scopes())


def connected() -> bool:
    return TOKEN.exists()


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

    if not TOKEN.exists():
        raise NotConnected("Google is not connected yet. Run: python gauth.py")

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)

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
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise NotConnected("The Google sign-in has lapsed. Run: python gauth.py")

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


if __name__ == "__main__":
    connect()
