#!/usr/bin/env python3
"""
Quick notes for ARC.

"Take a note: pick up milk" captures a line; "read my notes" reads them back;
"delete note 2" removes one. Stored in notes.json next to the app — plain, local,
and easy to back up.
"""

import os
import json
import time
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
# Per-instance data dir (see run.py); a second Bella keeps its own notes.
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
NOTES = DATA_DIR / "notes.json"
MAX_NOTES = 500


def connected() -> bool:
    return True


def _load() -> list:
    try:
        data = json.loads(NOTES.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items) -> None:
    try:
        NOTES.write_text(json.dumps(items[-MAX_NOTES:], ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception:
        pass


def add_note(text: str = "") -> str:
    """Save a quick note."""
    t = (text or "").strip()
    if not t:
        return "There's nothing to note — tell me what to write down."
    items = _load()
    items.append({"id": str(int(time.time() * 1000)), "text": t[:1000], "ts": time.time()})
    _save(items)
    return f"Noted: {t[:120]}"


def _ago(ts: float) -> str:
    try:
        d = dt.datetime.now() - dt.datetime.fromtimestamp(ts)
        s = int(d.total_seconds())
        if s < 3600:
            return f"{max(1, s // 60)}m ago"
        if s < 86400:
            return f"{s // 3600}h ago"
        return f"{s // 86400}d ago"
    except Exception:
        return ""


def list_notes() -> str:
    """Read back all saved notes, newest last."""
    items = _load()
    if not items:
        return "You don't have any notes saved."
    lines = [f"You have {len(items)} note(s):"]
    for i, n in enumerate(items, 1):
        when = _ago(n.get("ts", 0))
        lines.append(f"{i}. {n.get('text', '')}" + (f"  ({when})" if when else ""))
    return "\n".join(lines)


def delete_note(which: str = "") -> str:
    """Delete a note by its number (from list_notes) or by matching text. Pass
    'all' to clear every note."""
    items = _load()
    if not items:
        return "There are no notes to delete."
    w = str(which or "").strip().lower()
    if w in ("all", "everything", "*"):
        _save([])
        return "Cleared all your notes."
    # by number
    if w.isdigit():
        idx = int(w) - 1
        if 0 <= idx < len(items):
            gone = items.pop(idx)
            _save(items)
            return f"Deleted note: {gone.get('text', '')[:120]}"
        return f"There's no note number {w}."
    # by text match
    hit = next((n for n in items if w and w in n.get("text", "").lower()), None)
    if hit:
        items.remove(hit)
        _save(items)
        return f"Deleted note: {hit.get('text', '')[:120]}"
    return "I couldn't find a note matching that."


def notes_text() -> str:
    """Plain text of all notes, for showing on the second screen."""
    items = _load()
    return "\n".join("• " + n.get("text", "") for n in items)


TOOLS = [
    {"name": "add_note",
     "description": ("Save a quick note for the user. Use for 'take a note', 'note that…', 'remember this "
                     "as a note', 'jot down…'. 'text' is what to write down."),
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "list_notes",
     "description": "Read back the user's saved notes. Use for 'read my notes', 'what are my notes', 'show my notes'.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "delete_note",
     "description": ("Delete a saved note by its number (from list_notes) or by matching text; 'all' clears "
                     "every note."),
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": ["which"]}},
]

_DISPATCH = {"add_note": add_note, "list_notes": list_notes, "delete_note": delete_note}


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
