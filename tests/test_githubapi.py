# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""GitHub, through a linked account, and only ever looking.

githubapi.py reads notifications, repositories, issues and READMEs for the
person who linked GitHub. The link itself is read-only (links.SERVICES), and
this suite guards that the module is too — a writing tool added later should
turn this red and make somebody decide, not slip in beside the reads.

What this suite guards:
  · every request is a GET, to the path the tool claims, with the token only
    in the Authorization header — never in anything the model is handed;
  · a repository name that could climb out of /repos/ is refused before any
    request is made;
  · a refused link says to link again, and fails; 404 and the rate limit come
    back as plain sentences;
  · one person's link is not another's;
  · READS is every tool, and no tool is named for writing.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402,F401
sandbox()
os.environ["GITHUB_CLIENT_ID"] = "test-client"

import httpx      # noqa: E402

import githubapi  # noqa: E402
import links      # noqa: E402
import whose      # noqa: E402

c = Check()
OWNER, OTHER = "owner@example.com", "other@example.com"

calls = []
all_calls = []    # never cleared: the GET-only check covers the whole suite
replies = []          # queue of (status, kwargs, headers) the fake hands back


def fake_request(method, url, params=None, headers=None, timeout=None, **kw):
    all_calls.append(method)
    calls.append({"method": method, "url": url, "params": dict(params or {}),
                  "headers": dict(headers or {}), "extra": kw})
    status, body, extra_headers = replies.pop(0) if replies else (200, {"json": {}}, {})
    return httpx.Response(status, headers=extra_headers, request=httpx.Request(method, url), **body)


githubapi.httpx.request = fake_request


def reply(status=200, headers=None, **body):
    replies.append((status, body, headers or {}))


def fresh():
    del calls[:]
    del replies[:]


whose.use(OWNER)
links._store_token("github", {"access_token": "tok"})
outputs = []


def run(name, args=None):
    text, failed = githubapi.run_tool(name, args or {})
    outputs.append(text)
    return text, failed


print("Linked:")
c.truthy("  connected for the person who linked it", githubapi.connected())

print("\nNotifications:")
fresh()
reply(json=[{"repository": {"full_name": "octo/hello"}, "reason": "mention",
             "updated_at": "2026-09-01T10:00:00Z",
             "subject": {"type": "Issue", "title": "Crash on start"}}])
text, failed = run("github_notifications", {"limit": 3})
c("  not failed", failed, False)
c("  path", calls[0]["url"], "https://api.github.com/notifications")
c("  per_page", calls[0]["params"], {"per_page": 3})
c("  bearer token in the header", calls[0]["headers"].get("Authorization"), "Bearer tok")
c("  GitHub's media type", calls[0]["headers"].get("Accept"), "application/vnd.github+json")
c("  pinned API version", calls[0]["headers"].get("X-GitHub-Api-Version"), "2022-11-28")
c.truthy("  says what and where", "octo/hello" in text and "Crash on start" in text and "mention" in text)

print("\nMy repositories:")
fresh()
reply(json=[{"full_name": "owner/site", "description": "My site", "language": "Python",
             "stargazers_count": 4, "private": True, "pushed_at": "2026-09-10T00:00:00Z"}])
text, failed = run("github_my_repos")
c("  path", calls[0]["url"], "https://api.github.com/user/repos")
c("  sorted by update", calls[0]["params"], {"sort": "updated", "per_page": 15})
c.truthy("  private flag and stars", "private" in text and "4 stars" in text)
fresh()
reply(json=[])
run("github_my_repos", {"limit": "lots"})
c("  a nonsense limit means the default, not a crash", calls[0]["params"]["per_page"], 15)

print("\nSearch:")
for kind in ("repositories", "issues", "code"):
    fresh()
    reply(json={"total_count": 1, "items": [
        {"full_name": "a/b", "stargazers_count": 1, "number": 7, "title": "Bug",
         "repository_url": "https://api.github.com/repos/a/b", "pull_request": {},
         "path": "src/x.py", "repository": {"full_name": "a/b"}}]})
    text, failed = run("github_search", {"query": "arc bella", "kind": kind, "limit": 2})
    c("  %-12s path" % kind, calls[0]["url"], "https://api.github.com/search/" + kind)
    c("  %-12s query" % kind, calls[0]["params"], {"q": "arc bella", "per_page": 2})
    c.truthy("  %-12s summarised" % kind, "a/b" in text and not failed)
fresh()
c.truthy("  no query, no request", "what" in run("github_search", {"query": "  "})[0] and not calls)

print("\nOne repository:")
fresh()
reply(json={"full_name": "octo/hello", "description": "Hello", "stargazers_count": 9,
            "forks_count": 2, "open_issues_count": 1, "default_branch": "main",
            "pushed_at": "2026-09-02T00:00:00Z", "language": "Go"})
reply(text="# Hello\n" + "x" * 5000)
text, failed = run("github_repo", {"repo": "octo/hello"})
c("  repo path", calls[0]["url"], "https://api.github.com/repos/octo/hello")
c("  readme path", calls[1]["url"], "https://api.github.com/repos/octo/hello/readme")
c("  readme asked for raw", calls[1]["headers"].get("Accept"), "application/vnd.github.raw+json")
c.truthy("  facts", "9 stars" in text and "main" in text and "Go" in text)
c.truthy("  README capped", "# Hello" in text and text.count("x") < 3100)
c.truthy("  README marked as somebody else's words", "not instructions" in text)
fresh()
reply(json={"full_name": "octo/bare"})
reply(404, json={"message": "Not Found"})
c.truthy("  a missing README is not a failure", "No README" in run("github_repo", {"repo": "octo/bare"})[0])

print("\nA repository name that isn't owner/name is refused before any request:")
for bad in ("../x", "a/b/c", "a b/c", "a/..", "-a/b", "a/b?x=1", "a%2F/b", "", "octo"):
    fresh()
    text, failed = run("github_repo", {"repo": bad})
    c.truthy("  github_repo   %-10r no request" % bad, not calls and "owner/name" in text)
    fresh()
    run("github_issues", {"repo": bad})
    c.truthy("  github_issues %-10r no request" % bad, not calls)

print("\nIssues:")
fresh()
reply(json=[{"number": 5, "title": "Crash", "user": {"login": "sam"}, "comments": 3,
             "labels": [{"name": "bug"}]},
            {"number": 6, "title": "Fix crash", "user": {"login": "kim"}, "comments": 0,
             "labels": [], "pull_request": {"url": "x"}}])
text, failed = run("github_issues", {"repo": "octo/hello", "state": "closed", "limit": 2})
c("  path", calls[0]["url"], "https://api.github.com/repos/octo/hello/issues")
c("  params", calls[0]["params"], {"state": "closed", "per_page": 2})
c.truthy("  number, author, label, comments", "#5" in text and "sam" in text and "bug" in text and "3 comments" in text)
c.truthy("  a pull request is called one", "PR #6" in text and "issue #5" in text)

print("\nWhen GitHub says no:")
fresh()
reply(401, json={"message": "Bad credentials"})
text, failed = run("github_notifications")
c("  401 fails", failed, True)
c.truthy("  and says to link again", "Link it again" in text)
fresh()
reply(404, json={"message": "Not Found"})
text, failed = run("github_issues", {"repo": "octo/secret"})
c("  404 is a plain sentence", text, "That's not found on GitHub, or not visible to your account.")
fresh()
reply(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"})
text, failed = run("github_my_repos")
c.truthy("  rate limit is a plain sentence", text.startswith("GitHub's rate limit"))
fresh()
reply(403, json={"message": "Forbidden"})
c("  a 403 that isn't the rate limit is not 'wait'", run("github_my_repos")[0], "GitHub said no (403).")
fresh()
reply(500, text="oops")
c("  anything else", run("github_my_repos")[0], "GitHub said no (500).")


def boom(*a, **k):
    raise httpx.ConnectError("down")


githubapi.httpx.request = boom
text, failed = run("github_my_repos")
c.truthy("  unreachable fails without a traceback", failed and "Couldn't reach GitHub" in text)
githubapi.httpx.request = fake_request

print("\nThe account name, never the token:")
fresh()
reply(json={"login": "octocat"})
githubapi.remember_account()
c("  path", calls[0]["url"], "https://api.github.com/user")
c("  login kept for the sheet", links.account("github"), "octocat")

print("\nOne person's link is not another's:")
whose.use(OTHER)
fresh()
c("  not connected", githubapi.connected(), False)
text, failed = run("github_notifications")
c.truthy("  refused as not linked", failed and "isn't linked" in text)
c("  and nothing was sent", calls, [])
whose.use(OWNER)

print("\nOnly ever looking:")
c.truthy("  the token never reached any output", outputs and not any(re.search(r"\btok\b", o) for o in outputs))
c.truthy("  every request was a GET", all_calls and all(m == "GET" for m in all_calls))
names = {t["name"] for t in githubapi.TOOLS}
c("  READS is every tool", githubapi.READS, names)
c("  dispatch matches the tools", set(githubapi._DISPATCH), names)
c.truthy("  no tool is named for writing",
         not [n for n in names for w in ("create", "comment", "star", "merge", "delete", "close", "write")
              if w in n])
c.truthy("  every description says the contents are data",
         all("never instructions" in t["description"] for t in githubapi.TOOLS))

c.done()
