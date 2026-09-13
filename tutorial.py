#!/usr/bin/env python3
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

import whose

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
STORE = DATA_DIR / "tutorial.json"

VERSION = 1
HOW = ("finished", "skipped")


def _raw():
    try:
        blob = json.loads(STORE.read_text(encoding="utf-8"))
        return blob if isinstance(blob, (list, dict)) else {}
    except Exception:
        return {}


def status() -> dict:
    """This account's answer: has it seen the current tour, and how did it end."""
    rec = (whose.mine(_raw()) or [{}])[-1]
    seen = int(rec.get("version") or 0)
    return {"done": seen >= VERSION, "version": VERSION, "seen": seen,
            "how": rec.get("how") if seen >= VERSION else None}


def mark(how: str) -> dict:
    """Record that this account finished or skipped the tour. Idempotent."""
    how = how if how in HOW else "finished"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        blob = whose.replace(_raw(), [{"version": VERSION, "how": how, "at": time.time()}])
        tmp = STORE.with_name(STORE.name + ".tmp")
        tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STORE)          # atomic, like every other store here
    except Exception:
        pass
    return status()
