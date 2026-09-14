# -*- coding: utf-8 -*-
"""Bella checks her own work after she acts on the screen.

Asked for as: "Bella should automatically check its work when asked something
like type this on my screen". Before, the keyboard tool typed, reported the
keystrokes, and added a sentence asking the model to take a screenshot before
claiming success. Whether it did was the model's choice.

Now the check is not a choice: the tool runner photographs the window in front
after anything that changes the screen and returns that picture with the
result, so the reply is written with the evidence in view. This holds:

  · every screen-changing action comes back with a picture; reading actions
    (where is the pointer, list the windows) do not pay for one
  · the picture comes with an instruction to look before claiming success
  · a check that cannot be taken says the action is UNCONFIRMED, never done
  · a failed action is reported as failed, not photographed as a success
  · it can be switched off

Nothing here types, clicks or photographs the real machine: the actions and
the capture are swapped for stand-ins.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

import pc   # noqa: E402

c = Check()

try:
    from PIL import Image
    FAKE = Image.new("RGB", (640, 360), (20, 30, 40))
except ImportError:
    FAKE = None

real_dispatch = dict(pc._DISPATCH)
real_grab, real_settle, real_verify = pc._grab_front, dict(pc._SETTLE), pc.VERIFY
shots = []


def fake_grab():
    shots.append(1)
    return FAKE, "the window in front, “Notepad”", (0, 0)


pc._grab_front = fake_grab
pc._SETTLE = {k: 0.0 for k in real_settle}
pc._DISPATCH.update({
    "keyboard": lambda text="", key="": "Typed %d character(s) into the window “Notepad”." % len(text),
    "mouse_control": lambda action="", x=None, y=None, amount=None: "Left-clicked." if action == "click" else "The pointer is at 1, 2.",
    "open_app": lambda name="": "Opened %s." % name,
    "list_windows": lambda: "Notepad, Chrome",
})


def kinds(out):
    return [b.get("type") for b in out] if isinstance(out, list) else "text"


try:
    if FAKE is None:
        print("  (Pillow is not installed here, so the picture half cannot be built)")
    else:
        print("Typing comes back with a picture of where it went:")
        out, failed = pc.run_tool("keyboard", {"text": "hello there"})
        c("  not a failure", failed, False)
        c("  the result, then the picture", kinds(out), ["text", "text", "image"])
        c.truthy("  the tool's own words are still there", "Typed 11 character(s)" in out[0]["text"])
        c.truthy("  with the instruction to look before claiming it", "CHECK YOUR WORK" in out[0]["text"])
        c.truthy("  ...and never to say it worked without the picture showing it",
                 "Never say it worked when the picture does not show it" in out[0]["text"])
        c.truthy("  naming what was photographed", "Notepad" in out[0]["text"])
        c.truthy("  and not to read a secret off it", "do not read it out" in out[0]["text"])
        c("  one photograph taken", len(shots), 1)

        print("\nSo do clicks and opening things:")
        out, _ = pc.run_tool("mouse_control", {"action": "click", "x": 10, "y": 10})
        c("  a click", kinds(out), ["text", "text", "image"])
        out, _ = pc.run_tool("open_app", {"name": "notepad"})
        c("  opening an app", kinds(out), ["text", "text", "image"])

        print("\nReading the screen's state costs no picture:")
        n = len(shots)
        c("  where the pointer is", kinds(pc.run_tool("mouse_control", {"action": "position"})[0]), "text")
        c("  the list of windows", kinds(pc.run_tool("list_windows", {})[0]), "text")
        c("  and nothing was photographed for them", len(shots), n)

        print("\nA check that cannot be taken is not a success:")

        def broken():
            raise OSError("screen locked")
        pc._grab_front = broken
        out, failed = pc.run_tool("keyboard", {"text": "hi"})
        c("  the action itself still stands", failed, False)
        c("  one text block, no picture", kinds(out), ["text"])
        c.truthy("  saying it is NOT confirmed", "NOT confirmed" in out[0]["text"])
        c.truthy("  and what stopped the check", "screen locked" in out[0]["text"])
        pc._grab_front = fake_grab

    print("\nAn action that failed is reported as failed, not photographed:")

    def refuses(text="", key=""):
        raise RuntimeError("Nothing was typed: no window has focus.")
    pc._DISPATCH["keyboard"] = refuses
    n = len(shots)
    out, failed = pc.run_tool("keyboard", {"text": "hi"})
    c("  a failure", failed, True)
    c("  in words", isinstance(out, str) and "Nothing was typed" in out, True)
    c("  with no picture taken", len(shots), n)

    print("\nA tool that answers with a refusal instead of raising gets no picture:")
    real_now = dict(pc._DISPATCH)
    pc._DISPATCH.update({
        "keyboard": lambda text="", key="": "I don't know the key 'ctrl+s'. Known: enter, tab.",
        "open_website": lambda url="": "That doesn't look like a web address: notaurl",
        "focus_window": lambda title="": "I don't see a window matching 'nope'.",
        "open_app": lambda name="": "Couldn't find an app called 'nope' on this system.",
    })
    n = len(shots)
    for name, args in (("keyboard", {"key": "ctrl+s"}), ("open_website", {"url": "notaurl"}),
                       ("focus_window", {"title": "nope"}), ("open_app", {"name": "nope"})):
        out, _ = pc.run_tool(name, args)
        c("  %-13s plain words, no check" % name, isinstance(out, str) and "CHECK" not in out, True)
    c("  and no photograph taken for any of them", len(shots), n)
    pc._DISPATCH.clear()
    pc._DISPATCH.update(real_now)

    print("\nEvery success phrase is one the tool really says:")
    src_now = io.open(ARC / "pc.py", encoding="utf-8").read()
    for tool, phrases in pc._DONE.items():
        body = src_now[src_now.index("def %s(" % tool):]
        body = body[:body.index("\ndef ", 1)]
        for ph in phrases:
            c.truthy("  %-13s %r" % (tool, ph), ('"%s' % ph) in body or ("f\"%s" % ph) in body)

    print("\nOld pictures fade, new ones stay, other images are untouched:")
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "x"}}
    check = {"type": "tool_result", "tool_use_id": "a",
             "content": [{"type": "text", "text": "Typed. CHECK YOUR WORK ..."}, img]}
    asked = {"type": "tool_result", "tool_use_id": "b",
             "content": [{"type": "text", "text": "Screenshot of monitor 1"}, img]}
    user_photo = {"role": "user", "content": [{"type": "text", "text": "what is this"}, img]}
    convo = [user_photo, {"role": "assistant", "content": "x"},
             {"role": "user", "content": [check, asked]}]
    faded = pc.fade_old_checks(convo)
    kinds_after = [x["type"] for x in faded[2]["content"][0]["content"]]
    c("  the check's picture became a line of text", kinds_after, ["text", "text"])
    c("  a screenshot the model asked for keeps its image",
      [x["type"] for x in faded[2]["content"][1]["content"]], ["text", "image"])
    c("  a photo the user attached is untouched", faded[0], user_photo)
    c("  and the original conversation is not modified in place",
      [x["type"] for x in convo[2]["content"][0]["content"]], ["text", "image"])
    # In batches, not every round: fading rewrites earlier rounds, and the
    # conversation is cached now. See FADE_CHECKS_OVER in run.py.
    c.truthy("  run.py fades them before adding a round, once enough have piled up",
             "convo = (pc.fade_old_checks(convo) if pc.count_checks(convo) >= FADE_CHECKS_OVER"
             in io.open(ARC / "run.py", encoding="utf-8").read())
    c("  it counts the check's picture and not the others", pc.count_checks(convo), 1)
    c("  and nothing once it has faded", pc.count_checks(faded), 0)

    print("\nIt can be switched off:")
    pc._DISPATCH["keyboard"] = lambda text="", key="": "Typed."
    pc.VERIFY = False
    c("  plain text back", pc.run_tool("keyboard", {"text": "hi"})[0], "Typed.")
    pc.VERIFY = real_verify

    print("\nIt is wired where it cannot be skipped, and Bella is told:")
    src = io.open(ARC / "pc.py", encoding="utf-8").read()
    c.truthy("  in the tool runner every pc tool goes through",
             "if _needs_look(name, args, str(result)):\n            return _look_after(name, str(result)), False"
             in src.replace("\r\n", "\n"))
    c.truthy("  on by default", 'os.getenv("ARC_VERIFY_ACTIONS", "1")' in src)
    prompt = io.open(ARC / "prompts" / "main.md", encoding="utf-8").read()
    c.truthy("  the rulebook says to look before answering", "YOU CHECK YOUR OWN WORK" in prompt)
    c.truthy("  ...and that the picture is the check", "the picture is the check" in prompt)
finally:
    pc._DISPATCH.clear()
    pc._DISPATCH.update(real_dispatch)
    pc._grab_front, pc._SETTLE, pc.VERIFY = real_grab, real_settle, real_verify

c.done()
