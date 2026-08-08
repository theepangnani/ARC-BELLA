#!/usr/bin/env python3
"""
Zero-setup media: open YouTube and Spotify to what you asked for, by driving
the browser and the Spotify desktop app. No accounts, no API keys.

This is the honest ceiling without the platform APIs: it can OPEN and start a
search (and on Spotify, hand a track/playlist straight to the app), but it
cannot pause/skip/queue — that needs the Spotify Web API and a Premium account.
For a free account, opening the app to the right place and tapping play is the
realistic path, and this does that by voice.

Localhost only: opening a browser or app on a deployed server is meaningless,
so connected() is False in CLOUD mode.
"""

import os
import re
import webbrowser
import urllib.parse

CLOUD = bool(os.getenv("PORT"))


def connected() -> bool:
    return not CLOUD


def _q(s: str) -> str:
    return urllib.parse.quote_plus((s or "").strip())


def youtube(query: str) -> str:
    """Open a YouTube search for the query in the browser."""
    q = (query or "").strip()
    if not q:
        return "What should I search for on YouTube?"
    webbrowser.open(f"https://www.youtube.com/results?search_query={_q(q)}")
    return (f"Opening YouTube for {q}. It'll show the results — tap the first to play. "
            f"(For me to play the top result directly, the YouTube Data API needs setting up.)")


def spotify(query: str) -> str:
    """Open Spotify to a search for the query, preferring the desktop app."""
    q = (query or "").strip()
    if not q:
        return "What should I play on Spotify?"
    # The spotify: URI opens the desktop app straight to the search; if the app
    # isn't registered the OS/browser falls back to the web player.
    opened = False
    try:
        opened = webbrowser.open(f"spotify:search:{urllib.parse.quote(q)}")
    except Exception:
        opened = False
    if not opened:
        webbrowser.open(f"https://open.spotify.com/search/{_q(q)}")
    return (f"Opening Spotify for {q} — it'll land on the search in your app; hit play. "
            f"(On a free account I can't press play for you; that needs Premium and the Spotify API.)")


TOOLS = [
    {"name": "youtube",
     "description": "Open YouTube to a search for a song, video, or topic in the user's browser. Use for 'play X on YouTube', 'find the video for Y', 'pull up Z on YouTube'.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "spotify",
     "description": "Open Spotify (desktop app) to a search for a song, artist, album, or playlist. Use for 'play X on Spotify', 'put on some Y'. Opens it ready to play; on a free account the user taps play.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]

_DISPATCH = {"youtube": youtube, "spotify": spotify}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    if CLOUD:
        return "Media control is disabled on a deployed instance.", True
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
