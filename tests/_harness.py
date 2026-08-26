# -*- coding: utf-8 -*-
"""Shared plumbing for the suites in this directory.

Two jobs. The first is finding ARC without anyone hard-coding a path, so the
suites run from any checkout on any machine.

The second matters more. These suites call `revoke_all()`, delete Google token
files, write alarms and clear reminders. Pointed at a real install they would
sign the owner out of their own assistant, delete the refresh token behind it
and cancel whatever alarm was set for the morning — from a command whose whole
promise is that it changes nothing. So every suite runs against a fresh
temporary directory, and `sandbox()` refuses to start if anything has aimed
ARC_DATA_DIR at a real one.
"""

import atexit
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ARC = TESTS.parent                       # the arc/ package directory
HUD = ARC / "static" / "index.html"      # the whole browser client, one file

_tmp = None


def _looks_live(path: Path) -> bool:
    """Is this somebody's actual ARC data, rather than a scratch directory?

    Deliberately generous about what counts as live: a false positive costs a
    confusing error message, a false negative costs somebody their session
    store, their alarms and their notes.
    """
    if path == ARC or path == ARC.parent:
        return True
    live = ("sessions.json", "alarms.json", "token.json", "credentials_web.json",
            "reminders.json", "notes.json", "memory.json")
    return any((path / name).exists() for name in live)


def sandbox() -> Path:
    """Point ARC at a throwaway data directory, and make it importable.

    Returns the directory, already created and emptied at exit. Call before
    importing `run`, `session` or any toolkit — they read ARC_DATA_DIR at
    import time and never look again.
    """
    global _tmp
    if _tmp is not None:
        return _tmp

    asked = os.environ.get("ARC_DATA_DIR")
    if asked and _looks_live(Path(asked).resolve()):
        sys.stderr.write(
            "\n  REFUSING TO RUN: ARC_DATA_DIR points at live ARC data\n"
            "      %s\n"
            "  These tests revoke sessions and delete tokens. Unset it.\n\n" % asked)
        raise SystemExit(2)

    _tmp = Path(tempfile.mkdtemp(prefix="arc-test-"))
    os.environ["ARC_DATA_DIR"] = str(_tmp)
    # A key must exist for the Anthropic client to be constructed at all; no
    # test ever reaches the network with it.
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-a-real-one")
    # Never inherit the developer's own allowlist: a suite that signed in as a
    # real address would be testing their .env, not the code.
    os.environ.setdefault("ARC_ALLOWED_EMAILS", "owner@example.com")
    os.environ.setdefault("ARC_GUEST_EMAILS", "guest@example.com")
    os.environ.setdefault("ARC_SECRET", "0" * 64)

    if str(ARC) not in sys.path:
        sys.path.insert(0, str(ARC))
    os.chdir(ARC)                        # run.py resolves static/ from cwd

    atexit.register(lambda: shutil.rmtree(_tmp, ignore_errors=True))
    return _tmp


def fake_web_credentials():
    """Hand gauth a throwaway Google web client, in the sandbox.

    Two reasons, and the second is the important one. First, credentials_web.json
    is gitignored, so a fresh clone and every CI runner is without it and the
    sign-in flow could not be exercised at all. Second, when it IS present the
    suites were building authorization URLs from the developer's own client id
    — testing their Google project rather than this code, and differing between
    machines. Neither is a thing to leave to chance in a login test.

    Nothing here is a credential. The flow is built and inspected locally; no
    request is ever made to Google.
    """
    import json
    import gauth
    p = sandbox() / "credentials_web.json"
    p.write_text(json.dumps({"web": {
        "client_id": "000000000000-arctests.apps.googleusercontent.com",
        "project_id": "arc-tests",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_secret": "not-a-secret-test-placeholder",
        "redirect_uris": ["http://localhost:8420/oauth/callback"],
    }}), encoding="utf-8")
    gauth.WEB_CREDENTIALS = p            # read at call time, so this takes
    return p


def prompt_text(name: str = "main") -> str:
    """ARC's own instructions, wherever they currently live.

    They used to be a template literal inside index.html, so suites asked
    "is this rule in the page?". They now live in prompts/*.md, server-side,
    where a browser cannot edit them. The question the suites are actually
    asking — does ARC's prompt still say this — has not changed, so it gets a
    helper rather than a path each of them has to know.
    """
    return io.open(ARC / "prompts" / ("%s.md" % name), encoding="utf-8").read()


def system_text(sent) -> str:
    """Flatten what was sent as `system` into one string.

    It is a list of blocks now, not a string: the server's own text first and
    marked cacheable, the client's contribution second. A suite checking that
    some instruction reached the model should not have to care which block it
    landed in — but the ORDER matters and is checked directly in test_prompt.
    """
    if isinstance(sent, dict):
        sent = sent.get("system")
    if isinstance(sent, str):
        return sent
    return "\n\n".join(b.get("text", "") for b in (sent or [])
                       if isinstance(b, dict))


class Check:
    """The reporting the suites already used, in one place.

    Prints as it goes rather than collecting: when a suite dies half way
    through, the output up to that point is the useful part.
    """

    def __init__(self):
        self.ok = True
        self.run = 0

    def __call__(self, label, got, want):
        good = got == want
        self.run += 1
        self.ok = self.ok and good
        print(("  PASS  " if good else "  FAIL  ") + label +
              ("" if good else "\n          got  %r\n          want %r" % (got, want)))
        return good

    def truthy(self, label, got):
        return self(label, bool(got), True)

    def done(self):
        print("\nALL PASS (%d checks)" % self.run if self.ok else "\nFAILURES ABOVE")
        raise SystemExit(0 if self.ok else 1)
