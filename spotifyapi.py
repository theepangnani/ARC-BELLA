#!/usr/bin/env python3
"""Spotify, through the account the person linked (links.py).

media.py already opens Spotify to a search, with no account at all, and says
plainly that it cannot press play. This is the other half: what is playing,
what they played, their playlists and top tracks, and — on a Premium account,
with the consent gate's say-so — play, pause, skip and queue.

Reads are passive. Everything that changes what is coming out of their
speakers is an action, gated like any other, because "skip" misheard from the
television is a small thing and a playlist started at full volume at 2am is
not. Guests are lent none of it.

What comes back is a song title, an artist, a playlist name: words somebody
else wrote. They are data like an email subject, and the turn is marked for
lessons like any other outside read.
"""

import httpx

import links

API = "https://api.spotify.com/v1"
SID = "spotify"


def connected() -> bool:
    return links.linked(SID)


def _call(method: str, path: str, params=None, json=None, request=None):
    tok = links.token(SID)
    request = request or httpx.request
    r = request(method, API + path, params=params, json=json,
                headers={"Authorization": "Bearer " + tok}, timeout=links.TIMEOUT)
    if r.status_code == 401:
        raise links.NotLinked("Spotify turned the link down. Link it again in Connectors.")
    if r.status_code == 403:
        return None, "That needs Spotify Premium — on a free account I can see what's playing but can't control it."
    if r.status_code == 404:
        return None, "Spotify isn't playing on any device right now. Open it somewhere and try again."
    if r.status_code == 429:
        return None, "Spotify is asking me to slow down. Try again in a moment."
    if r.status_code >= 400:
        return None, "Spotify said no (%s)." % r.status_code
    if r.status_code == 204 or not r.content:
        return {}, ""
    return r.json(), ""


def _track(t: dict) -> str:
    if not t:
        return "something"
    artists = ", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name"))
    return "%s by %s" % (t.get("name", "?"), artists) if artists else t.get("name", "?")


def now_playing() -> str:
    d, err = _call("GET", "/me/player/currently-playing")
    if err:
        return err
    if not d or not d.get("item"):
        return "Nothing is playing on Spotify."
    state = "Playing" if d.get("is_playing") else "Paused on"
    return "%s %s." % (state, _track(d["item"]))


def recent(limit: int = 10) -> str:
    d, err = _call("GET", "/me/player/recently-played", params={"limit": max(1, min(int(limit or 10), 30))})
    if err:
        return err
    items = (d or {}).get("items") or []
    if not items:
        return "No recent plays."
    return "Recently played: " + "; ".join(_track(i.get("track")) for i in items) + "."


def top(kind: str = "tracks", period: str = "short") -> str:
    kind = "artists" if str(kind).startswith("artist") else "tracks"
    rng = {"short": "short_term", "month": "short_term", "medium": "medium_term",
           "long": "long_term", "all": "long_term"}.get(str(period), "short_term")
    d, err = _call("GET", "/me/top/" + kind, params={"limit": 10, "time_range": rng})
    if err:
        return err
    items = (d or {}).get("items") or []
    if not items:
        return "Spotify doesn't have enough listening yet to say."
    names = [i.get("name", "?") if kind == "artists" else _track(i) for i in items]
    return "Top %s: %s." % (kind, "; ".join(names))


def playlists() -> str:
    d, err = _call("GET", "/me/playlists", params={"limit": 30})
    if err:
        return err
    items = (d or {}).get("items") or []
    if not items:
        return "No playlists."
    return "Playlists: " + "; ".join(
        "%s [uri:%s]" % (p.get("name", "?"), p.get("uri", "")) for p in items if p) + "."


def search(query: str = "", kind: str = "track") -> str:
    q = str(query or "").strip()
    if not q:
        return "Search Spotify for what?"
    kind = kind if kind in ("track", "artist", "album", "playlist") else "track"
    d, err = _call("GET", "/search", params={"q": q, "type": kind, "limit": 5})
    if err:
        return err
    items = ((d or {}).get(kind + "s") or {}).get("items") or []
    if not items:
        return "Nothing on Spotify for '%s'." % q
    rows = [("%s [uri:%s]" % (_track(i) if kind == "track" else i.get("name", "?"), i.get("uri", "")))
            for i in items if i]
    return "On Spotify: " + "; ".join(rows) + "."


def play(uri: str = "", query: str = "") -> str:
    body = None
    uri = str(uri or "").strip()
    if not uri and str(query or "").strip():
        d, err = _call("GET", "/search", params={"q": query, "type": "track", "limit": 1})
        if err:
            return err
        items = ((d or {}).get("tracks") or {}).get("items") or []
        if not items:
            return "Nothing on Spotify for '%s'." % query
        uri = items[0].get("uri", "")
    if uri.startswith("spotify:track:"):
        body = {"uris": [uri]}
    elif uri.startswith("spotify:"):
        body = {"context_uri": uri}
    elif uri:
        return "That isn't a Spotify link I can play."
    _, err = _call("PUT", "/me/player/play", json=body)
    return err or ("Playing." if body else "Resumed.")


def pause() -> str:
    _, err = _call("PUT", "/me/player/pause")
    return err or "Paused."


def skip(direction: str = "next") -> str:
    back = str(direction).lower() in ("previous", "back", "prev")
    _, err = _call("POST", "/me/player/previous" if back else "/me/player/next")
    return err or ("Back one." if back else "Skipped.")


def queue(uri: str = "") -> str:
    if not str(uri).startswith("spotify:track:"):
        return "Give me a track to queue — search for it first."
    _, err = _call("POST", "/me/player/queue", params={"uri": uri})
    return err or "Queued."


def volume(percent: int = 50) -> str:
    p = max(0, min(int(percent), 100))
    _, err = _call("PUT", "/me/player/volume", params={"volume_percent": p})
    return err or "Spotify volume %d." % p


def remember_account() -> None:
    """After linking: the display name for the sheet. Never the token."""
    d, err = _call("GET", "/me")
    if not err and d:
        links.set_account(SID, d.get("display_name") or d.get("id") or "")


READS = {"spotify_now_playing", "spotify_recent", "spotify_top", "spotify_playlists", "spotify_search"}

TOOLS = [
    {"name": "spotify_now_playing",
     "description": "What is playing on the user's Spotify right now. Use for 'what's this song', 'what's playing'.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spotify_recent",
     "description": "What the user played recently on Spotify.",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer"}}}},
    {"name": "spotify_top",
     "description": "The user's most-played tracks or artists on Spotify (period: short = about a month, medium = six months, long = years).",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["tracks", "artists"]},
         "period": {"type": "string", "enum": ["short", "medium", "long"]}}}},
    {"name": "spotify_playlists",
     "description": "The user's Spotify playlists, each with a [uri:...] for spotify_play. Never read a uri aloud.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spotify_search",
     "description": "Search Spotify for a track, artist, album or playlist. Returns [uri:...] for spotify_play or spotify_queue; never read one aloud.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string"},
         "kind": {"type": "string", "enum": ["track", "artist", "album", "playlist"]}},
         "required": ["query"]}},
    {"name": "spotify_play",
     "description": ("Play on the user's Spotify (Premium only): a uri from spotify_search or "
                     "spotify_playlists, or a query to play the best match, or nothing to resume. "
                     "Prefer this over the plain 'spotify' tool when Spotify is linked."),
     "input_schema": {"type": "object", "properties": {
         "uri": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "spotify_pause", "description": "Pause the user's Spotify.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "spotify_skip", "description": "Skip to the next track, or 'previous' to go back.",
     "input_schema": {"type": "object", "properties": {
         "direction": {"type": "string", "enum": ["next", "previous"]}}}},
    {"name": "spotify_queue", "description": "Add a track (uri from spotify_search) to the user's Spotify queue.",
     "input_schema": {"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]}},
    {"name": "spotify_volume", "description": "Set the Spotify player's volume, 0 to 100.",
     "input_schema": {"type": "object", "properties": {"percent": {"type": "integer"}}, "required": ["percent"]}},
]

_DISPATCH = {"spotify_now_playing": now_playing, "spotify_recent": recent, "spotify_top": top,
             "spotify_playlists": playlists, "spotify_search": search, "spotify_play": play,
             "spotify_pause": pause, "spotify_skip": skip, "spotify_queue": queue,
             "spotify_volume": volume}


def run_tool(name: str, args: dict) -> tuple:
    fn = _DISPATCH.get(name)
    if not fn:
        return "No such tool: %s" % name, True
    try:
        return str(fn(**(args or {}))), False
    except links.NotLinked as e:
        return str(e), True
    # ValueError too: int("loud") from the model, or a non-JSON 200 from the
    # service, escaped dispatch_tool and ended the whole turn (Claude 4's review).
    except (TypeError, ValueError) as e:
        return "Wrong arguments for %s: %s" % (name, e), True
    except httpx.HTTPError as e:
        return "Couldn't reach Spotify: %s" % type(e).__name__, True
    # Anything else — an argument of a shape nobody expected, an answer of a
    # shape Spotify never documented — fails this one tool, never the turn.
    # The type only: an exception's text can carry the request, token and all.
    except Exception as e:
        return "Spotify didn't work (%s)." % type(e).__name__, True
