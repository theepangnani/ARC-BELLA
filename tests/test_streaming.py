# -*- coding: utf-8 -*-
"""Streaming: the same turn, its words arriving as they are written.

What this guards:
  · off unless ARC_STREAM is on — the route answers 404 and the page falls back;
  · /api/chat is untouched: with no stream listening, the model is called with
    create() exactly as before;
  · the streamed turn IS chat(): same reply, same tools, same cost, and the
    message the loop reads is the SDK's final message, not deltas pasted
    together (Claude 3's conditions: usage, stop_reason, content verbatim);
  · every model call opens with a round event, so text written before a tool
    or before a step-up is dropped by the page rather than spoken;
  · an error mid-turn arrives as an error event with the server's sentence;
  · the page never runs extractDirectives on half a reply, and never speaks early.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ.pop("ARC_STREAM", None)

from starlette.testclient import TestClient   # noqa: E402
import run       # noqa: E402
import session   # noqa: E402

c = Check()


class Blk:
    def __init__(self, t):
        self.type, self.text = "text", t


class ToolBlk:
    type = "tool_use"

    def __init__(self, name, args, i):
        self.name, self.input, self.id = name, args, "t%d" % i


class Usage:
    def __init__(self):
        self.input_tokens, self.output_tokens = 1000, 50
        self.cache_read_input_tokens, self.cache_creation_input_tokens = 400, 0
        self.server_tool_use = None


class Resp:
    def __init__(self, content, stop="end_turn"):
        self.content, self.stop_reason, self.usage = content, stop, Usage()


class Ev:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class D:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def script(i):
    """Round 1 asks for the weather after a stray preamble; round 2 answers."""
    if i == 1:
        return Resp([Blk("Let me look."), ToolBlk("weather", {"place": "Toronto"}, i)], stop="tool_use")
    return Resp([Blk("Fourteen degrees and cloudy. An umbrella for later, sir.")])


calls = {"create": 0, "stream": 0}
finals = []


class Stream:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for b in self.resp.content:
                if b.type == "text":
                    for piece in b.text.split(" "):
                        yield Ev("content_block_delta", delta=D(type="text_delta", text=piece + " "))
                else:
                    yield Ev("content_block_start", content_block=D(type=b.type, name=b.name))
        return gen()

    async def get_final_message(self):
        finals.append(self.resp)
        return self.resp


class Fake:
    def __init__(self, broken=False):
        self.n, self.broken = 0, broken
        outer = self

        class messages:
            @staticmethod
            async def create(**kw):
                calls["create"] += 1
                outer.n += 1
                return script(outer.n)

            @staticmethod
            def stream(**kw):
                calls["stream"] += 1
                outer.n += 1
                if outer.broken:
                    raise RuntimeError("socket gone")
                return Stream(script(outer.n))
        self.messages = messages

    async def close(self):
        pass


def events(text):
    out = []
    for frame in text.split("\n\n"):
        kind, data = None, None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if kind:
            out.append((kind, data))
    return out


BODY = {"messages": [{"role": "user", "content": "weather in toronto"}], "allow_actions": True}

with TestClient(run.app) as client:
    OWNER = {run.COOKIE: session.create("owner@example.com", "browser")}
    run.dispatch_tool = lambda name, args, **kw: ("14C, cloudy", False)

    print("Off unless the owner turns it on:")
    run.STREAMING = False
    c("  the route is a 404", client.post("/api/chat/stream", cookies=OWNER, json=BODY).status_code, 404)

    print("\n/api/chat is untouched:")
    run.app.state.claude = Fake()
    r = client.post("/api/chat", cookies=OWNER, json=BODY)
    plain = r.json()
    c("  it answers", r.status_code, 200)
    c("  through create(), never stream()", (calls["create"], calls["stream"]), (2, 0))

    print("\nThe streamed turn is the same turn:")
    run.STREAMING = True
    run.app.state.claude = Fake()
    r = client.post("/api/chat/stream", cookies=OWNER, json=BODY)
    c("  it answers", r.status_code, 200)
    c.truthy("  as server-sent events", r.headers["content-type"].startswith("text/event-stream"))
    ev = events(r.text)
    kinds = [k for k, _ in ev]
    c("  through stream(), never create()", (calls["create"], calls["stream"]), (2, 2))
    c("  each model call opens with a round", kinds.count("round"), 2)
    c.truthy("  the tool is announced before the second round",
             kinds.index("tool") < [i for i, k in enumerate(kinds) if k == "round"][1])
    said = "".join(d["t"] for k, d in ev if k == "delta")
    c.truthy("  the words arrive as deltas", "Fourteen degrees" in said and said.count(" ") > 5)
    c("  exactly one done, last", (kinds.count("done"), kinds[-1]), (1, "done"))
    done = ev[-1][1]
    c("  the reply is the finished one, not the preamble", done["reply"], plain["reply"])
    c.truthy("  ...which never contains the preamble", "Let me look" not in done["reply"])
    c("  the same tools ran", done["tools"], plain["tools"])
    c("  and it cost the same (usage came from the final message)",
      round(done["cost_today"] - plain["cost_today"], 6) > 0, True)
    c("  the loop read the SDK's final messages", len(finals), 2)

    print("\nA failure mid-turn arrives as an error event:")
    run.app.state.claude = Fake(broken=True)
    r = client.post("/api/chat/stream", cookies=OWNER, json=BODY)
    ev = events(r.text)
    c("  one error, last", ev[-1][0], "error")
    c.truthy("  with a sentence, not a traceback", "socket" not in ev[-1][1]["detail"])
    c("  a stranger gets nothing", client.post("/api/chat/stream", json=BODY).status_code, 401)
    session.revoke_all()

# A guest who closes the page mid-turn stops being paid for: the round in hand
# and its tools finish, the next model call is never made, and what was spent is
# still booked. The owner's turn runs to its end, as /api/chat's does. Driven
# through chat_stream itself, closing its event stream the way Starlette does
# when the client leaves.
print("\nWhen the page goes away mid-turn:")
import asyncio      # noqa: E402
import http.cookies  # noqa: E402
from starlette.requests import Request  # noqa: E402

os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"
run.GUEST_EMAILS = {"guest@example.com"}


def left_after_first_round(email):
    """One streamed turn by `email` whose page leaves during round 1."""
    gate = {}
    seen = {"stream": 0, "tools": 0}

    class GatedStream(Stream):
        def __aiter__(self):
            inner = Stream.__aiter__(self)

            async def gen():
                async for ev in inner:
                    yield ev
                await gate["open"].wait()
            return gen()

    class Leaving:
        def __init__(self):
            self.n = 0
            outer = self

            class messages:
                @staticmethod
                def stream(**kw):
                    seen["stream"] += 1
                    outer.n += 1
                    return GatedStream(script(outer.n))
            self.messages = messages

    def tool(name, args, **kw):
        seen["tools"] += 1
        return "14C, cloudy", False

    async def go():
        gate["open"] = asyncio.Event()
        run.app.state.claude = Leaving()
        run.dispatch_tool = tool
        sid = session.create(email, "browser")
        cookie = http.cookies.SimpleCookie()
        cookie[run.COOKIE] = sid
        body = json.dumps(BODY).encode()
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)

        req = Request({"type": "http", "method": "POST", "path": "/api/chat/stream",
                       "headers": [(b"cookie", cookie.output(header="").strip().encode()),
                                   (b"content-type", b"application/json")],
                       "client": ("127.0.0.1", 5000), "server": ("test", 80),
                       "scheme": "http", "query_string": b"", "app": run.app}, receive)
        before = run._day["cost"]
        resp = await run.chat_stream(req)
        it = resp.body_iterator
        first = await it.__anext__()
        await it.aclose()                  # the page went away, mid round 1
        gate["open"].set()
        for t in list(run._stream_turns):
            await asyncio.wait_for(t, 10)
        seen["first"] = first
        seen["booked"] = run._day["cost"] > before
        seen["held"] = len(run._stream_turns)
        return seen

    return asyncio.run(go())


s = left_after_first_round("guest@example.com")
c.truthy("  (the stream really had started)", "round" in s["first"])
c("  a guest's turn: round 1 and its tool finish", s["tools"], 1)
c("  ...and the next model call is never made", s["stream"], 1)
c("  ...and what round 1 cost is still booked", s["booked"], True)
c("  ...and the finished turn is let go", s["held"], 0)
s = left_after_first_round("owner@example.com")
c("  the owner's turn runs to its end", (s["tools"], s["stream"]), (1, 2))
session.revoke_all()

src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the one model call in chat() goes through _claude_round",
         "resp = await _claude_round(claude, kwargs)" in src)
c.truthy("  kwargs still built as the cache and thinking tests pin them",
         "messages=cache_mark(convo)" in src and "thinking=fit_thinking(" in src)

print("\nThe page:")
hud = io.open(ARC / "static" / "index.html", encoding="utf-8").read()
reader = hud.split("async function readChatStream(")[1].split("\n  async function speakRemote(")[0]
c.truthy("  falls back to /api/chat when the route is missing",
         'fetch("/api/chat/stream"' in hud and "streamOff = true" in hud)
c.truthy("  drops earlier text when a new round begins", 'kind === "round") said = ""' in reader)
c.truthy("  never runs extractDirectives on half a reply", "extractDirectives" not in reader
         and "extractDirectives" not in hud.split("function speechPreview(")[1].split("function prefetchSpeech(")[0])
c.truthy("  never SPEAKS early — only renders audio ahead", "speak(" not in reader)
c.truthy("  renders nothing ahead in chat mode", "readChatStream(res, !chatMode)" in hud)
c.truthy("  speakRemote uses a head start when it has one", "ttsPrefetch.get(prefetchKey(t))" in hud)
c.truthy("  and cuts replies the same way the head start does",
         "chunks = speechChunks(text)" in hud.split("async function speakRemote(")[1][:6000])

# The promise that matters, run for real: nothing rendered ahead is ever a chunk
# the finished reply would cut differently. Every prefix of each reply is fed
# in as the stream would deliver it, and every chunk the page would render
# early must be a leading chunk of the finished reply. Same engines and the same
# no-pass-without-an-engine stance as test_speech_gate.py.
print("\nThe head start, run in a real engine:")
import re           # noqa: E402
import shutil       # noqa: E402
import subprocess   # noqa: E402
import tempfile     # noqa: E402

script_src = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", hud, re.S)[0]


def cut(start, end):
    i = script_src.index(start)
    return script_src[i:script_src.index(end, i)]


FNS = cut("  function splitForSpeech(", "\n  }\n") + "\n  }\n" + \
    cut("  function speechChunks(", "\n  }\n") + "\n  }\n"
REPLIES = [
    "Fourteen degrees and cloudy. An umbrella for later, sir.",
    "Right.",
    "It's half past four. You have the dentist at five, and the drive is twenty minutes. "
    "Leave by twenty to, and you'll be early enough to find parking without hurrying.",
    "No plan survives contact with a Tuesday! Still, the first step is booking the flight, "
    "which I can't do, but I can find you the cheapest dates if you like.",
]
JS = FNS + """
const __replies = %s;
const __out = __replies.map(r => {
  const final = speechChunks(r);
  let bad = 0, early = 0;
  for (let n = 1; n <= r.length; n++) {
    const ch = speechChunks(r.slice(0, n));
    const ahead = ch.slice(0, Math.min(2, ch.length - 1));
    ahead.forEach((c, i) => { early++; if (final[i] !== c) bad++; });
  }
  return [final.length, final[0], early, bad];
});
""" % json.dumps(REPLIES)

roots = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
exe = next((p for p in [os.path.join(r, *parts) for r in roots if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))] if os.path.isfile(p)), None) \
    or shutil.which("google-chrome") or shutil.which("chromium")
if not exe:
    print("  NOTE  no Chrome or Edge here. NOT CONFIRMED - a gap, not a pass.")
else:
    work = tempfile.mkdtemp(prefix="arcstream")
    try:
        page = os.path.join(work, "s.html")
        io.open(page, "w", encoding="utf-8").write(
            '<!doctype html><meta charset="utf-8"><body><script>\ntry {\n' + JS +
            '\ndocument.body.textContent = "RESULT:" + JSON.stringify(__out);\n'
            '} catch (e) { document.body.textContent = "ERROR:" + e; }\n</script></body>')
        dom = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=" + os.path.join(work, "p"), "--dump-dom",
             "file:///" + page.replace("\\", "/")],
            capture_output=True, timeout=90, encoding="utf-8", errors="replace").stdout
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m = re.search(r"(RESULT|ERROR):(.*?)(?:</body>|$)", dom, re.S)
    c.truthy("  it ran (%s)" % os.path.basename(exe), m and m.group(1) == "RESULT")
    res = json.loads(m.group(2).strip()) if m and m.group(1) == "RESULT" else []
    if res:
        c("  a two-sentence reply is two chunks, the first sentence alone",
          res[0][:2], [2, "Fourteen degrees and cloudy."])
        c("  a one-word reply is one chunk, rendered only when finished", res[1][:3], [1, "Right.", 0])
        c.truthy("  a long reply gets a head start", res[2][2] > 0 and res[3][2] > 0)
        c("  and not one chunk rendered ahead differs from the finished reply",
          [r[3] for r in res], [0, 0, 0, 0])

c.done()
