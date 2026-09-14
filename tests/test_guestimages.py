# -*- coding: utf-8 -*-
"""How many pictures a guest may send in a day — and that the owner decides.

Screen sharing for everybody means a guest can send a picture with every
sentence, and every picture is paid for. What this guards:

  · off by default: unset or 0 means no cap, so shipping this changes nothing;
  · the owner is never capped, whatever the setting;
  · a guest over the cap still gets an answer — the picture is dropped and the
    model is told why, so it neither refuses nor describes what it never saw;
  · counts are per guest and per day, memory only, and yesterday's go.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com,guest@example.com,other@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com,other@example.com"
os.environ.pop("ARC_GUEST_IMAGES_PER_DAY", None)

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

print("Off unless the owner sets a number:")
c("  unset is no cap", run.GUEST_IMAGES_PER_DAY, 0)
c.truthy("  so a guest's hundredth picture is still allowed",
         all(run.guest_image_allowed("guest@example.com") for _ in range(100)))
for raw, want in (("", 0), ("0", 0), ("12", 12), ("lots", 0), ("-3", 0)):
    os.environ["ARC_GUEST_IMAGES_PER_DAY"] = raw
    c("  ARC_GUEST_IMAGES_PER_DAY=%-5r means %d" % (raw, want), run._guest_image_cap(), want)
os.environ.pop("ARC_GUEST_IMAGES_PER_DAY", None)

print("\nCounted per guest, per day:")
run.GUEST_IMAGES_PER_DAY = 2
run._guest_images.clear()
day1 = 1_800_000_000
c("  first", run.guest_image_allowed("guest@example.com", now=day1), True)
c("  second", run.guest_image_allowed("guest@example.com", now=day1), True)
c("  third is over", run.guest_image_allowed("guest@example.com", now=day1), False)
c("  another guest has their own count", run.guest_image_allowed("other@example.com", now=day1), True)
c("  the next day starts again", run.guest_image_allowed("guest@example.com", now=day1 + 86400), True)
c("  and yesterday's counts are gone", len(run._guest_images), 1)

sent = []


class Blk:
    type, text = "text", "Here you go."


class Usage:
    input_tokens = output_tokens = 5
    cache_read_input_tokens = cache_creation_input_tokens = 0
    server_tool_use = None


class Resp:
    content, stop_reason, usage = [Blk()], "end_turn", Usage()


class Fake:
    class messages:
        @staticmethod
        async def create(**kw):
            sent.append(kw)
            return Resp()

    async def close(self):
        pass


def images_in(kw):
    n = 0
    for m in kw.get("messages", []):
        if isinstance(m.get("content"), list):
            n += sum(1 for b in m["content"] if isinstance(b, dict) and b.get("type") == "image")
    return n


def system_text(kw):
    s = kw.get("system")
    return s if isinstance(s, str) else " ".join(b.get("text", "") for b in s or [])


print("\nThrough /api/chat:")
with TestClient(run.app) as client:
    run.app.state.claude = Fake()
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    GUEST = {run.COOKIE: session.create("guest@example.com", "phone")}
    body = {"messages": [{"role": "user", "content": "what's on my screen"}], "image": PIXEL}
    run.GUEST_IMAGES_PER_DAY = 2
    run._guest_images.clear()

    got = []
    for _ in range(3):
        sent.clear()
        r = client.post("/api/chat", cookies=GUEST, json=body)
        got.append((r.status_code, images_in(sent[-1]) if sent else None,
                    "NO PICTURE THIS TIME" in system_text(sent[-1]) if sent else None))
    c("  a guest's first two pictures are attached", got[:2], [(200, 1, False)] * 2)
    c("  the third turn still answers, without the picture, and says why", got[2], (200, 0, True))

    for _ in range(4):
        sent.clear()
        r = client.post("/api/chat", cookies=OWNER, json=body)
    c("  the owner is never capped", (r.status_code, images_in(sent[-1])), (200, 1))

    run.GUEST_IMAGES_PER_DAY = 0
    sent.clear()
    r = client.post("/api/chat", cookies=GUEST, json=body)
    c("  with the cap off again, the same guest's picture goes through", images_in(sent[-1]), 1)
    session.revoke_all()

c.done()
