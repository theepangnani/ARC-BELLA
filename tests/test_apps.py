# -*- coding: utf-8 -*-
"""Opening apps, switching windows, and messaging apps ARC cannot send to.

Two things are being protected here. One is that `open_app` stops guessing:
Windows already knows what is installed, and matching against that list is why
"open code" now opens Visual Studio Code instead of failing.

The other is honesty. WhatsApp and Instagram have no personal API — anything
that appears to send for you is an unofficial client that gets accounts banned.
So ARC pre-fills and the human presses send, and the code says so in the words
it gives back. A test that let "Opened WhatsApp" drift into "Sent" would be
letting ARC claim something it did not do.

Nothing here opens a window, launches an app or touches the shell: the opener
is stubbed and the app list is replaced with a known one.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check, prompt_text   # noqa: E402
sandbox()


# ARC's own instructions live server-side now (prompts/main.md), where a
# browser cannot edit them. These checks ask what ARC is TOLD, so they read
# the prompt rather than the page it used to be pasted into.
PROMPT = prompt_text()
import pc   # noqa: E402

c = Check()

# A believable Start-menu listing, including the traps: a longer name that
# contains a shorter one, and a helper nobody means.
INSTALLED = [
    ("Spotify", "Spotify.exe"),
    ("Spotify Web Helper", "SpotifyWebHelper.exe"),
    ("Visual Studio Code", "code.exe"),
    ("Code Writer", "codewriter.exe"),
    ("Google Chrome", "chrome.exe"),
    ("WhatsApp", "whatsapp.exe"),
    ("Steam", "steam.exe"),
    ("Settings", "settings.exe"),
    ("notepad.exe", "notepad.exe"),
]
pc._start_apps = lambda force=False: INSTALLED

print("Matching a spoken name to something really installed:")
cases = [("spotify", "Spotify"),            # exact beats the longer sibling
         ("Spotify", "Spotify"),
         ("code", "Code Writer"),           # prefix: shortest of the prefixes
         ("visual studio code", "Visual Studio Code"),
         ("chrome", "Google Chrome"),       # substring, nothing else matches
         ("whatsapp", "WhatsApp"),
         ("steam", "Steam")]
for said, want in cases:
    got = pc._match_app(said)
    c("  %-20s -> %s" % (repr(said), want), got[0] if got else None, want)
c("  something not installed", pc._match_app("photoshop"), None)
c("  nothing asked", pc._match_app(""), None)
c("  all-words match as the last resort",
  (pc._match_app("google chrome") or (None,))[0], "Google Chrome")

print("\nListing what is installed:")
out = pc.list_apps()
c.truthy("  names them", "Spotify" in out and "Steam" in out)
c("  and hides raw executables nobody means", "notepad.exe" in out, False)
c.truthy("  filtering works", "Spotify" in pc.list_apps("spot"))
c("  a filter that matches nothing says so",
  "Nothing installed matches" in pc.list_apps("zzzz"), True)

print("\nMatching an open window:")
pc._windows = lambda: [(1, "ARC - Ambient Response Core"),
                       (2, "notes.txt - Notepad"),
                       (3, "Google Chrome")]
c("  by fragment", (pc._match_window("notepad") or (None, None))[1], "notes.txt - Notepad")
c("  by exact title", (pc._match_window("Google Chrome") or (None, None))[1], "Google Chrome")
c("  by prefix", (pc._match_window("ARC") or (None, None))[1],
  "ARC - Ambient Response Core")
c("  no match is None, not a wrong window", pc._match_window("photoshop"), None)
out = pc.list_windows()
c.truthy("  listing them reads as prose", "3 open:" in out and "Notepad" in out)

print("\nMessaging apps: pre-filled, never sent.")
opened = []
pc._open_default = lambda url: opened.append(url)

out = pc.message_app("whatsapp", "+44 7700 900123", "running late")
c.truthy("  builds a wa.me link", opened and opened[-1].startswith("https://wa.me/447700900123"))
c.truthy("  with the message in it", "running%20late" in opened[-1])
c.truthy("  and says the user presses send", "press send" in out)
# The claim that must never appear.
c("  it never claims to have sent it", "Sent" in out or "I've sent" in out, False)
c.truthy("  it says outright that it cannot", "deliberately can't send it" in out)

opened.clear()
out = pc.message_app("sms", "07700900123", "on my way")
c.truthy("  SMS uses the sms: scheme", opened[-1].startswith("sms:"))
c.truthy("  telegram takes a username",
         pc.message_app("telegram", "someone", "hi") and "t.me" in opened[-1])

print("\n  ...and it refuses rather than inventing:")
c.truthy("  a name where a number is needed",
         "needs a phone number" in pc.message_app("whatsapp", "Mum", "hi"))
# For SMS an empty recipient trips the number check first, which is the more
# useful message of the two; the generic one is for the username apps.
c.truthy("  no recipient at all (sms says it needs a number)",
         "needs a phone number" in pc.message_app("sms", "", "hi"))
c.truthy("  no recipient at all (telegram asks who)",
         "Who is it going to" in pc.message_app("telegram", "", "hi"))
c.truthy("  an app it cannot do", "I don't know" in pc.message_app("snapchat", "x", "y"))
c.truthy("  Instagram, which genuinely cannot be pre-filled",
         "no personal API" in pc.message_app("instagram", "someone", "hello"))
before = len(opened)
pc.message_app("whatsapp", "Mum", "hi")
c("  and a refusal opens nothing", len(opened), before)

print("\nWired into ARC:")
import run  # noqa: E402
for name in ["list_apps", "list_windows", "focus_window", "close_window", "message_app"]:
    c.truthy("  %-14s registered" % name, name in run.TOOL_OWNER)
c.truthy("  looking is free", {"list_apps", "list_windows"} <= run.PASSIVE_TOOLS)
c("  acting is not",
  [n for n in ["focus_window", "close_window", "message_app"] if n in run.PASSIVE_TOOLS], [])
c("  and no guest gets any of them",
  [n for n in ["list_apps", "list_windows", "focus_window", "close_window", "message_app"]
   if n in run.GUEST_TOOLS], [])
c("  none of it is offered over the tunnel",
  [n for n in ["list_apps", "message_app", "close_window"]
   if n in {t["name"] for t in run.all_tools(local=False)}], [])

print("\nARC is told what it can and cannot do here:")
page = open(HUD, encoding="utf-8").read()
c.truthy("  that the user presses send", "the USER presses send" in PROMPT)
c.truthy("  that no personal API exists", "no personal API" in PROMPT)
c.truthy("  not to invent a phone number", "Never invent a number" in PROMPT)
c.truthy("  to ask before closing something unsaved", "ask first if there might be unsaved work"
         in PROMPT)
c.truthy("  to look at what's installed rather than guess twice",
         "rather than guessing twice" in PROMPT)

c.done()
