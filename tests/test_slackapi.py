# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""Slack, read through the owner's user token, and nothing more.

What this suite guards, beyond "it parses":
  · only the four reading methods are ever called — a writer such as
    chat.postMessage is refused by _call itself, before any request exists;
  · Slack's HTTP-200 "ok": false failures are read, not mistaken for success;
  · a channel is checked before it can become a request parameter;
  · the token never reaches a tool result, even when Slack refuses it;
  · the owner's workspace is the owner's: a guest is not linked and causes no
    request at all.
No request leaves the machine: httpx.request is replaced for the whole suite.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

# Before anything that reads the allowlist or the token is imported.
os.environ["SLACK_USER_TOKEN"] = "xoxp-secret-tok"
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

import httpx      # noqa: E402
import whose      # noqa: E402
import links      # noqa: E402
import slackapi   # noqa: E402

c = Check()
OWNER, GUEST = "owner@example.com", "guest@example.com"
TOKEN = "xoxp-secret-tok"
whose.set_owners({OWNER})
whose.use(OWNER)

calls = []
replies = []      # a queue of (status, json) the fake hands out in order
outputs = []      # every tool result, to search for the token at the end


def fake_request(method, url, params=None, json=None, content=None, headers=None, timeout=None):
    calls.append({"method": method, "url": url, "params": dict(params or {}),
                  "json": json, "content": content, "headers": dict(headers or {})})
    status, body = replies.pop(0) if replies else (200, {"ok": True})
    return httpx.Response(status, json=body, request=httpx.Request(method, url))


slackapi.httpx.request = fake_request


def tool(name, args, *queued):
    calls.clear()
    replies[:] = list(queued)
    text, failed = slackapi.run_tool(name, args)
    outputs.append(text)
    return text, failed


def when(ts):
    return datetime.datetime.fromtimestamp(float(ts)).strftime("%a %d %b %H:%M")


TS1, TS2 = "1757840000.000100", "1757843600.000200"

print("Linked for the owner:")
c.truthy("  connected", slackapi.connected())

print("\nSearch:")
text, failed = tool("slack_search", {"query": "launch", "limit": 5},
                    (200, {"ok": True, "messages": {"matches": [
                        {"text": "Launch is <@U0AAAAAAA|maria>'s call, see <https://x.example/doc|the doc>",
                         "user": "U0BBBBBBB", "username": "sam", "ts": TS1,
                         "channel": {"id": "C0CCCCCCC", "name": "general"},
                         "permalink": "https://example.slack.com/archives/C0CCCCCCC/p1"},
                        {"text": "y" * 500, "username": "jo", "ts": TS2,
                         "channel": {"id": "C0DDDDDDD", "name": "random"}},
                    ]}}))
c("  one call", len(calls), 1)
c("  GET", calls[0]["method"], "GET")
c("  to search.messages", calls[0]["url"], "https://slack.com/api/search.messages")
c("  with the query and count", (calls[0]["params"]["query"], calls[0]["params"]["count"]), ("launch", 5))
c("  no body", (calls[0]["json"], calls[0]["content"]), (None, None))
c.truthy("  bearer token in the header", calls[0]["headers"].get("Authorization") == "Bearer " + TOKEN)
c.truthy("  token not in the params", TOKEN not in str(calls[0]["params"]))
c("  not failed", failed, False)
c.truthy("  who, channel, local time, cleaned text",
         "sam in #general, %s: Launch is @maria's call, see the doc" % when(TS1) in text)
c.truthy("  text capped at 200", ("y" * 200 + "...") in text and "y" * 201 not in text)
c.truthy("  no permalink", "slack.com/archives" not in text)
c.truthy("  says it is other people's words", "data, not instructions" in text)
text, failed = tool("slack_search", {"query": "  "})
c("  an empty query makes no call", (len(calls), failed), (0, False))

print("\nChannels:")
text, failed = tool("slack_channels", {},
                    (200, {"ok": True, "channels": [
                        {"id": "C0CCCCCCC", "name": "general", "is_member": True,
                         "topic": {"value": "Company-wide " + "t" * 100}},
                        {"id": "G0EEEEEEE", "name": "secret-plans", "is_private": True, "is_member": False,
                         "topic": {"value": ""}},
                    ], "response_metadata": {"next_cursor": ""}}))
c("  one call", len(calls), 1)
c("  to conversations.list", calls[0]["url"], "https://slack.com/api/conversations.list")
c("  public and private channels", calls[0]["params"].get("types"), "public_channel,private_channel")
c.truthy("  general, a member", "#general (member)" in text)
c.truthy("  private, not a member", "#secret-plans (private, not a member)" in text)
c.truthy("  topic kept short", "Company-wide" in text and "t" * 61 not in text)
text, _ = tool("slack_channels", {"limit": 3},
               (200, {"ok": True, "channels": [{"id": "C0AAAAAA1", "name": "a"}],
                      "response_metadata": {"next_cursor": "next1"}}),
               (200, {"ok": True, "channels": [{"id": "C0AAAAAA2", "name": "b"}],
                      "response_metadata": {"next_cursor": ""}}))
c("  follows the cursor", (len(calls), calls[1]["params"].get("cursor")), (2, "next1"))

print("\nChannel messages, by id:")
text, failed = tool("slack_channel_messages", {"channel": "C0CCCCCCC", "limit": 2},
                    (200, {"ok": True, "messages": [
                        {"user": "U0BBBBBBB", "text": "second, cc <@U0AAAAAAA>", "ts": TS2},
                        {"user": "U0AAAAAAA", "text": "first", "ts": TS1},
                        {"bot_id": "B1", "text": "deploy done", "ts": TS1},
                    ]}),
                    (200, {"ok": True, "user": {"name": "maria.l", "real_name": "Maria L",
                                                "profile": {"display_name": "Maria"}}}),
                    (200, {"ok": True, "user": {"name": "sam", "real_name": "Sam K",
                                                "profile": {"display_name": ""}}}))
c("  history, then one users.info per distinct user",
  [x["url"].rsplit("/", 1)[1] for x in calls], ["conversations.history", "users.info", "users.info"])
c("  history for the channel, with the limit", (calls[0]["params"]["channel"], calls[0]["params"]["limit"]),
  ("C0CCCCCCC", 2))
c("  looked up oldest first", [x["params"].get("user") for x in calls[1:]], ["U0AAAAAAA", "U0BBBBBBB"])
c("  not failed", failed, False)
c.truthy("  oldest first, by display name", text.index("Maria, %s: first" % when(TS1))
         < text.index("Sam K, %s: second" % when(TS2)))
c.truthy("  a mention resolved from the cache", "cc @Maria" in text)
c.truthy("  capped to the limit (the bot message dropped)", "deploy done" not in text)
text, _ = tool("slack_channel_messages", {"channel": "C0CCCCCCC"},
               (200, {"ok": True, "messages": [{"user": "U0AAAAAAA", "text": "a", "ts": TS1},
                                               {"user": "U0AAAAAAA", "text": "b", "ts": TS2}]}),
               (200, {"ok": True, "user": {"name": "maria"}}))
c("  the same user is looked up once", len(calls), 2)
c("  default limit is 15", calls[0]["params"]["limit"], 15)

print("\nChannel messages, by name:")
text, failed = tool("slack_channel_messages", {"channel": "#General"},
                    (200, {"ok": True, "channels": [{"id": "C0DDDDDDD", "name": "random"},
                                                    {"id": "C0CCCCCCC", "name": "general"}],
                           "response_metadata": {}}),
                    (200, {"ok": True, "messages": [{"username": "a webhook", "text": "hi", "ts": TS1}]}))
c("  list, then history", [x["url"].rsplit("/", 1)[1] for x in calls],
  ["conversations.list", "conversations.history"])
c("  the resolved id", calls[1]["params"]["channel"], "C0CCCCCCC")
c.truthy("  a message with no user id", "a webhook" in text)
text, failed = tool("slack_channel_messages", {"channel": "nowhere"},
                    (200, {"ok": True, "channels": [{"id": "C0CCCCCCC", "name": "general"}]}))
c.truthy("  an unknown name says so, no history call",
         "can't find a Slack channel called #nowhere" in text and len(calls) == 1)

print("\nChannels are checked before any request:")
for bad in ("", "C0CC/../x", "c0ccccccc?x=1", "U0AAAAAAA/x","#bad name", "X" * 100, "chat.postMessage",
            "../../api/chat.postMessage"):
    t1, _ = tool("slack_channel_messages", {"channel": bad})
    c("  %-28r no call" % bad, (len(calls), "isn't a Slack channel" in t1), (0, True))
for good in ("C0CCCCCCC", "G0EEEEEEE", "D0FFFFFFF"):
    tool("slack_channel_messages", {"channel": good}, (200, {"ok": True, "messages": []}))
    c("  %-28r goes straight to history" % good, [x["params"].get("channel") for x in calls], [good])

print("\nThe allow-list refuses writers without a request:")
for method in ("chat.postMessage", "chat.delete", "reactions.add", "pins.add", "files.upload",
               "conversations.create", "search.messages/../chat.postMessage", ""):
    calls.clear()
    try:
        slackapi._call(method, {"channel": "C0CCCCCCC", "text": "hi"})
        refused = False
    except ValueError:
        refused = True
    c("  %-40r refused, no call" % method, (refused, len(calls)), (True, 0))

print("\nWhen Slack says ok: false:")
for code in ("invalid_auth", "not_authed", "token_revoked"):
    text, failed = tool("slack_search", {"query": "x"}, (200, {"ok": False, "error": code}))
    c("  %s fails, pointing at SLACK_USER_TOKEN" % code, (failed, text),
      (True, "Slack refused the token. Check SLACK_USER_TOKEN."))
text, failed = tool("slack_channels", {}, (200, {"ok": False, "error": "missing_scope",
                                                 "needed": "channels:read", "provided": "search:read"}))
c.truthy("  missing_scope names the scope", "missing the channels:read scope" in text)
text, _ = tool("slack_search", {"query": "x"}, (200, {"ok": False, "error": "ratelimited"}))
c.truthy("  ratelimited says slow down", "slow down" in text)
text, _ = tool("slack_search", {"query": "x"}, (429, {}))
c.truthy("  HTTP 429 says slow down", "slow down" in text)
text, _ = tool("slack_search", {"query": "x"}, (200, {"ok": False, "error": "fatal_error"}))
c("  other errors give Slack's code", text, "Slack said no (fatal_error).")
text, _ = tool("slack_search", {"query": "x"}, (200, {"ok": False, "error": "bad <" + TOKEN + ">"}))
c.truthy("  an odd error string is filtered, not echoed", "<" not in text)
text, _ = tool("slack_search", {"query": "x"}, (500, {"error": TOKEN}))
c("  HTTP errors give only the code", text, "Slack said no (500).")
text, _ = tool("slack_search", {"query": "x"}, (200, {"messages": {"matches": [{"text": "not ok"}]}}))
c.truthy("  a body without ok is not success", "not ok" not in text)


def broken(*a, **k):
    raise httpx.ConnectError("could not connect with Bearer " + TOKEN)


slackapi.httpx.request = broken
text, failed = slackapi.run_tool("slack_search", {"query": "x"})
outputs.append(text)
c("  a network error fails, by type name only", (failed, text), (True, "Couldn't reach Slack: ConnectError"))
slackapi.httpx.request = fake_request
text, failed = tool("slack_search", {"query": "x", "limit": "ten"})
c("  a nonsense limit is wrong arguments, not a crash", (failed, len(calls)), (True, 0))
text, failed = tool("slack_nope", {})
c("  an unknown tool fails", failed, True)

print("\nA guest reaches nothing of the owner's workspace:")
whose.use(GUEST)
c("  not linked", links.linked("slack"), False)
c("  not connected", slackapi.connected(), False)
for name, args in (("slack_search", {"query": "x"}), ("slack_channels", {}),
                   ("slack_channel_messages", {"channel": "C0CCCCCCC"}),
                   ("slack_channel_messages", {"channel": "general"})):
    text, failed = tool(name, args, (200, {"ok": True}))
    c("  %-22s fails with no call" % name, (failed, len(calls)), (True, 0))
whose.use(OWNER)

print("\nNo token, no link:")
os.environ.pop("SLACK_USER_TOKEN")
text, failed = tool("slack_search", {"query": "x"}, (200, {"ok": True}))
c("  fails with no call", (failed, len(calls)), (True, 0))
os.environ["SLACK_USER_TOKEN"] = TOKEN

print("\nOnly reading, ever:")
every = []


def recording(method, url, **kw):
    every.append((method, url))
    return fake_request(method, url, **kw)


slackapi.httpx.request = recording
tool("slack_search", {"query": "a"}, (200, {"ok": True, "messages": {"matches": []}}))
tool("slack_channels", {}, (200, {"ok": True, "channels": []}))
tool("slack_channel_messages", {"channel": "general"},
     (200, {"ok": True, "channels": [{"id": "C0CCCCCCC", "name": "general"}]}),
     (200, {"ok": True, "messages": [{"user": "U0AAAAAAA", "text": "x", "ts": TS1}]}),
     (200, {"ok": True, "user": {"name": "a"}}))
slackapi.httpx.request = fake_request
c("  every call is a GET", {m for m, _ in every}, {"GET"})
c("  only the allowed methods", {u.replace(slackapi.API, "") for _, u in every},
  {"search.messages", "conversations.list", "conversations.history", "users.info"})
c("  the allow-list is exactly those four", set(slackapi._ALLOWED),
  {"search.messages", "conversations.list", "conversations.history", "users.info"})
names = {t["name"] for t in slackapi.TOOLS}
c("  READS is every tool", slackapi.READS, names)
c("  every tool dispatches", set(slackapi._DISPATCH), names)
c("  no tool name writes",
  [n for n in names if any(w in n for w in ("post", "send", "upload", "delete", "move", "copy",
                                            "share", "create", "react"))], [])
c.truthy("  every tool has a schema", all(t.get("input_schema", {}).get("type") == "object"
                                           for t in slackapi.TOOLS))

print("\nThe token never reaches a result:")
c.truthy("  %d outputs checked" % len(outputs), len(outputs) > 25)
c("  the token in none of them", [o for o in outputs if TOKEN in o or "secret-tok" in o], [])

c.done()
