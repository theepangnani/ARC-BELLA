# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Mini Bella's circle: where it sits, when it shows, and what it opens.

No window is drawn here. The decisions are plain functions in bubble.py and
are checked as such; the rest is held by reading the source:

  · shown only while every Bella window is minimised, never when none exists
  · bottom right of the work area, clear of the taskbar; the chat above it
  · the chat is /mini in Bella's own browser profile, so it is already signed
    in, and bubble.py itself talks to no server and holds no credential
  · the titles it looks for are the titles the pages really have
"""
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

import bubble   # noqa: E402

c = Check()
B, M = bubble.BELLA_TITLE, bubble.MINI_TITLE

print("When the circle shows:")
c("  Bella minimised", bubble.should_show([(B, "chrome.exe", True)]), True)
c("  Bella in front", bubble.should_show([(B, "chrome.exe", False)]), False)
c("  Bella not running at all", bubble.should_show([("Inbox - Gmail", "chrome.exe", True)]), False)
c("  two Bella windows, one still open", bubble.should_show([(B, "msedge.exe", True), (B, "chrome.exe", False)]), False)
c("  two Bella windows, both minimised", bubble.should_show([(B, "msedge.exe", True), (B, "chrome.exe", True)]), True)
c("  a tab in a normal browser window that merely shares the title is still Bella's page",
  bubble.should_show([(B, "chrome.exe", True), ("Notepad", "notepad.exe", False)]), True)
c("  the same title in another program is not Bella", bubble.should_show([(B, "notepad.exe", True)]), False)

print("\nWhere it sits:")
work = (0, 0, 1920, 1032)            # a 1080p screen less a 48px taskbar
x, y = bubble.circle_position(work)
c("  bottom right, clear of the taskbar", (x, y), (1920 - 16 - 48, 1032 - 16 - 48))
cx, cy = bubble.chat_position(work)
c.truthy("  the chat opens above the circle, right edges aligned",
         cy + bubble.CHAT_H <= y and cx + bubble.CHAT_W == 1920 - 16)
c("  never off the top of a small screen", bubble.chat_position((0, 0, 800, 400))[1], 0)
c("  a second monitor to the left keeps its own origin",
  bubble.circle_position((-1280, 0, 0, 984)), (-64, 920))

print("\nWhat a click opens:")
cmd = bubble.chat_command("chrome.exe", 8421, Path("bella-data") / ".arc-window", (100, 200))
c("  /mini on this port", "--app=http://localhost:8421/mini" in cmd, True)
c.truthy("  in Bella's own profile, so it is already signed in",
         any(a.startswith("--user-data-dir=") and a.endswith(".arc-window") for a in cmd))
c("  at the chat's size and place", ("--window-size=360,520" in cmd, "--window-position=100,200" in cmd), (True, True))
c("  the profile is the one run.py opens Bella in",
  bubble.profile_dir({"ARC_DATA_DIR": "bella-data"}).name, ".arc-window")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  ...which is DATA_DIR/.arc-window", 'WINDOW_PROFILE = DATA_DIR / ".arc-window"' in run_src)

print("\nThe port:")
c("  --port wins", bubble.port_from(["--port", "8421"], {"ARC_PORT": "9000"}), 8421)
c("  else ARC_PORT", bubble.port_from([], {"ARC_PORT": "9000"}), 9000)
c("  else 8420", bubble.port_from([], {}), 8420)
c("  a bad value is not a crash", bubble.port_from(["--port", "x"], {"ARC_PORT": "y"}), 8420)

print("\nThe titles are the pages' real titles:")
hud = io.open(ARC / "static" / "index.html", encoding="utf-8").read()
c("  Bella's window", re.search(r"<title>(.*?)</title>", hud).group(1), B)
mini = ARC / "static" / "mini.html"
if mini.exists():
    c("  the mini chat", re.search(r"<title>(.*?)</title>", io.open(mini, encoding="utf-8").read()).group(1), M)
else:
    print("  NOTE  static/mini.html not written yet; its title is checked once it is.")

print("\nWhich Bella the circle belongs to (owner, 15 Sep):")
# (title, exe, minimised, hwnd, pid). Both Bellas title their window the same,
# so without the note the private Bella's circle showed for the shared one.
MINE, THEIRS = 111, 222
both = [(B, "chrome.exe", True, 11, MINE), (B, "chrome.exe", False, 22, THEIRS)]
c("  ours minimised, theirs in front: shown", bubble.should_show(both, MINE), True)
c("  ours in front, theirs minimised: hidden",
  bubble.should_show([(B, "chrome.exe", False, 11, MINE), (B, "chrome.exe", True, 22, THEIRS)], MINE), False)
c("  with no note, every Bella counts, as before", bubble.should_show(both, 0), False)
c("  a note naming a process with no window falls back to every Bella",
  bubble.should_show(both, 999), False)
c("  and the window it brings back is ours", [w[3] for w in bubble.mine(both, MINE)], [11])
data = sandbox_dir = os.environ.get("ARC_DATA_DIR", "")
c("  the pid is read from the data folder's window.json",
  bubble.window_pid({"ARC_DATA_DIR": data}), 0)
io.open(os.path.join(data, "window.json"), "w", encoding="utf-8").write('{"pid": 4242, "port": 8421}')
c("  ...once run.py has written it", bubble.window_pid({"ARC_DATA_DIR": data}), 4242)
c("  a damaged note is no note", (io.open(os.path.join(data, "window.json"), "w",
                                          encoding="utf-8").write("not json"),
                                 bubble.window_pid({"ARC_DATA_DIR": data}))[1], 0)
os.remove(os.path.join(data, "window.json"))

print("\nIt survives its own mistakes, and can always be closed:")
c.truthy("  the poll catches everything, not only OSError",
         "except Exception:" in io.open(ARC / "bubble.py", encoding="utf-8").read())
c.truthy("  a middle click closes it",
         'canvas.bind("<Button-2>", quit_circle)' in io.open(ARC / "bubble.py", encoding="utf-8").read())

print("\nThe server starts it, so it comes back with a restart:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  run.py starts bubble.py with its own port",
         'str(ROOT / "bubble.py"),' in run_src and '"--port", str(port)' in run_src)
c.truthy("  without a console window", "CREATE_NO_WINDOW" in run_src)
c.truthy("  and can be switched off", 'os.getenv("ARC_MINI_BELLA", "").strip().lower() == "off"' in run_src)
c.truthy("  never in the cloud, never off Windows",
         'if sys.platform != "win32" or CLOUD:' in run_src)
c.truthy("  and run.py notes which process owns its window", "note_window(proc.pid, port)" in run_src)

print("\nThe private Bella starts it:")
launch = io.open(ARC / "launch-bella-private.ps1", encoding="utf-8-sig").read()
c.truthy("  launched with its port, without a console",
         "'bubble.py', '--port', \"$port\"" in launch and "pythonw.exe" in launch)
c.truthy("  and can be switched off", "$env:ARC_MINI_BELLA -ne 'off'" in launch)

print("\nIt holds nothing and talks to nothing:")
src = io.open(ARC / "bubble.py", encoding="utf-8").read()
code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
code = re.sub(r'"""[\s\S]*?"""', "", code)          # docstrings explain; code acts
imports = set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", code, re.M))
for mod in ("urllib", "httpx", "requests", "socket", "http", "aiohttp", "session", "gauth"):
    c("  does not import %s" % mod, any(i == mod or i.startswith(mod + ".") for i in imports), False)
c("  never names a cookie or .env", ("cookie" in code.lower(), bool(re.search(r"\.env\b", code))),
  (False, False))
c.truthy("  one circle per port, by a mutex that goes with the process", "CreateMutexW" in src)

c.done()
