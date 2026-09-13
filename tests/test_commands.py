# -*- coding: utf-8 -*-
"""Slash commands: the things you do TO ARC rather than ask ARC to do.

/check and /repair existed twice over before this — as REST routes and as tools
— and reaching them cost a turn either way. You asked, the whole conversation
went up as input tokens, the model picked the tool, and you paid for all of it
to be told whether a log file had grown too big. Worse, the tool route is the
one that stops working in exactly the circumstances you most want to ask: no
credit, a revoked key, a provider outage. A diagnostic you can only reach
through the thing being diagnosed is not a diagnostic.

Two properties are worth a test rather than a reading, and both are about where
the interception lives:

  · It is on the TYPED path only. submit() is shared by the microphone,
    routines, the camera and the schedule runner. Speech recognition never
    produces a "/", but a routine whose instruction happened to begin with one
    would become an unattended "/repair all" at seven in the morning.
  · A bare /repair reports and does not act. Restoring files from backup and
    restarting the background loop are recoverable, but they should not happen
    because a key was pressed by accident.

The rest is the join between the two halves: every command the client marks
owner-only must hit a route the SERVER also refuses to a guest. A client-side
tier check is a courtesy — it saves a guest a 403 for something /help never
offered them — and courtesies are not access control.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

# The table, read out of the page rather than restated here: a test that keeps
# its own copy of the list only ever proves the copy is a copy.
table = re.search(r"const COMMANDS = \{(.*?)\n  \};", body, re.S)
NAMES = re.findall(r"^\s{4}([a-z]+):\s*\{", table.group(1), re.M) if table else []

print("The command table:")
c.truthy("  there is one", table)
c.truthy("  with the three that were asked for",
         {"compact", "check", "repair"} <= set(NAMES))
c.truthy("  and /help, without which none of the others are discoverable",
         "help" in NAMES)
print("    " + ", ".join("/" + n for n in NAMES))
for n in NAMES:
    c.truthy("  /%-8s has a handler" % n, "function cmd" + n.capitalize() in body)
    c.truthy("  /%-8s says what it does" % n,
             re.search(r"\n\s{4}%s:\s*\{[^}]*hint:\s*\"[^\"]{8,}\"" % n, table.group(1), re.S))

print("\nCommands are typed, never spoken:")
# The whole guarantee. submit() is the shared path; if the interception ever
# migrates into it, a routine or a schedule can run one unattended.
# Anchored on the name, not the whole signature: submit() gained a third
# parameter for the mid-task queue, and a check about what submit() must NOT do
# should not stop working because of what it now also takes.
sub = body[body.index("async function submit("):]
sub = sub[:sub.index("\n  function ") if "\n  function " in sub else 4000]
c("  submit() does not intercept commands", "looksLikeCommand" in sub, False)
c("  ...nor run them", "runCommand" in sub, False)
send = re.search(r'el\.sendBtn\.addEventListener\("click".*?\n  \}\);', body, re.S)
c.truthy("  the typed box does", send and "looksLikeCommand" in send.group(0))
c.truthy("  ...and returns rather than also sending the turn",
         send and re.search(r"if \(looksLikeCommand\(t\)\) \{ runCommand\(t\); return; \}",
                            send.group(0)))
c.truthy("  and the reason is written down where it is done",
         "can never trigger one" in body)

print("\nAnything shaped like a command is handled like one:")
# Falling through to the model on an unknown command would mean a typo —
# /compct — silently costs the turn the whole feature exists to save.
c.truthy("  an unknown one is answered, not forwarded",
         "No such command: /" in body)
c.truthy("  ...pointing at the list", 'Type /help for the list' in body)
c.truthy("  the pattern anchors at the start of the line", 'SLASH = /^\\/' in body)

print("\nA bare /repair reports; it does not act:")
rep = body[body.index("async function cmdRepair(arg)"):]
rep = rep[:rep.index("\n  async function cmdUsage")]
head, tail = rep.split("const what =", 1)
c("  the no-argument branch never POSTs", "selfrepair" in head, False)
c.truthy("  it asks selfcheck what is fixable", "/api/selfcheck" in head)
c.truthy("  and names the second word needed to act", "/repair all" in head)
c.truthy("  only the argument branch calls selfrepair", "/api/selfrepair" in tail)
c.truthy("  'all' means every fixable thing, which the server spells as empty",
         re.search(r'arg\.toLowerCase\(\) === "all" \? "" : arg', tail))
# The server matches a name against its own repair list and does nothing at all
# when it does not match — which reads as a successful repair of nothing.
c.truthy("  an unrecognised name is reported rather than read as success",
         "do not know how to repair" in tail)

print("\n/compact keeps the note before it drops the turns:")
comp = body[body.index("async function cmdCompact()"):]
comp = comp[:comp.index("\n  async function cmdCheck")]
c.truthy("  it builds the digest first", comp.index("refreshThread") < comp.index("state.history ="))
c.truthy("  and bails out without trimming if that failed",
         re.search(r"if \(!\(await refreshThread\(\)\)\) \{", comp))
c.truthy("  ...saying so", "left the conversation alone" in comp)
c.truthy("  the transcript itself is not touched", "transcript above is untouched" in comp)
# refreshThread used to return nothing at all, so "it failed" and "it worked"
# were the same value and /compact could not tell them apart.
thread = body[body.index("async function refreshThread()"):]
thread = thread[:thread.index("\n  function maybeRefreshThread")]
c.truthy("  refreshThread reports success", "return true;" in thread)
c("  ...and every other exit reports failure",
  thread.count("return false;"), 3)

print("\nWhat the client marks owner-only, the SERVER refuses to a guest:")
OWNER = re.findall(r"\n\s{4}([a-z]+):\s*\{[^}]*owner: true", table.group(1), re.S)
print("    " + ", ".join("/" + n for n in OWNER))
c.truthy("  check, repair and usage are all marked",
         {"check", "repair", "usage"} <= set(OWNER))
# Read each command's endpoints out of its own handler, then look at the route.
run_src = io.open(ARC / "run.py", encoding="utf-8").read()


def handler(name):
    """One cmd* function, cut at the next one rather than at a fixed length.

    An over-long slice is why this test first reported /check calling /api/usage:
    it had swallowed the two handlers after it and was auditing their endpoints
    under the wrong command's name.
    """
    start = body.index("function cmd" + name.capitalize() + "(")
    rest = body[start:]
    ends = [m.start() for m in re.finditer(r"\n  (?:async )?function \w", rest)]
    return rest[:ends[0]] if ends else rest


def route_body(path):
    """The route for a path, anchored on its DECORATOR.

    Anchoring on the bare quoted path finds whichever mention comes first in the
    file, and for /api/usage that is its entry in BACKGROUND_PATHS several
    hundred lines above the route — a stretch of code with no deny_guest in it
    and no reason to have one.
    """
    m = re.search(r'@app\.(?:get|post)\("%s"\)' % re.escape(path), run_src)
    if not m:
        return ""
    tail = run_src[m.start():]
    nxt = tail.index("\n@app.", 1) if "\n@app." in tail[1:] else len(tail)
    return tail[:nxt]


for n in OWNER:
    fn = handler(n)
    # /graph opens a PAGE rather than fetching one, through a helper shared
    # with the HUD's clickable readouts. Follow it, and hold the page to the
    # same rule as an API route: the owner-only command must lead somewhere a
    # guest is turned away.
    if "openGraph(" in fn:
        start = body.index("function openGraph(")
        fn += body[start:body.index("\n  }", start)]
    paths = sorted(set(re.findall(r'"(/api/[a-z-]+|/watch)[?"]', fn)))
    c.truthy("  /%-7s calls something" % n, paths)
    for path in paths:
        c.truthy("  /%-7s -> %-18s is guest-denied" % (n, path),
                 "deny_guest(request)" in route_body(path))

print("\nAnd the guest is actually turned away, not merely un-offered:")
with TestClient(run.app) as client:
    GUEST = {run.COOKIE: session.create("guest@example.com", "browser")}
    OWNR = {run.COOKIE: session.create("owner@example.com", "browser")}
    for path in ("/api/selfcheck", "/api/usage"):
        c("  guest %s" % path, client.get(path, cookies=GUEST).status_code, 403)
    c("  guest /api/selfrepair",
      client.post("/api/selfrepair", cookies=GUEST, json={}).status_code, 403)
    # And the owner is not locked out by the same gate.
    c("  owner /api/selfcheck", client.get("/api/selfcheck", cookies=OWNR).status_code, 200)
    session.revoke_all()

print("\nThe surface says it exists:")
c.truthy("  the input box points at it",
         'placeholder="Ask anything, or /help"' in page)
c("  and the old placeholder, which promised commands there were none of, is gone",
  'placeholder="Type a command…"' in page, False)

c.done()
