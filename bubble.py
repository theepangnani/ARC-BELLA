#!/usr/bin/env python3
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Mini Bella: a small circle in the corner while Bella's window is minimised.

The owner asked for it on 15 Sep 2026: when the AI is minimised, a little
circle the size of a tab's logo sits at the bottom right of the screen, and
clicking it opens a mini chat box, for asking something when you cannot talk
and Bella's window is not in front of you.

How it is built, and why:
  · the circle is a borderless, always-on-top tkinter window. tkinter ships
    with Python, so nothing new is installed, and a browser cannot put a
    window on top of the desktop by itself;
  · it shows only while Bella's own window is minimised, and hides the moment
    she is back, so it is never a second copy of her on the screen;
  · the chat is /mini, a page on the same server, opened as a small Chromium
    app window in Bella's OWN browser profile. That profile is already signed
    in, so the chat is the owner's, with every gate the main page has, and
    this file never holds a cookie, a key or a password;
  · a second click brings the open chat forward instead of opening another;
  · a right-click brings Bella's full window back.

It talks to nothing itself: no HTTP, no files but its logo. Run it with
pythonw so there is no console: `pythonw bubble.py --port 8421`.
"""
import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

# Bella's window title (static/index.html <title>) and the mini chat's
# (static/mini.html). Matched as the whole title a Chromium app window shows.
BELLA_TITLE = "ARC — Ambient Response Core"
MINI_TITLE = "Mini Bella"
BROWSERS = {"chrome.exe", "msedge.exe"}

SIZE = 48          # the circle, in pixels: a tab's logo, a little bigger to hit
MARGIN = 16        # from the work area's corner, so it clears the taskbar
CHAT_W, CHAT_H = 360, 520
POLL_MS = 700


def port_from(argv, env) -> int:
    """--port N, else ARC_PORT, else 8420."""
    if "--port" in argv:
        try:
            return int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    try:
        return int(env.get("ARC_PORT") or 8420)
    except ValueError:
        return 8420


def profile_dir(env) -> Path:
    """The browser profile Bella's window uses (run.py WINDOW_PROFILE), so the
    chat opens already signed in."""
    return Path(env.get("ARC_DATA_DIR") or ROOT).resolve() / ".arc-window"


def find_browser() -> str:
    """Chrome or Edge, in the order run.py looks."""
    import shutil
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.isfile(p):
            return p
    return shutil.which("chrome") or shutil.which("msedge") or ""


def circle_position(work, size=SIZE, margin=MARGIN):
    """Top-left of the circle, in the bottom-right corner of the work area
    (left, top, right, bottom), which is the screen less the taskbar."""
    left, top, right, bottom = work
    return right - margin - size, bottom - margin - size


def chat_position(work, w=CHAT_W, h=CHAT_H, size=SIZE, margin=MARGIN):
    """Top-left of the chat window: above the circle, right edges aligned, and
    never off the top or left of the work area."""
    left, top, right, bottom = work
    x = max(left, right - margin - w)
    y = max(top, bottom - margin - size - 8 - h)
    return x, y


def chat_command(exe: str, port: int, profile: Path, pos) -> list:
    """The command that opens /mini as its own small app window."""
    return [exe, "--app=http://localhost:%d/mini" % port,
            "--user-data-dir=%s" % profile,
            "--window-size=%d,%d" % (CHAT_W, CHAT_H),
            "--window-position=%d,%d" % pos,
            "--no-first-run", "--no-default-browser-check"]


def window_pid(env) -> int:
    """The process that owns THIS instance's Bella window, as run.py recorded
    it in the data folder (window.json). Both Bellas title their window the
    same, so without this the circle for the private Bella showed while the
    shared one was minimised, and brought back the wrong window. 0 when there
    is no note yet, and then the title is all there is to go on."""
    try:
        blob = json.loads((Path(env.get("ARC_DATA_DIR") or ROOT).resolve()
                           / "window.json").read_text(encoding="utf-8"))
        return int(blob.get("pid") or 0)
    except Exception:
        return 0


def mine(windows, pid: int) -> list:
    """Bella's windows: this instance's, when the noted process still has one,
    and otherwise every Bella window, as before the note existed."""
    bella = [w for w in windows if w[0] == BELLA_TITLE and w[1] in BROWSERS]
    ours = [w for w in bella if pid and len(w) > 4 and w[4] == pid]
    return ours or bella


def should_show(windows, pid: int = 0) -> bool:
    """windows: (title, exe, minimised[, hwnd, pid]) for every top-level
    window. Shown while a Bella window exists and every one of them is
    minimised: one in front means she is right there, and none at all means
    she is not running."""
    bella = mine(windows, pid)
    return bool(bella) and all(w[2] for w in bella)


# --- Windows ------------------------------------------------------------------

def _user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
                              wintypes.LPARAM]
    return u


def top_windows():
    """(title, exe, minimised, hwnd) for every visible top-level window."""
    u = _user32()
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    out = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        h = k.OpenProcess(0x1000, False, pid.value)      # QUERY_LIMITED_INFORMATION
        if h:
            try:
                size = wintypes.DWORD(520)
                path = ctypes.create_unicode_buffer(520)
                if k.QueryFullProcessImageNameW(h, 0, path, ctypes.byref(size)):
                    exe = os.path.basename(path.value).lower()
            finally:
                k.CloseHandle(h)
        out.append((buf.value, exe, bool(u.IsIconic(hwnd)), hwnd, pid.value))
        return True

    u.EnumWindows(proto(each), 0)
    return out


def work_area():
    """The primary screen less the taskbar, as (left, top, right, bottom)."""
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
    return rect.left, rect.top, rect.right, rect.bottom


def bring_forward(hwnd) -> None:
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)          # SW_RESTORE
    u.SetForegroundWindow(hwnd)


def one_instance(port: int) -> bool:
    """False if a bubble for this port is already running. The mutex belongs to
    this process and goes with it, so a crash never leaves the bubble unable
    to start again."""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    one_instance.handle = k.CreateMutexW(None, False, "Local\\ARC-mini-bella-%d" % port)
    return ctypes.get_last_error() != 183          # ERROR_ALREADY_EXISTS


# --- the circle -----------------------------------------------------------------

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    port = port_from(argv, os.environ)
    if sys.platform != "win32":
        print("Mini Bella runs on Windows only.")
        return 1
    if not one_instance(port):
        return 0
    exe = find_browser()
    if not exe:
        print("Mini Bella needs Chrome or Edge, the browser Bella's window uses.")
        return 1

    import tkinter as tk
    key = "#010203"                 # painted transparent, so the square is a circle
    root = tk.Tk()
    root.title(MINI_TITLE + " circle")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=key)
    try:
        root.attributes("-transparentcolor", key)
    except tk.TclError:
        pass
    canvas = tk.Canvas(root, width=SIZE, height=SIZE, bg=key, highlightthickness=0, cursor="hand2")
    canvas.pack()
    canvas.create_oval(1, 1, SIZE - 1, SIZE - 1, fill="#0b1a24", outline="#4fd1ff", width=2)
    try:
        logo = tk.PhotoImage(file=str(ROOT / "static" / "icon-192.png")).subsample(5)
        canvas.create_image(SIZE // 2, SIZE // 2, image=logo)
        root._logo = logo                            # kept, or Tk drops the image
    except tk.TclError:
        canvas.create_text(SIZE // 2, SIZE // 2, text="B", fill="#4fd1ff",
                           font=("Segoe UI", 16, "bold"))

    def place():
        x, y = circle_position(work_area())
        root.geometry("%dx%d+%d+%d" % (SIZE, SIZE, x, y))

    def open_chat(_event=None):
        for w in top_windows():
            if w[0] == MINI_TITLE and w[1] in BROWSERS:
                bring_forward(w[3])
                return
        subprocess.Popen(chat_command(exe, port, profile_dir(os.environ), chat_position(work_area())))

    def open_bella(_event=None):
        for w in mine(top_windows(), window_pid(os.environ)):
            bring_forward(w[3])
            return

    def quit_circle(_event=None):
        """A middle click closes Mini Bella, so a circle that ever gets stuck
        is one click to be rid of. run.py starts it again on the next restart."""
        root.destroy()

    canvas.bind("<Button-1>", open_chat)
    canvas.bind("<Button-3>", open_bella)
    canvas.bind("<Button-2>", quit_circle)

    shown = {"now": True}

    trouble = {"n": 0}

    def tick():
        try:
            want = should_show(top_windows(), window_pid(os.environ))
            trouble["n"] = 0
        except Exception:
            # Any failure at all, not only OSError: a Tcl error or a bad window
            # handle used to end the poll, and the circle then sat there for
            # ever, showing or hidden, with nothing left to change it.
            trouble["n"] += 1
            want = shown["now"] if trouble["n"] < 5 else False
        if want and not shown["now"]:
            place()
            root.deiconify()
            root.attributes("-topmost", True)
        elif not want and shown["now"]:
            root.withdraw()
        shown["now"] = want
        root.after(POLL_MS, tick)

    place()
    root.withdraw()
    shown["now"] = False
    root.after(POLL_MS, tick)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
