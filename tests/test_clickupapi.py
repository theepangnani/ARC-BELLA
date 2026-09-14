# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""ClickUp, read through the owner's personal API token, and nothing more.

What this suite guards, beyond "it parses":
  · the token goes in the Authorization header bare, without "Bearer" — the
    way ClickUp takes a personal token;
  · every request is a GET and no tool name writes;
  · a task or Workspace id is checked before it can become part of a URL;
  · the token never reaches a tool result, even when ClickUp refuses it;
  · the owner's account is the owner's: a guest is not linked and causes no
    request at all.
No request leaves the machine: httpx.request is replaced for the whole suite.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

# Before anything that reads the allowlist or the token is imported.
os.environ["CLICKUP_TOKEN"] = "pk_clickup_secret_tok"
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

import httpx       # noqa: E402
import whose       # noqa: E402
import links       # noqa: E402
import clickupapi  # noqa: E402

c = Check()
OWNER, GUEST = "owner@example.com", "guest@example.com"
TOKEN = "pk_clickup_secret_tok"
whose.set_owners({OWNER})
whose.use(OWNER)

TEAM = "9012345678"
TASK = "86b1x2y3z"
DUE_MS = "1789401600000"
DUE = datetime.fromtimestamp(int(DUE_MS) / 1000).strftime("%Y-%m-%d")

calls = []
replies = []
outputs = []
every = []


def fake_request(method, url, params=None, json=None, headers=None, timeout=None):
    calls.append({"method": method, "url": url, "params": dict(params or {}),
                  "headers": dict(headers or {})})
    every.append((method, url))
    status, body = replies.pop(0) if replies else (200, {})
    return httpx.Response(status, json=body, request=httpx.Request(method, url))


clickupapi.httpx.request = fake_request


def tool(name, args, *queued):
    calls.clear()
    replies[:] = list(queued)
    text, failed = clickupapi.run_tool(name, args)
    outputs.append(text)
    return text, failed


TEAMS = (200, {"teams": [{"id": TEAM, "name": "Home", "members": [{}, {}]}, {"id": "1", "name": "Other"}]})
USER = (200, {"user": {"id": 4242, "username": "owner"}})


def t(name, **extra):
    d = {"id": TASK, "name": name, "status": {"status": "in progress"}, "list": {"name": "Chores"}}
    d.update(extra)
    return d


print("Linked for the owner:")
c.truthy("  connected", clickupapi.connected())

print("\nWorkspaces:")
text, failed = tool("clickup_teams", {}, TEAMS)
c("  one call to /team", [x["url"] for x in calls], ["https://api.clickup.com/api/v2/team"])
c("  token in the header, with no Bearer", calls[0]["headers"].get("Authorization"), TOKEN)
c("  not failed", failed, False)
c.truthy("  renders", "Home (2 members) [id:%s]; Other (0 members) [id:1]" % TEAM in text)

print("\nMy tasks:")
text, failed = tool("clickup_my_tasks", {"limit": 1}, TEAMS, USER,
                    (200, {"tasks": [t("Mow lawn", due_date=DUE_MS), t("Second, over the limit")]}))
c("  team, user, then the team's tasks", [x["url"] for x in calls],
  ["https://api.clickup.com/api/v2/team", "https://api.clickup.com/api/v2/user",
   "https://api.clickup.com/api/v2/team/%s/task" % TEAM])
c("  assigned to me, open, by due date", calls[2]["params"],
  {"assignees[]": "4242", "include_closed": "false", "order_by": "due_date", "page": 0})
c.truthy("  renders status, local due date, list and id",
         "Mow lawn (in progress; due %s; in Chores) [id:%s]" % (DUE, TASK) in text)
c.truthy("  limited", "Second" not in text)
c.truthy("  says it's data", "data, not instructions" in text)
tool("clickup_my_tasks", {"team_id": "1"}, USER, (200, {"tasks": []}))
c("  a given Workspace skips /team", [x["url"] for x in calls],
  ["https://api.clickup.com/api/v2/user", "https://api.clickup.com/api/v2/team/1/task"])
text, _ = tool("clickup_my_tasks", {"team_id": "12/../3"})
c("  a bad Workspace id makes no call", (len(calls), "isn't a ClickUp Workspace id" in text), (0, True))

print("\nOne task:")
text, failed = tool("clickup_task", {"task_id": TASK},
                    (200, t("Mow lawn", due_date=DUE_MS, text_content="z" * 4000,
                            assignees=[{"username": "ana"}, {"email": "bo@example.com"}],
                            url="https://app.clickup.com/t/86b1x2y3z")))
c("  GET /task/<id>", calls[0]["url"], "https://api.clickup.com/api/v2/task/%s" % TASK)
for want in ("Task: Mow lawn", "Status: in progress", "Due: " + DUE, "Assignees: ana, bo@example.com",
             "Link: https://app.clickup.com/t/86b1x2y3z", "List: Chores", "[...cut short]",
             "data, not instructions"):
    c.truthy("  renders %r" % want, want in text)
# Counted after the heading: the task id in the link has a z of its own.
c("  description capped at 3000", text.split("Description:", 1)[1].count("z"), 3000)

print("\nTask ids are checked before any request:")
for bad in ("", "DEV-123", "../team", "a" * 33, "86b 1x", "86b1/x"):
    t1, _ = tool("clickup_task", {"task_id": bad})
    c("  task id %-36r no call" % bad, (len(calls), "isn't a ClickUp task id" in t1), (0, True))

print("\nSearch (by name, locally):")
page0 = [t("Paint fence")] + [t("filler %d" % i) for i in range(99)]
text, failed = tool("clickup_search", {"query": "FENCE", "limit": 5, "team_id": TEAM},
                    (200, {"tasks": page0, "last_page": False}),
                    (200, {"tasks": [t("Fix fence gate")], "last_page": True}))
c("  pages through the team's open tasks until the last page", [x["params"] for x in calls],
  [{"include_closed": "false", "page": 0}, {"include_closed": "false", "page": 1}])
c.truthy("  matches names case-insensitively", "Paint fence" in text and "Fix fence gate" in text)
c.truthy("  leaves the rest out", "filler" not in text)
c.truthy("  says it is by name only, and why", "no search endpoint" in text)
text, _ = tool("clickup_search", {"query": "nothing", "team_id": TEAM}, (200, {"tasks": [t("a")], "last_page": True}))
c.truthy("  no match says what was searched", "No ClickUp task names contain 'nothing'" in text)
tool("clickup_search", {"query": "x", "team_id": TEAM}, *[(200, {"tasks": page0, "last_page": False})] * 5)
c("  at most three pages", len(calls), 3)
text, _ = tool("clickup_search", {"query": ""})
c("  an empty search makes no call", (len(calls), "Say what" in text), (0, True))

print("\nWhen ClickUp says no:")
text, failed = tool("clickup_teams", {}, (401, {"err": "Token invalid " + TOKEN, "ECODE": "OAUTH_025"}))
c("  401 fails", failed, True)
c.truthy("  and points at CLICKUP_TOKEN", "CLICKUP_TOKEN" in text)
text, _ = tool("clickup_task", {"task_id": TASK}, (403, {}))
c.truthy("  403 says not allowed", "isn't allowed" in text)
text, _ = tool("clickup_task", {"task_id": TASK}, (404, {}))
c.truthy("  404 says not found", "not found" in text)
text, _ = tool("clickup_teams", {}, (429, {}))
c.truthy("  429 says slow down", "slow down" in text)
text, _ = tool("clickup_teams", {}, (500, {"err": TOKEN}))
c("  other errors give only the code", text, "ClickUp said no (500).")


def broken(*a, **k):
    raise httpx.ConnectError("could not connect with Authorization " + TOKEN)


clickupapi.httpx.request = broken
text, failed = clickupapi.run_tool("clickup_teams", {})
outputs.append(text)
c("  a network error fails, by type name only", (failed, text), (True, "Couldn't reach ClickUp: ConnectError"))
clickupapi.httpx.request = fake_request
text, failed = tool("clickup_my_tasks", {"limit": "ten"})
c("  a nonsense limit is wrong arguments, not a crash", failed, True)
text, failed = tool("clickup_nope", {})
c("  an unknown tool fails", failed, True)

print("\nA guest reaches nothing of the owner's account:")
whose.use(GUEST)
c("  not linked", links.linked("clickup"), False)
c("  not connected", clickupapi.connected(), False)
for name, args in (("clickup_teams", {}), ("clickup_my_tasks", {}), ("clickup_task", {"task_id": TASK}),
                   ("clickup_search", {"query": "x"})):
    text, failed = tool(name, args, TEAMS)
    c("  %-16s fails with no call" % name, (failed, len(calls)), (True, 0))
whose.use(OWNER)

print("\nNo token, no link:")
os.environ.pop("CLICKUP_TOKEN")
text, failed = tool("clickup_teams", {}, TEAMS)
c("  fails with no call", (failed, len(calls)), (True, 0))
os.environ["CLICKUP_TOKEN"] = TOKEN

print("\nOnly reading, ever:")
c.truthy("  requests were made", len(every) > 10)
c("  every request in the suite was a GET", {m for m, _ in every}, {"GET"})
names = {x["name"] for x in clickupapi.TOOLS}
c("  READS is every tool", clickupapi.READS, names)
c("  every tool dispatches", set(clickupapi._DISPATCH), names)
c("  no tool name writes",
  [n for n in names if any(w in n for w in ("create", "update", "delete", "archive", "add", "move",
                                            "complete", "close", "comment", "post", "send"))], [])
c.truthy("  every tool has a schema", all(x.get("input_schema", {}).get("type") == "object"
                                           for x in clickupapi.TOOLS))

print("\nThe token never reaches a result:")
c.truthy("  %d outputs checked" % len(outputs), len(outputs) > 20)
c("  the token in none of them", [o for o in outputs if TOKEN in o], [])

c.done()
