# -*- coding: utf-8 -*-
"""The system-prompt ceiling, and the two speaker-mode wiring bugs.

The first of these is the dangerous one: the limit was set when ARC's prompt was
small, and the prompt has since grown past it. The failure mode is that every
single message is rejected once a user has accumulated enough memory — and what
that looks like from the outside is simply "it stopped answering", with nothing
in the conversation to explain why.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient  # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "\n          got  %r\n          want %r" % (got, want)))


def truthy(label, got):
    check(label, bool(got), True)


def prompt_len():
    i = page.index("const SYSTEM_PROMPT = `") + len("const SYSTEM_PROMPT = `")
    j = i
    while True:
        j = page.index("`", j)
        if page[j - 1] != "\\":
            break
        j += 1
    return len(page[i:j])


BASE = prompt_len()
# What the page concatenates around it, at a realistic worst case for someone
# who has actually used ARC: memory built up, a thread digest, a lesson running.
BLOCKS = 400 + 2400 + 1600 + 300 + 400 + 300 + 300 + 900 + 300
GUEST_PREAMBLE = 640

print("Measured, not assumed:")
print("    SYSTEM_PROMPT           %6d" % BASE)
print("    + client blocks         %6d" % BLOCKS)
print("    + guest preamble        %6d" % GUEST_PREAMBLE)
print("    = worst realistic turn  %6d" % (BASE + BLOCKS + GUEST_PREAMBLE))
print("    server ceiling          %6d" % run.MAX_SYSTEM_CHARS)

WORST = BASE + BLOCKS + GUEST_PREAMBLE
truthy("the old 40,000 ceiling really was below a realistic turn", WORST > 40000)
truthy("the new ceiling clears it", run.MAX_SYSTEM_CHARS > WORST)
truthy("with room to grow (>=2x the base prompt)", run.MAX_SYSTEM_CHARS >= BASE * 2)
truthy("but still bounds abuse (not unlimited)", run.MAX_SYSTEM_CHARS < 1_000_000)

print("\nEnd to end, through the real endpoint:")


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class Resp:
    def __init__(self):
        import types as _t
        self.content = [Blk("Yes, sir.")]
        self.stop_reason = "end_turn"
        self.usage = _t.SimpleNamespace(input_tokens=1, output_tokens=1)


class FakeClaude:
    class messages:
        @staticmethod
        async def create(**kw):
            return Resp()

    async def close(self):
        pass


with TestClient(run.app) as client:
    run.app.state.claude = FakeClaude()
    sid = session.create("owner@example.com", "browser")
    gid = session.create("guest@example.com", "phone")
    C, G = {run.COOKIE: sid}, {run.COOKIE: gid}

    def ask(cookies, system):
        return client.post("/api/chat", cookies=cookies, json={
            "system": system,
            "messages": [{"role": "user", "content": "hello"}],
            "allow_actions": False,
        })

    real = "x" * (BASE + BLOCKS)
    check("a realistic owner turn is accepted", ask(C, real).status_code, 200)
    check("a realistic GUEST turn is accepted too (preamble added server-side)",
          ask(G, real).status_code, 200)
    # This is the exact size that used to fail.
    check("the size that used to be rejected now works",
          ask(C, "x" * 43800).status_code, 200)
    # And the ceiling still exists.
    r = ask(C, "x" * (run.MAX_SYSTEM_CHARS + 1))
    check("an absurd prompt is still refused", r.status_code, 400)
    truthy("with a reason", "too long" in r.text)
    # Not refused: /api/chat reads `payload.get("system") or ""`, so a missing
    # prompt becomes an empty one and ARC answers with no personality rather
    # than erroring. That is long-standing behaviour and the isinstance guard
    # below it can never actually fire; recorded here so the next reader does
    # not "fix" a 400 that was never there.
    r = ask(C, None)
    check("a missing prompt is treated as empty, not an error", r.status_code, 200)

    session.revoke_all()

print("\nBUG: speaker mode restore was nested inside `if (nightBtn) {`.")
# Two unrelated controls; one must not gate the other.
i_night = body.index('if (nightBtn) {')
i_close = body.index('applyNight(localStorage.getItem("arc.night") === "1"); } catch (_) {}')
i_spk = body.index('localStorage.getItem("arc.speaker")')
truthy("restore now sits AFTER the night-button block closes", i_spk > i_close)
# The decisive check is indentation: inside the `if (nightBtn) {` block the
# statement was indented four spaces, at top level it is two.
line = body[body.rindex("\n", 0, i_spk) + 1:i_spk]
indent = len(line) - len(line.lstrip())
check("and at top level, not nested (indent)", indent, 4)   # 4 = "  " + "if("
truthy("the restore is inside its own try, not the night one",
       body[i_close:i_spk].count("try {") >= 1)
truthy("still guarded against storage being unavailable",
       "catch (_) {}" in body[i_spk - 200:i_spk + 200])

print("\nBUG: a wake lock refused at load was only retried on tab switch.")
truthy("first interaction also retries it", "function onFirstInteraction()" in body)
truthy("...and it is what the listeners call now",
       'window.addEventListener(ev, onFirstInteraction' in body)
truthy("...and it only retries when one is not already held",
       "if (speakerMode && !wakeLock) takeWakeLock();" in body)
truthy("audio unlocking still happens on the same interaction",
       "unlockAlarmAudio();" in body)
truthy("the tab-switch retry is still there", "visibilitychange" in body)

print("\nNothing reads a `let` before it has run:")
# Match the listener REGISTRATION, not the bare identifier -- the identifier
# also appears in a comment above the declarations, which made this compare a
# declaration against a comment and report a problem that wasn't there.
LISTENER = "window.addEventListener(ev, onFirstInteraction"
for name in ["speakerMode", "wakeLock", "lastAlarmInfo"]:
    decl = body.index("let %s" % name)
    first_use = body.index(LISTENER) if name != "lastAlarmInfo" \
        else body.index("lastAlarmInfo = next;")
    truthy("  %-14s declared before the code that reads it" % name,
           decl < first_use)
    check("  %-14s declared once" % name,
          len(re.findall(r"\blet %s\b" % name, body)), 1)

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
