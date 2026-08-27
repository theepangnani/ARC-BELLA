# -*- coding: utf-8 -*-
"""What ARC says when the API says no.

The failure that prompted this was not a bug in the code — the Anthropic
account had run out of credit, which no amount of software can fix. What WAS a
bug is what the user saw:

    Link failure — Error code: 400 - {'type': 'error', 'error': {'type':
    'invalid_request_error', 'message': 'Your credit balance is too low to
    access the Anthropic API. Please go to Plans & Billing to upgrade or
    purchase credits.'}, 'request_id': 'req_011...'}

Everything needed was in there and none of it was readable, and "link failure"
sends someone to check their wifi when the answer is a billing page. So: the
provider's payload goes to the log, and the person gets the thing to do.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

import run   # noqa: E402

c = Check()


class Err(Exception):
    """Shaped like anthropic.APIStatusError: a status and a message."""

    def __init__(self, status, msg):
        self.status_code, self.message = status, msg


CREDIT = ("Error code: 400 - {'type': 'error', 'error': {'type': "
          "'invalid_request_error', 'message': 'Your credit balance is too low to "
          "access the Anthropic API. Please go to Plans & Billing to upgrade or "
          "purchase credits.'}, 'request_id': 'req_011CeGHTVGuVsyqa9KtCsT8X'}")

print("The exact error from the screenshot:")
h = run._claude_error(Err(400, CREDIT))
print("      " + h.detail)
c.truthy("  it says what is wrong, in a sentence", "out of credit" in h.detail)
c.truthy("  it says where to fix it", "console.anthropic.com" in h.detail)
c.truthy("  and what to do afterwards", "try again" in h.detail)
c("  no raw payload reaches the user", "{" in h.detail or "'type'" in h.detail, False)
c("  no request id either", "req_011" in h.detail, False)
# 402 Payment Required is exactly what this is, and it lets the client tell it
# apart from a transport failure.
c("  the status says payment, not bad request", h.status_code, 402)

print("\nThe others a person can actually act on:")
cases = [
    # 502, not 401 — see the section below, which is the whole reason.
    (401, "invalid x-api-key", 502, ["ANTHROPIC_API_KEY", ".env"]),
    (403, "permission denied for model", 403, ["isn't permitted"]),
    (404, "model: claude-nope not found", 404, ["doesn't exist"]),
    (429, "rate limit exceeded", 429, ["rate-limiting", "moment"]),
    (529, "Overloaded", 529, ["overloaded"]),
    (500, "internal server error", 502, ["their end"]),
]
for status, msg, want_status, needles in cases:
    h = run._claude_error(Err(status, msg))
    c("  %3d -> %3d" % (status, want_status), h.status_code, want_status)
    for n in needles:
        c.truthy("       says %r" % n, n.lower() in h.detail.lower())

print("\nA spending cap is not an empty balance, and saying so is the point:")
# Hit for real on 2026-08-27: the key was valid, the account had credit, and a
# monthly ceiling the owner had set on themselves was stopping it being used.
# Sending them to Plans & Billing would have had them buy credit they already
# had and watch it change nothing — a different page, a different fix.
LIMIT = ("You have reached your specified API usage limits. "
         "You will regain access on 2026-09-01 at 00:00 UTC.")
h = run._claude_error(Err(400, LIMIT))
c("  it is payment-shaped, like the credit one", h.status_code, 402)
c.truthy("  ...but says CAP, not empty", "spending limit" in h.detail)
c.truthy("  ...and says there IS credit", "there's credit" in h.detail)
c.truthy("  ...and sends them to the right page", "Settings, Limits" in h.detail)
c("  ...not the wrong one", "Plans & Billing" in h.detail, False)
# The reset date is the one fact that decides whether you change a setting or
# just wait, so the provider's own sentence is kept.
c.truthy("  and keeps the date it lifts", "2026-09-01" in h.detail)
c.truthy("  ...as a whole sentence", "You will regain access" in h.detail)
# Without a date it must still be a sentence, not a dangling fragment.
c.truthy("  a message with no date still reads",
         run._claude_error(Err(400, "You have reached your specified API usage limits."))
         .detail.rstrip().endswith("Settings, Limits."))
# And the older, genuinely-empty case is untouched.
c("  an empty balance is still its own message",
  "Plans & Billing" in run._claude_error(Err(400, CREDIT)).detail, True)

print("\n401 MEANS ONE THING, AND IT IS NOT THIS:")
# The bug this pins cost an evening. ARC's page wraps every fetch and treats a
# 401 as "your sign-in has ended" — it navigates to the sign-in page. When a
# revoked ANTHROPIC key also came back as 401, asking a question sent the
# browser away, landed it on a HUD the session was still perfectly good for,
# and did the same on the next question. A loop, with the one message that
# explained it never on screen long enough to read.
#
# ARC is a proxy. Its upstream refusing ARC's own credentials is a bad gateway,
# and it has nothing to do with who is signed in at this end.
for _msg in ("invalid x-api-key", "authentication_error", "API key is invalid."):
    h = run._claude_error(Err(401, _msg))
    c("  upstream %-20r is not a 401 here" % _msg[:20], h.status_code == 401, False)
    c("  ...it is a bad gateway", h.status_code, 502)
c.truthy("  and it still names the key and the file",
         "ANTHROPIC_API_KEY" in run._claude_error(Err(401, "bad key")).detail)
# Stated as an invariant, where it can be checked: nothing the provider does
# may come back as 401, because 401 is spoken for.
c("  NO provider failure maps to 401 at all",
  [st for st, m in [(400, "credit balance too low"), (401, "invalid x-api-key"),
                    (403, "permission denied"), (404, "model x not found"),
                    (429, "rate limit"), (500, "boom"), (529, "Overloaded")]
   if run._claude_error(Err(st, m)).status_code == 401], [])

print("\nAnd the page checks before it throws itself away:")
_hud = open(HUD, encoding="utf-8").read()
c.truthy("  a 401 is confirmed against the session first",
         'dead = (await raw("/api/session")).status === 401' in _hud)
c.truthy("  ...through the unwrapped fetch, so it cannot recurse",
         'raw("/api/session")' in _hud)
c.truthy("  and only the session's own answer ends the session",
         "if (dead) { leaving = true; signInAgain" in _hud)

print("\nAn unrecognised one is trimmed, not dumped:")
h = run._claude_error(Err(400, "messages.0.content: field required {'lots': 'of noise'}"))
c.truthy("  keeps the useful first clause", "field required" in h.detail)
c("  drops the payload", "{" in h.detail, False)
c.truthy("  and is bounded", len(h.detail) < 260)

print("\nEvery route that talks to Claude goes through it:")
src = open(ARC / "run.py", encoding="utf-8").read()
c("  no site still forwards the raw message",
  len(re.findall(r"str\(e\.message\)\[:\d+\]", src)), 0)
c("  both APIStatusError handlers use the mapper",
  len(re.findall(r"except anthropic\.APIStatusError as e:\s*\n\s*raise _claude_error\(e\)", src)), 2)
c.truthy("  and the detail is still logged in full for whoever is debugging",
         'print(f"{C_RED}  ! anthropic {status}' in src)

print("\nThe page shows a server sentence as itself:")
page = open(HUD, encoding="utf-8").read()
c.truthy("  a server-authored error is marked", "err.fromServer = said" in page)
c.truthy("  and shown without the 'link failure' dressing",
         "plain ? err.message :" in page)
c.truthy("  network failures still read as network failures",
         "the connection dropped for a moment" in page)
# Saying "the link is down" when the truth is "your credit ran out" sends
# someone to check their router.
c.truthy("  and it SPEAKS the real reason", "err.message.length < 260" in page)
c.truthy("  ...with the old line kept for genuine link failures",
         '"I\'m afraid the link is down, sir."' in page)

c.done()
