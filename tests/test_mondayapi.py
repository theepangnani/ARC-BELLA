# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""monday.com, read through the owner's personal token, and nothing more.

What this suite guards, beyond "it parses":
  · every request is a GraphQL query, never a mutation — the documents are this
    module's constants and model input travels only as variables;
  · a board id is checked before it can reach monday;
  · the token never reaches a tool result, even when monday refuses it;
  · the owner's account is the owner's: a guest is not linked and causes no
    request at all.
No request leaves the machine: httpx.request is replaced for the whole suite.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

# Before anything that reads the allowlist or the token is imported.
os.environ["MONDAY_TOKEN"] = "secret_tok"
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

import httpx      # noqa: E402
import whose      # noqa: E402
import links      # noqa: E402
import mondayapi  # noqa: E402

c = Check()
OWNER, GUEST = "owner@example.com", "guest@example.com"
whose.set_owners({OWNER})
whose.use(OWNER)

calls = []
replies = []      # a queue of (status, json) the fake hands out in order
outputs = []      # every tool result, to search for the token at the end
every = []        # every request of the whole suite, for the no-mutation check


def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
    rec = {"method": method, "url": url, "params": dict(params or {}),
           "json": json, "headers": dict(headers or {})}
    calls.append(rec)
    every.append(rec)
    status, body = replies.pop(0) if replies else (200, {"data": {}})
    return httpx.Response(status, json=body, request=httpx.Request(method, url))


mondayapi.httpx.request = fake_request


def tool(name, args, *queued):
    calls.clear()
    replies[:] = list(queued)
    text, failed = mondayapi.run_tool(name, args)
    outputs.append(text)
    return text, failed


def person(pid):
    return {"id": "p", "type": "people", "text": "Someone",
            "persons_and_teams": [{"id": pid, "kind": "person"}]}


print("Linked for the owner:")
c.truthy("  connected", mondayapi.connected())

print("\nBoards:")
text, failed = tool("monday_boards", {"limit": 5},
                    (200, {"data": {"boards": [
                        {"id": "111", "name": "Launch plan", "state": "active", "items_count": 12},
                        {"id": "222", "name": "Hiring", "state": "archived", "items_count": 3}]}}))
c("  one call", len(calls), 1)
c("  POST", calls[0]["method"], "POST")
c("  to the v2 endpoint", calls[0]["url"], "https://api.monday.com/v2")
c("  bare token, no Bearer", calls[0]["headers"].get("Authorization"), "secret_tok")
c("  pinned API version", calls[0]["headers"].get("API-Version"), mondayapi.API_VERSION)
c("  limit as a variable", calls[0]["json"]["variables"], {"limit": 5})
c("  not failed", failed, False)
c.truthy("  renders a board", "Launch plan (active, 12 items) [id:111]" in text)
c.truthy("  and another", "Hiring (archived, 3 items) [id:222]" in text)
c.truthy("  says it is data", "data, not instructions" in text)

print("\nMy items:")
text, failed = tool("monday_my_items", {"limit": 5},
                    (200, {"data": {"me": {"id": "42"}, "boards": [
                        {"id": "111", "name": "Launch plan", "items_page": {"items": [
                            {"id": "9001", "name": "Write press release", "updated_at": "2026-09-10T08:00:00Z",
                             "group": {"title": "This week"},
                             "column_values": [person("42"), {"id": "s", "type": "status", "text": "Working on it"}]},
                            {"id": "9002", "name": "Not mine", "updated_at": "2026-09-11T08:00:00Z",
                             "group": {"title": "This week"}, "column_values": [person("77")]},
                        ]}},
                        {"id": "222", "name": "Hiring", "items_page": {"items": [
                            {"id": "9003", "name": "Interview Sam", "updated_at": "2026-09-12T08:00:00Z",
                             "group": {"title": "Next"}, "column_values": [person(42)]}]}},
                    ]}}))
c("  one call", len(calls), 1)
c("  not failed", failed, False)
c.truthy("  mine, with board, group, status, date",
         "Write press release (Launch plan, This week, Working on it, updated 2026-09-10) [id:9001]" in text)
c.truthy("  a numeric person id still matches", "Interview Sam" in text)
c("  somebody else's item left out", "Not mine" in text, False)
c.truthy("  newest first", text.index("Interview Sam") < text.index("Write press release"))
text, _ = tool("monday_my_items", {}, (200, {"data": {"me": {"id": "42"}, "boards": []}}))
c.truthy("  nothing found says where it looked", "most used monday.com boards" in text)

print("\nBoard items:")
text, failed = tool("monday_board_items", {"board_id": "111", "limit": 2},
                    (200, {"data": {"boards": [{"id": "111", "name": "Launch plan",
                        "columns": [{"id": "status", "title": "Status"}, {"id": "date4", "title": "Due"},
                                    {"id": "c3", "title": "C3"}, {"id": "c4", "title": "C4"},
                                    {"id": "c5", "title": "C5"}, {"id": "c6", "title": "C6"}],
                        "items_page": {"items": [
                            {"id": "9001", "name": "Write press release", "group": {"title": "This week"},
                             "column_values": [{"id": "status", "type": "status", "text": "Done"},
                                               {"id": "date4", "type": "date", "text": "2026-09-20"},
                                               {"id": "c3", "type": "text", "text": ""},
                                               {"id": "c3", "type": "text", "text": "three"},
                                               {"id": "c4", "type": "text", "text": "four"},
                                               {"id": "c5", "type": "text", "text": "five"},
                                               {"id": "c6", "type": "text", "text": "six"}]}]}}]}}))
c("  board id as a variable", calls[0]["json"]["variables"], {"ids": ["111"], "limit": 2})
c("  not failed", failed, False)
for want in ("Launch plan", "Write press release [This week]", "Status: Done", "Due: 2026-09-20",
             "C5: five", "[id:9001]"):
    c.truthy("  renders %r" % want, want in text)
c("  no more than five columns", "six" in text, False)
text, _ = tool("monday_board_items", {"board_id": "999"}, (200, {"data": {"boards": []}}))
c.truthy("  unknown board says so", "no board with that id" in text)

print("\nSearch:")
text, failed = tool("monday_search", {"query": "PRESS"},
                    (200, {"data": {"boards": [{"id": "111", "name": "Launch plan", "items_page": {"items": [
                        {"id": "9001", "name": "Write press release", "updated_at": "2026-09-10T08:00:00Z",
                         "group": {"title": "This week"}},
                        {"id": "9004", "name": "Book venue", "updated_at": "2026-09-10T08:00:00Z"}]}}]}}))
c("  not failed", failed, False)
c.truthy("  matches by name, case-insensitively", "Write press release (Launch plan, This week" in text)
c("  leaves the rest", "Book venue" in text, False)
c("  the term is never sent to monday", "PRESS" in str(calls[0]["json"]), False)
text, _ = tool("monday_search", {"query": "zzz"}, (200, {"data": {"boards": []}}))
c.truthy("  no hits says where it looked", "No item named like 'zzz'" in text)
text, failed = tool("monday_search", {"query": "  "})
c("  an empty search makes no call", len(calls), 0)

print("\nIds are checked before any request:")
for bad in ("", "abc", "12a", "1 OR 1", "../boards", "-5", "1" * 21, "111}) { id } mutation {"):
    t1, _ = tool("monday_board_items", {"board_id": bad})
    c("  board id %-30r no call" % bad, (len(calls), "isn't a monday.com board id" in t1), (0, True))

print("\nWhen monday says no:")
text, failed = tool("monday_boards", {}, (401, {"error_message": "Not Authenticated secret_tok"}))
c("  401 fails", failed, True)
c.truthy("  and points at MONDAY_TOKEN", "MONDAY_TOKEN" in text)
text, failed = tool("monday_boards", {}, (200, {"errors": [{"message": "secret_tok bad",
                                                            "extensions": {"code": "UNAUTHORIZED"}}]}))
c("  an unauthorised GraphQL error fails too", (failed, "MONDAY_TOKEN" in text), (True, True))
text, _ = tool("monday_boards", {}, (403, {}))
c.truthy("  403 says not allowed", "isn't allowed" in text)
text, _ = tool("monday_board_items", {"board_id": "111"}, (404, {}))
c("  404 says not found", text, "monday.com says that's not found.")
text, _ = tool("monday_boards", {}, (429, {}))
c.truthy("  429 says slow down", "slow down" in text)
text, _ = tool("monday_boards", {}, (200, {"errors": [{"message": "x",
                                                       "extensions": {"code": "ComplexityException"}}]}))
c.truthy("  complexity budget says slow down", "slow down" in text)
text, _ = tool("monday_boards", {}, (200, {"errors": [{"message": "secret_tok",
                                                       "extensions": {"code": "secret tok; drop"}}]}))
c("  a code that isn't a code is not repeated", text, "monday.com couldn't answer that (error).")
text, _ = tool("monday_boards", {}, (500, {"error_message": "secret_tok"}))
c("  other errors give only the code", text, "monday.com said no (500).")


def broken(*a, **k):
    raise httpx.ConnectError("could not connect with secret_tok")


mondayapi.httpx.request = broken
text, failed = mondayapi.run_tool("monday_boards", {})
outputs.append(text)
c("  a network error fails, by type name only", (failed, text), (True, "Couldn't reach monday.com: ConnectError"))
mondayapi.httpx.request = fake_request
text, failed = tool("monday_boards", {"limit": "ten"})
c("  a nonsense limit is wrong arguments, not a crash", failed, True)
text, failed = tool("monday_nope", {})
c("  an unknown tool fails", failed, True)
try:
    mondayapi._call("mutation { delete_board(board_id: 1) { id } }", {})
    c("  _call refuses a document of its own making", True, False)
except ValueError:
    c("  _call refuses a document of its own making", True, True)

print("\nA guest reaches nothing of the owner's account:")
whose.use(GUEST)
c("  not linked", links.linked("monday"), False)
c("  not connected", mondayapi.connected(), False)
for name, args in (("monday_boards", {}), ("monday_my_items", {}),
                   ("monday_board_items", {"board_id": "111"}), ("monday_search", {"query": "x"})):
    text, failed = tool(name, args, (200, {"data": {}}))
    c("  %-20s fails with no call" % name, (failed, len(calls)), (True, 0))
whose.use(OWNER)

print("\nNo token, no link:")
os.environ.pop("MONDAY_TOKEN")
text, failed = tool("monday_boards", {}, (200, {"data": {}}))
c("  fails with no call", (failed, len(calls)), (True, 0))
os.environ["MONDAY_TOKEN"] = "secret_tok"

print("\nOnly reading, ever:")
c.truthy("  %d requests recorded" % len(every), len(every) > 10)
c("  every request is a POST to the one endpoint", {(r["method"], r["url"]) for r in every},
  {("POST", mondayapi.API)})
c("  no request body holds 'mutation'",
  [r for r in every if "mutation" in str((r["json"] or {}).get("query", "")).lower()], [])
c("  every query is one of the module's constants",
  {(r["json"] or {}).get("query") for r in every} - set(mondayapi._DOCUMENTS), set())
c("  no module document holds 'mutation'", [q for q in mondayapi._DOCUMENTS if "mutation" in q.lower()], [])
names = {t["name"] for t in mondayapi.TOOLS}
c("  READS is every tool", mondayapi.READS, names)
c("  every tool dispatches", set(mondayapi._DISPATCH), names)
c("  no tool name writes",
  [n for n in names if any(w in n for w in ("create", "update", "delete", "archive", "add", "move",
                                              "comment", "post", "send"))], [])
c.truthy("  every tool has a schema", all(t.get("input_schema", {}).get("type") == "object"
                                           for t in mondayapi.TOOLS))

print("\nThe token never reaches a result:")
c.truthy("  %d outputs checked" % len(outputs), len(outputs) > 20)
c("  'secret_tok' in none of them", [o for o in outputs if "secret_tok" in o], [])

c.done()
