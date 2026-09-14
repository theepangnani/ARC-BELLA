# -*- coding: utf-8 -*-
"""No argument the model can send ends the turn, in any linked-account kit.

The tool schemas aren't strict, so a model — or text it read and was steered by
— can send 5 or ["x"] where a kit expected a string. Several kits did
(x or "").strip(), the AttributeError got past run_tool's
(TypeError, ValueError) and past dispatch_tool, and the whole reply ended with
"Something went wrong mid-reply" (Claude 4's security review).

The guard is over EVERY kit in run.LINK_KITS and EVERY tool it lists, so a kit
added later is covered without anyone remembering to add it here: each
parameter of each tool set to None, a number, a list and an object, and the
service answering with an empty object, an empty list, or a page that isn't
JSON. run_tool must hand back (text, flag) every time, never raise.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["TRELLO_KEY"] = "trello-key"

import httpx   # noqa: E402
import links   # noqa: E402
import run     # noqa: E402

c = Check()
TOKEN = "tok-SECRET-kitargs"
BAD = {"None": None, "a number": 5, "a list": ["x"], "an object": {"a": 1}}


def answer(body):
    def request(method, url, **kw):
        if isinstance(body, bytes):
            return httpx.Response(200, content=body, request=httpx.Request(method, url))
        return httpx.Response(200, json=body, request=httpx.Request(method, url))
    return request


real_token, real_request = links.token, httpx.request
links.token = lambda sid, post=None: TOKEN
try:
    for sid, kit in sorted(run.LINK_KITS.items()):
        tools = {t["name"]: t for t in kit.TOOLS}
        crashes, leaks, cases = [], [], 0
        for name, spec in sorted(tools.items()):
            props = list((spec.get("input_schema") or {}).get("properties", {}))
            for served in ({}, [], b"<html>not json"):
                httpx.request = answer(served)
                for label, bad in BAD.items():
                    # Every parameter bad at once, then each one alone, so a
                    # check on one argument can't hide a crash on the next.
                    for args in [{p: bad for p in props}] + [{p: bad} for p in props]:
                        cases += 1
                        try:
                            out, failed = kit.run_tool(name, args)
                            if not isinstance(out, str) or not isinstance(failed, bool):
                                crashes.append("%s %s -> %r" % (name, json.dumps(args), (out, failed)))
                            elif "AttributeError" in out:
                                # Caught by a kit's last-resort handler: no turn
                                # lost, but an argument still wasn't read as text.
                                crashes.append("%s %s -> %s" % (name, json.dumps(args), out))
                            elif TOKEN in out:
                                leaks.append(name)
                        except Exception as e:
                            crashes.append("%s %s, service sent %r: %s" % (
                                name, json.dumps(args), served, type(e).__name__))
        c("  %-10s %4d calls, none raised" % (sid, cases), crashes[:5], [])
        c("  %-10s and no answer carried the token" % sid, leaks, [])
finally:
    links.token, httpx.request = real_token, real_request

c.done()
