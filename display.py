#!/usr/bin/env python3
"""
ARC's second-screen display board.

A glanceable dashboard meant to live on a second monitor. ARC can push a heading
and some text to it with show_on_display ("put the recipe on my second screen");
the /display page polls the board and shows whatever is current. Purely in-memory
and local — nothing is saved to disk.
"""

import time

_board = {"title": "", "text": "", "ts": 0.0}


def connected() -> bool:
    # No external dependency — the display board is always available.
    return True


def show_on_display(title: str = "", text: str = "") -> str:
    """Show information on the user's second-screen display."""
    t = (title or "").strip()[:100]
    b = (text or "").strip()[:4000]
    if not t and not b:
        return "Nothing to show — give a title or some text."
    _board.update(title=t, text=b, ts=time.time())
    shown = t or (b[:40] + ("…" if len(b) > 40 else ""))
    return f"Showing on the second screen: {shown}"


def clear_display() -> str:
    """Clear whatever is on the second-screen display."""
    _board.update(title="", text="", ts=time.time())
    return "Cleared the second-screen display."


def get_board() -> dict:
    return dict(_board)


TOOLS = [
    {"name": "show_on_display",
     "description": ("Show information on the user's SECOND-SCREEN display — a glanceable dashboard on their second "
                     "monitor. Use when they ask to 'put/show X on the second screen / second monitor / display' "
                     "(a recipe, a list, directions, notes, a summary). 'title' is a short heading; 'text' is the body."),
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "text": {"type": "string"}}}},
    {"name": "clear_display",
     "description": "Clear whatever ARC is showing on the second-screen display.",
     "input_schema": {"type": "object", "properties": {}}},
]

_DISPATCH = {"show_on_display": show_on_display, "clear_display": clear_display}


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
