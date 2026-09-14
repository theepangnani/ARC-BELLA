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

c.done()
