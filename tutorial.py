#!/usr/bin/env python3
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""
Whether somebody has been shown round yet.

The first time a person signs in, the page walks them through what is on the
screen — the ring, the text box, modes, the voice, panels, their Google
account — so they are not left wandering a HUD full of readouts wondering which
of it does anything. Once they finish it, or skip it, it does not come back.

Kept on the server, per account, rather than in the browser. The browser was the
obvious place and the wrong one: the same person on their phone, on a second
computer, or after clearing site data would be walked round again as a
stranger, and two people sharing one computer would share one "seen it". The
page keeps a copy in localStorage only for when this is unreachable.

Per account through whose.py, like notes and alarms, so a guest finishing the
tour does not finish it for the owner.

VERSION is what "done" means. A small change to the wording should not show
everybody the tour again; a tour that has grown something people genuinely need
to be shown can bump this, and everyone who finished an older one sees it once.
"""

import json
import os
import time
from pathlib import Path

import storefile
import whose

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "tutorial.json"

VERSION = 1
HOW = ("finished", "skipped")


def _raw():
    """For reading: a damaged or busy-too-long file shows as nothing. Saving
    reads strictly instead (storefile.py), so nothing is written over it."""
    try:
        return storefile.shaped(storefile.read(STORE, dict))
    except storefile.Unreadable:
        return {}


def status() -> dict:
    """This account's answer: has it seen the current tour, and how did it end."""
    rows = [r for r in whose.mine(_raw()) if isinstance(r, dict)]
    rec = rows[-1] if rows else {}
    try:
        seen = int(rec.get("version") or 0)
    except (TypeError, ValueError):
        seen = 0          # a hand-edited or damaged record reads as "not seen"
    return {"done": seen >= VERSION, "version": VERSION, "seen": seen,
            "how": rec.get("how") if seen >= VERSION else None}


def mark(how: str) -> dict:
    """Record that this account finished or skipped the tour. Idempotent."""
    how = how if how in HOW else "finished"
    try:
        with storefile.lock(STORE):
            blob = storefile.shaped(storefile.read(STORE, dict))
            storefile.write(STORE, whose.replace(
                blob, [{"version": VERSION, "how": how, "at": time.time()}]))
    except (storefile.Unreadable, OSError):
        pass            # the page keeps its own note; nothing else depends on it
    return status()
