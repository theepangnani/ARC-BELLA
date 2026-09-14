# -*- coding: utf-8 -*-
"""Live screen for everybody: off the desktop, it shares your own device's screen.

The desktop's Live screen is the server screenshotting THIS machine, which is
why it is local-only — through the funnel it would show a guest the owner's
home PC. The owner asked on 14 Sep 2026 for Live screen for everybody, so
anywhere else the same button shares the screen of the device the person is
really on, through the browser's own picker, and a frame rides with each
message the way a camera photo does.

What this holds in place:
  · the desktop's own Live screen is exactly what it was, and still local-only
  · a shared frame goes through the camera's `image` field, which guests have
  · it can be seen, not acted on, and the model is told that in so many words
  · it stops on a click, on the browser's Stop sharing, and when the page goes;
    NOT when this tab hides, because sharing hides it every time
  · a frame that is not ready is no frame, never a failed turn
  · it never reaches history, localStorage, or watch mode
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c = Check()


def fn(name):
    """The text of one function in the page, up to the next function at its level."""
    start = body.index("function " + name + "(")
    nxt = re.search(r"\n  (async )?function |\n  const |\n  let |\n  if \(", body[start + 10:])
    return body[start:start + 10 + (nxt.start() if nxt else 4000)]


print("The desktop's Live screen is unchanged, and still the desktop's:")
c.truthy("  the server still attaches its own screen only when local",
         "if see_screen and local and pc.connected():" in run_src)
c.truthy("  the page still asks for it only when this is the desktop",
         "see_screen: (liveScreen && canSeeScreen) ? liveScreenMode : false," in body)
c.truthy("  and 'the desktop' is still only what the server says",
         "canSeeScreen = !!h.computer;" in body)

print("\nEverywhere else, the same button shares this device's screen:")
c.truthy("  through the browser's own picker",
         "navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })" in body)
c.truthy("  no audio is asked for", "audio: false" in fn("startScreenShare"))
c.truthy("  only offered where the browser can do it",
         "const canShareScreen = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);" in body)
c.truthy("  the button is never greyed out, so a phone can press it and be told why",
         "liveScreenBtn.disabled = false;" in body
         and "liveScreenBtn.disabled = !canSeeScreen" not in body)
c.truthy("  its tooltip no longer says desktop only", "with every message (desktop only)" not in page
         and "anywhere else it shares the screen or window you pick" in page)
c.truthy("  its tooltip follows what pressing it will do",
         "liveScreenBtn.title = canSeeScreen" in fn("paintLiveScreen"))
c.truthy("  a phone is told why not, and offered the camera",
         "This browser can't share its screen." in body and "use the camera button" in body)
label = fn("liveScreenLabel")
c.truthy("  the button says when it is sharing", '"Live screen: sharing"' in label)
c.truthy("  starting it says what it means",
         "I can look, " in body and "not click." in body and "Nothing is saved." in body)
c.truthy("  cancelling the picker is not reported as a fault", "Cancelling the picker is a choice" in body)
# Chrome's cancel is NotAllowedError "Permission denied", with no word "cancel"
# in it, so matching on /cancel/ reported every cancel as a failure (Claude 1's
# review). Only the SYSTEM's refusal of a NotAllowedError is explained.
c("  ...and is not recognised by the word 'cancel', which Chrome never says",
  "/cancel|dismiss/" in body, False)
c.truthy("  the system blocking it is explained", "/system/i.test(e.message" in body)
# getDisplayMedia can succeed and play() still fail; the stream was not yet
# sharedStream, so stopScreenShare() could not reach it and the browser's
# sharing bar stayed up over nothing.
c.truthy("  a capture that never played is stopped",
         re.search(r"await video\.play\(\);\s*\}\s*catch \(e\) \{[^}]*stream\.getTracks\(\)\.forEach",
                   fn("startScreenShare")) is not None)

print("\nA frame rides with each message, the way the camera's does:")
c.truthy("  in the same image field", "image: pendingCameraImage || sharedScreenFrame()" in body)
c.truthy("  the camera wins when there are both",
         body.index("pendingCameraImage || sharedScreenFrame()") > 0)
# The server path it relies on has no guest check, which is what makes this
# work for everybody without a new route. Pinned so that one being added later
# is a decision about screen sharing, not an accident.
cam = run_src[run_src.index('picture = client_image(payload.get("image"))'):]
cam = cam[:cam.index("# --- the prompt itself")]
# Moved once, deliberately: the path now has ONE guest check, the owner's
# per-day picture cap (ARC_GUEST_IMAGES_PER_DAY, off unless set, and over it
# the words are still answered — tests/test_guestimages.py). Anything else
# mentioning guests here is still a decision this guard should stop.
c("  ...its one guest check is the picture cap, exactly once",
  cam.count("guest and not guest_image_allowed("), 1)
_over = cam[cam.index("guest and not guest_image_allowed("):]
_over = _over[:_over.index("else:")]
c.truthy("  ...and over the cap the picture is dropped, not the turn",
         "extra +=" in _over and "return" not in _over and "raise" not in _over
         and "HTTPException" not in _over)
_capped = cam.replace("guest and not guest_image_allowed(", "")
if "NO PICTURE THIS TIME" in _capped:
    _capped = (_capped[:_capped.index("NO PICTURE THIS TIME")] +
               _capped[_capped.index("picture you have not seen"):])
c("  the server's image path is not closed to guests", "guest" in _capped, False)
# The bound moved into client_image(), which the path calls first: the bytes
# are checked, and decoded to at most 5 MB, before anything is attached.
_ci = run_src[run_src.index("def client_image("):]
_ci = _ci[:_ci.index("\ndef ", 10)]
c.truthy("  ...and is bounded",
         "0 < len(b64) < 8_000_000" in _ci and "CLIENT_IMAGE_MAX_BYTES" in _ci)
frame = fn("sharedScreenFrame")
c.truthy("  no wider than 1280px", "Math.min(1, 1280 / w)" in frame)
c.truthy("  as a JPEG", 'toDataURL("image/jpeg", 0.7)' in frame)
c.truthy("  null when nothing is shared", "if (!screenShared()) return null;" in frame)
c.truthy("  null when no frame has arrived yet", "readyState < 2) return null" in frame)
c.truthy("  null, never a throw, if drawing fails", "catch (_) { return null; }" in frame)
c("  it waits on nothing", "await" in frame, False)

print("\nARC is told it can see this screen and not act on it:")
sb = fn("screenBlock")
c.truthy("  a shared screen gets its own note", "SCREEN SHARED FROM THEIR DEVICE" in sb)
c.truthy("  ...before the desktop's", sb.index("SCREEN SHARED") < sb.index("LIVE SCREEN IS ON"))
c.truthy("  not the home computer", "NOT the home computer" in sb)
c.truthy("  nothing can be clicked or typed there", "You CANNOT click, type" in sb)
c.truthy("  the computer tools are a different machine", "act on a different machine" in sb)
c.truthy("  so guide in words", "guide them in words" in sb)
c.truthy("  and it is not a camera photo", "not a camera photo" in sb)

print("\nIt stops when it should, and only then:")
c.truthy("  on a click", "Screen sharing off." in body)
c.truthy("  on the browser's Stop sharing", 'addEventListener("ended"' in fn("startScreenShare"))
c.truthy("  when the page goes away", 'window.addEventListener("pagehide", stopScreenShare);' in body)
c.truthy("  every track is stopped", "stream.getTracks().forEach(t => { try { t.stop(); }" in fn("stopScreenShare"))
c.truthy("  on the desktop, which sees its own screen",
         "else if (screenShared()) stopScreenShare();" in body)
share_code = body[body.index("const canShareScreen"):body.index("function liveScreenLabel")]
c("  NOT when this tab hides: sharing hides it by design",
  "visibilitychange" in share_code or "document.hidden" in share_code, False)
c.truthy("  ...and the reason is written down", "hides this tab every single time" in body)

print("\nIt is never kept, and never used by watch mode:")
c("  no frame in localStorage", re.search(r"localStorage\.setItem\([^)]*shared", body) is not None, False)
c("  history keeps text only", "sharedScreenFrame" in body[body.index("function restoreHistory"):
                                                         body.index("async function askClaude")], False)
c("  only askClaude takes a frame", len(re.findall(r"(?<!function )sharedScreenFrame\(\)", body)), 1)
watch = body[body.index("async function watchGlance"):body.index("function setWatch")]
c("  watch mode has no shared frame", "sharedScreenFrame" in watch or "image:" in watch, False)

c.done()
