# -*- coding: utf-8 -*-
"""Two turns needing Google at once refresh the token once, and never tear it.

Tools are moving off the event loop, so two requests can reach gauth.service()
at the same instant with an expired token. Before: both refreshed, and both
wrote the file with write_text, which a third reader could catch half-written
and report as "the sign-in has lapsed". Now: one refresh under the token
file's lock, the file read again once the lock is held, and an atomic write.
(Claude 2 found the same race in links.py, fixed in 2d25ab5.)
"""
import json
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
DATA = sandbox()

import gauth   # noqa: E402

c = Check()
refreshes = []


class Creds:
    def __init__(self, blob):
        self.blob = dict(blob)
        self.refresh_token = blob.get("refresh_token")

    @classmethod
    def from_authorized_user_file(cls, path, scopes):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @property
    def valid(self):
        return self.blob.get("token") == "fresh"

    @property
    def expired(self):
        return not self.valid

    def refresh(self, request):
        time.sleep(0.2)                  # long enough for the others to pile up
        refreshes.append(threading.get_ident())
        self.blob["token"] = "fresh"

    def to_json(self):
        return json.dumps(self.blob)


def fake(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


fake("google")
fake("google.oauth2")
fake("google.oauth2.credentials", Credentials=Creds)
fake("google.auth")
fake("google.auth.transport")
fake("google.auth.transport.requests", Request=lambda: None)
fake("googleapiclient")
fake("googleapiclient.discovery", build=lambda api, ver, credentials=None, cache_discovery=False: credentials)

tok = DATA / "token-under-test.json"
tok.write_text(json.dumps({"token": "stale", "refresh_token": "r", "scopes": gauth.SCOPES}), encoding="utf-8")
gauth._tok = lambda: tok
gauth.granted_scopes = lambda: set(gauth.SCOPES)

got, errors = [], []
gate = threading.Barrier(4)


def turn():
    try:
        gate.wait()
        got.append(gauth.service("calendar", "v3").blob["token"])
    except Exception as e:
        errors.append(repr(e))


def reader():
    # Asks what the token grants, over and over, while the refresh happens.
    # A read caught mid-write (or, on Windows, mid-swap) comes back as no
    # scopes at all, which the chips show as "not connected".
    gate.wait()
    end = time.time() + 0.6
    while time.time() < end:
        if gauth.granted_scopes() != set(gauth.SCOPES):
            errors.append("a read saw no scopes")
            break


ts = [threading.Thread(target=turn) for _ in range(3)] + [threading.Thread(target=reader)]
[t.start() for t in ts]
[t.join(10) for t in ts]

print("Three turns with an expired Google token at once:")
c("  nothing failed", errors, [])
c("  Google was asked to refresh exactly once", len(refreshes), 1)
c("  every turn got the fresh token", got, ["fresh"] * 3)
c("  the file holds the fresh token", json.loads(tok.read_text(encoding="utf-8"))["token"], "fresh")
c("  no temporary file was left behind", [p.name for p in DATA.glob("token-under-test.json.*.tmp")], [])

c.done()
