#!/usr/bin/env python3
"""
Phone push notifications for ARC, via ntfy.

ntfy (https://ntfy.sh) needs no account and no key: you pick a hard-to-guess
topic name, the phone app subscribes to it, and anything POSTed to that topic
pops up as a notification. ARC uses it to reach the owner's phone even when the
browser tab is closed — a fired reminder, or anything ARC is told to "send to my
phone".

Setup (owner, once):
  1. Install the "ntfy" app; subscribe to a private topic, e.g. arc-a7f3k9zq.
  2. Put that topic in .env:  ARC_NTFY_TOPIC=arc-a7f3k9zq
     (optionally ARC_NTFY_SERVER=https://ntfy.sh for a self-hosted server)
  3. Restart ARC.

Because the topic is a single owner-configured secret, pushes only ever reach the
owner — never a visitor who signed in with their own Google. That's deliberate.
"""

import os
import urllib.request

NTFY_SERVER = os.getenv("ARC_NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
NTFY_TOPIC = os.getenv("ARC_NTFY_TOPIC", "").strip()


def configured() -> bool:
    return bool(NTFY_TOPIC)


# So the module plugs into the same TOOLKITS machinery: the notify_phone tool is
# only offered when push is actually set up.
def connected() -> bool:
    return configured()


def _ascii(s: str) -> str:
    # HTTP header values must be latin-1; strip anything that isn't so a stray
    # emoji in a title can never take the whole push down.
    return (s or "").encode("ascii", "ignore").decode("ascii").strip()


def send(message: str, title: str = "ARC", tags: str = "bell", priority=None) -> bool:
    """POST one notification. Returns True on a 2xx. Never raises."""
    if not configured():
        return False
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    body = (message or "").encode("utf-8")           # body is UTF-8, emoji fine
    req = urllib.request.Request(url, data=body, method="POST")
    t = _ascii(title)
    if t:
        req.add_header("Title", t)
    if tags:
        req.add_header("Tags", _ascii(tags))
    if priority:
        req.add_header("Priority", str(priority))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False


def notify_phone(message: str = "", title: str = "") -> str:
    """Tool entry point: push a message to the user's phone."""
    if not configured():
        return ("Phone alerts aren't set up. The owner needs to set ARC_NTFY_TOPIC "
                "in .env and subscribe to that topic in the ntfy app.")
    msg = (message or "").strip()
    if not msg:
        return "What should I send to your phone?"
    ok = send(msg, title=title or "ARC")
    return "Sent that to your phone, sir." if ok else "I couldn't reach the phone alert service."


TOOLS = [
    {"name": "notify_phone",
     "description": ("Send a push notification to the user's phone. Use when they ask you to "
                     "'text/ping/alert/notify my phone', 'send that to my phone', or when you "
                     "want to reach them and the page may be closed. 'message' is the alert text; "
                     "'title' is an optional short heading."),
     "input_schema": {"type": "object",
                      "properties": {"message": {"type": "string"}, "title": {"type": "string"}},
                      "required": ["message"]}},
]

_DISPATCH = {"notify_phone": notify_phone}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
