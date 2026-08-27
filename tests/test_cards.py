# -*- coding: utf-8 -*-
"""The floating cards: weather, markets, and the two new ones.

TODAY is the rest of your day beside the markets. It is not upcoming_events with
a larger number — that function exists to nudge you before a meeting, so it
skips all-day entries and anything already started, and both of those are
exactly what an agenda has to show. "Leave for the airport" is on your day
whether or not it has a clock on it, and a thing that began ten minutes ago is
the most relevant row on the panel.

NOW PLAYING is read from the players' own window titles, because Windows already
writes it there. Listening to the speakers to work out something the operating
system could be asked is a research project standing in for a lookup — and it
would want the microphone, which is busy. The tests that matter are the
NEGATIVE ones: a spreadsheet open in Chrome is not music, and a document called
"Bohemian Rhapsody" is not music either.

Both cards hide themselves when they have nothing to say. A card reading "no
events" all evening is one you stop seeing, and then it is no use on the morning
it finally has something.
"""
import io
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402
import pc        # noqa: E402
import gcal      # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

print("Four cards, one set of chrome:")
# They were two identical-but-separate rules. A third copy is the point at which
# they start drifting apart by a pixel each.
c.truthy("  the border, blur and shadow are written once",
         ".forecast, .stocks, .agenda, .nowplaying {" in page)
c.truthy("  and only POSITION differs per card",
         ".agenda    { top: 60px;" in page and ".stocks { top: 262px;" in page)
c.truthy("  the new ones mirror the old two across the stage",
         "left: calc(clamp(180px, 13vw, 250px) + 34px); }" in page)
c.truthy("  chat mode hides all four", "html.chat .agenda," in page
         and "html.chat .nowplaying," in page)

print("\nToday: what an agenda must show that a nudge must not:")
src = io.open(ARC / "gcal.py", encoding="utf-8").read()
c.truthy("  there is a separate function for it", "def agenda(" in src)
# From the start of today, not from now — something running since nine is still
# on your day at ten.
c.truthy("  it looks back to the start of today", "day0 = now.replace(hour=0" in src)
c.truthy("  ...so a meeting already under way still shows", '"started": mins < 0' in src)
c.truthy("  and is dropped only once it is genuinely over",
         "if fin and _parse(fin) < now:" in src)
c.truthy("  all-day entries are included", '"at": "all day"' in src)
# Tomorrow's all-day would otherwise sit at the top of the panel all afternoon
# claiming to be next.
c.truthy("  ...but only today's", 'st["date"] != now.strftime' in src)
c.truthy("  the ordering is stated rather than left to a sentinel",
         'key=lambda r: (0 if r["all_day"] else 1' in src)

print("\nNow playing: the negatives are the whole test:")
pc.IS_WIN = True
_real_windows, _real_exe = pc._windows, pc._exe_of


def playing(exe, title):
    pc._windows = lambda: [(1, title)]
    pc._exe_of = lambda h: exe
    return pc.now_playing()


for exe, title, want, why in [
    ("spotify.exe", "Daft Punk - Around the World", True,  "a track, artist split off"),
    ("spotify.exe", "Spotify Premium",              False, "its own name means stopped"),
    ("spotify.exe", "Spotify",                      False, "so does the bare name"),
    ("chrome.exe",  "lofi beats - YouTube - Google Chrome", True, "a tab that is music"),
    ("chrome.exe",  "Quarterly budget.xlsx - Google Chrome", False,
     "A SPREADSHEET IS NOT MUSIC"),
    ("firefox.exe", "Song Name - SoundCloud - Mozilla Firefox", True, "another site"),
    ("vlc.exe",     "interstellar.mkv - VLC media player", True, "a file"),
    ("vlc.exe",     "VLC media player",             False, "idle"),
    ("notepad.exe", "Bohemian Rhapsody",            False,
     "A DOCUMENT IS NOT MUSIC, whatever it is called"),
]:
    c("  %-42s %s" % ("%r" % title[:40], why), playing(exe, title)["playing"], want)

got = playing("spotify.exe", "Daft Punk - Around the World")
c("  the artist is separated", got["artist"], "Daft Punk")
c("  ...from the song", got["title"], "Around the World")
c("  and the player is named", got["source"], "Spotify")
# A browser tab is "Song - Site", not "Artist - Song", so splitting it the same
# way would file the site as the artist.
got = playing("chrome.exe", "lofi beats - YouTube - Google Chrome")
c("  a browser tab keeps its whole title as the song", got["title"], "lofi beats")
c("  ...with no invented artist", got["artist"], "")
pc._windows, pc._exe_of = _real_windows, _real_exe

print("\nBoth routes behave:")
with TestClient(run.app) as client:
    sid = session.create("owner@example.com", "browser")
    CK = {run.COOKIE: sid}
    for path in ("/api/calendar/agenda", "/api/nowplaying"):
        c("  %-24s needs a session" % path, client.get(path).status_code, 401)
        c("  %-24s answers one" % path, client.get(path, cookies=CK).status_code, 200)
        # Polled on a timer with nobody necessarily there. Left out of this set,
        # a card sitting on screen would hold a session alive for ever.
        c("  %-24s does not refresh the idle clock" % path,
          path in run.BACKGROUND_PATHS, True)
    # An unlinked calendar is the ordinary case, not a fault. A card that shows
    # an error when you simply have not connected Google trains you to ignore it.
    d = client.get("/api/calendar/agenda", cookies=CK).json()
    c("  no calendar is an empty day, not an error", d.get("events"), [])
    c("  ...and says which it is", "connected" in d, True)
    # Reading the desktop over the tunnel would report a room the caller is not
    # in, and the phone showing it as its own is a small lie.
    c("  now playing is refused to a remote caller",
      client.get("/api/nowplaying", cookies=CK).json().get("remote"), True)
    session.revoke_all()

print("\nAnd the cards are built as nodes, like everything else here:")
for name, start, end in [
    ("agenda", "  /* ---------------- the agenda card", "  /* ---------------- now playing"),
    ("now playing", "  /* ---------------- now playing", "  /* ---------------- override"),
]:
    blk = body[body.index(start):body.index(end)]
    # An event title is text somebody else wrote — an invitation from a stranger
    # is the ordinary case — and this renders it on the owner's screen.
    c("  %-11s never parses a value as markup" % name,
      len(re.findall(r"\.innerHTML\s*=", blk)), 0)
    c.truthy("  %-11s hides itself when empty" % name, "hidden = true" in blk)
c.truthy("  the agenda polls slowly, being a Google call", "300000" in body)
c.truthy("  ...and now playing faster, being local", "10000" in body)
# Rebuilding a card that has not changed makes it flicker every ten seconds.
c.truthy("  the track is only redrawn when it changes", "key === npLast" in body)

c.done()
