# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""The mini chat the desktop bubble opens (static/mini.html, /mini).

Asked for by the owner: "when the AI is minimised there should be a little
circle ... that can answer questions even when you can't talk". bubble.py
(Claude 2) is the circle; this page is the chat box it opens (Claude 4). It is
small in what it may do, and that is what this suite guards:

  · /mini needs a sign-in and is refused to a guest;
  · actions go through the same consent gate as the HUD, and the page never
    authorises one on its own — only a plain yes straight after Bella said she
    was holding one back;
  · no directive in a reply is acted on ([[remember:]] never reaches the
    memory route, a board never draws) and none is shown;
  · reply text reaches the page only as text, never as markup;
  · its title is exactly "Mini Bella", which bubble.py finds it by.
"""
import html as _html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()
PAGE = io.open(ARC / "static" / "mini.html", encoding="utf-8").read()
SCRIPT = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", PAGE, re.S)

print("The page, as written:")
c("  its title is exactly what bubble.py looks for",
  re.search(r"<title>(.*?)</title>", PAGE).group(1), "Mini Bella")
c.truthy("  the copyright notice sits right after the charset",
         PAGE.startswith('<meta charset="utf-8">\n<!-- ARC — Ambient Response Core.')
         or PAGE.startswith('<meta charset="utf-8">\r\n<!-- ARC — Ambient Response Core.'))
c("  one inline script, and no script loaded from anywhere", (len(SCRIPT), "<script src" in PAGE), (1, False))
c("  nothing is ever written as markup",
  [w for w in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write") if w in PAGE], [])
c("  no inline event handlers", re.findall(r"\son[a-z]+\s*=", PAGE), [])
c.truthy("  it doesn't load a squashed second Bella into its own small window",
         'href="/"' not in PAGE and "right-click the circle" in PAGE)
c.truthy("  its lock is never off, and a yes approves by token (consent.py)",
         "allow_actions: false" in SCRIPT[0] and "approve: approve" in SCRIPT[0]
         and not re.search(r"allow_actions\s*:\s*(true|authorize)", SCRIPT[0]))
c("  and it never calls the routes a directive would",
  [r for r in ("/api/chat/remember", "/api/memory", "/api/alarm", "/api/timer") if r in SCRIPT[0]], [])

print("\nThe route:")
OWNER, GUEST = "owner@example.com", "guest@example.com"
with TestClient(run.app) as client:
    O = {run.COOKIE: session.create(OWNER, "browser")}
    G = {run.COOKIE: session.create(GUEST, "phone")}
    r = client.get("/mini", cookies=O)
    c("  the owner gets the page", (r.status_code, "<title>Mini Bella</title>" in r.text), (200, True))
    c.truthy("  never cached", "no-store" in r.headers.get("cache-control", ""))
    c("  a guest is refused", client.get("/mini", cookies=G).status_code, 403)
    stranger = client.get("/mini", headers={"accept": "text/html"})
    c("  a stranger gets the sign-in page, not the chat",
      (stranger.status_code, "Mini Bella" in stranger.text), (401, False))

    # What the page sends is a same-origin JSON POST. The request gate must let
    # that through to the chat route (a stand-in model answers).
    class Blk:
        type, text = "text", "Hello from Bella."

    class Usage:
        input_tokens = output_tokens = 1
        cache_read_input_tokens = cache_creation_input_tokens = 0
        server_tool_use = None

    class Resp:
        content, stop_reason, usage = [Blk()], "end_turn", Usage()

    class Fake:
        class messages:
            @staticmethod
            async def create(**kw):
                return Resp()

        async def close(self):
            pass

    run.app.state.claude = Fake()
    r = client.post("/api/chat", cookies=O,
                    headers={"origin": "http://testserver", "sec-fetch-site": "same-origin"},
                    json={"messages": [{"role": "user", "content": "hi"}], "prompt": "main",
                          "model": "auto", "allow_actions": False})
    c("  the page's own request reaches Bella through the gate", (r.status_code, r.json().get("reply")),
      (200, "Hello from Bella."))
    session.revoke_all()

print("\nIn a browser: consent, directives and markup:")
_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]

# A stand-in server: each reply in turn, and every request the page makes kept.
STUB = r"""<script>
window.__sent = [];
window.__replies = [
  { reply: "I can switch that off for you. [[remember: the user likes it dark]] [[timer: 60|lights]]",
    blocked: ["run_prepared"],
    consent: [{ token: "TOKEN-ONE", tool: "run_prepared", preview: "run_prepared this command: lights off" }] },
  { reply: "Done, the lights are off.", blocked: [] },
  { reply: "Sure. [[board: notes]]\nsecret board text\n[[/board]]", blocked: [] },
  { reply: "<img src=x onerror=\"window.pwned=1\"> is what that tag looks like.", blocked: [] },
];
window.fetch = async (url, init) => {
  window.__sent.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
  const next = window.__replies.shift() || { reply: "ok", blocked: [] };
  return { ok: true, status: 200, json: async () => next };
};
</script>
"""
PROBE = r"""<pre id="probe"></pre><script>
(async () => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const say = document.getElementById("say"), form = document.getElementById("ask");
  async function type(text) { say.value = text; form.requestSubmit(); await wait(150); }
  await type("turn off the lights");
  await type("yes");
  await type("yes");
  await type("show me a board");
  await type("what does an img tag look like");
  // Past the window it keeps: trimming used to drop the first message and
  // leave the history starting with a reply, which the API refuses — and once
  // that started, every later message failed (bug check, 15 Sep 2026).
  for (let i = 0; i < 9; i++) await type("question number " + i);
  const shown = [...document.querySelectorAll("#log .msg")].map(d => d.textContent);
  const out = {
    allow: window.__sent.filter(s => s.url === "/api/chat").map(s => s.body.allow_actions),
    approve: window.__sent.filter(s => s.url === "/api/chat").map(s => s.body.approve),
    urls: window.__sent.map(s => s.url),
    shown: shown,
    imgs: document.querySelectorAll("#log img").length,
    pwned: !!window.pwned,
    kept: JSON.parse(sessionStorage.getItem("arc.mini.history") || "[]").length,
    firsts: window.__sent.filter(s => s.url === "/api/chat")
      .map(s => (s.body.messages[0] || {}).role),
    lens: window.__sent.filter(s => s.url === "/api/chat").map(s => s.body.messages.length),
  };
  document.getElementById("probe").textContent = JSON.stringify(out);
})();
</script>"""


def in_browser():
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    # The fonts and the logo are the real server's; offline they simply fail.
    page = PAGE.replace("<script>", STUB + "<script>", 1) + PROBE
    work = tempfile.mkdtemp(prefix="arcmini")
    try:
        p = os.path.join(work, "mini.html")
        io.open(p, "w", encoding="utf-8").write(page)
        for _ in range(3):      # a loaded machine can starve headless Chrome
            out = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                 "--user-data-dir=" + os.path.join(work, "u"), "--virtual-time-budget=8000",
                 "--dump-dom", "file:///" + p.replace("\\", "/")],
                capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
            m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
            if m and m.group(1).strip():
                return json.loads(_html.unescape(m.group(1)))
        return {}
    finally:
        shutil.rmtree(work, ignore_errors=True)


got = in_browser()
if got is None:
    print("  NOT CONFIRMED - no Chrome or Edge on this machine; a gap, not a pass")
else:
    c.truthy("  the browser ran the page", bool(got))
    got = got or {}
    c("  the lock is never off, whatever is said", set(got.get("allow", [])), {False})
    c("  a yes sends the held action's token, once, and nothing else ever does",
      got.get("approve", [])[:5], [[], ["TOKEN-ONE"], [], [], []])
    c("  ...and no later turn carries a token", [a for a in got.get("approve", [])[5:] if a], [])
    c.truthy("  and what she wants to do is shown before the yes",
             any("this command: lights off" in s for s in got.get("shown", [])))
    c("  only /api/chat was ever called, so no directive reached its route",
      sorted(set(got.get("urls", []))), ["/api/chat"])
    shown = got.get("shown", [])
    c("  no [[directive]] is shown", [s for s in shown if "[[" in s], [])
    c.truthy("  the held action is said, in words", any("Reply \"yes\" to allow just that" in s for s in shown))
    c.truthy("  a board is not drawn, and its text is not shown",
             not any("secret board text" in s for s in shown)
             and any("open full Bella" in s for s in shown))
    c("  markup in a reply is text, not an element", (got.get("imgs"), got.get("pwned")), (0, False))
    c.truthy("  ...shown as the characters it is", any("<img src=x" in s for s in shown))
    c("  the history is kept for this window only, both sides of each turn", got.get("kept"), 20)
    c("  every request starts with the person, past the window's length too",
      sorted(set(got.get("firsts", []))), ["user"])
    c.truthy("  ...and the history stays inside what it keeps",
             got.get("lens") and max(got.get("lens")) <= 20 and len(got.get("lens", [])) == 14)

c.done()
