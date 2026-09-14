# -*- coding: utf-8 -*-
"""Outlook mail, the Outlook calendar and OneDrive, through a linked account.

microsoft.py talks to Microsoft Graph with a token links.py keeps per person.
Nothing here reaches the network: httpx.request is swapped for a fake that
records what was asked and answers from a script.

What this suite guards, beyond "it calls the API":
  · it only ever READS — no tool may send, reply, draft, forward, move or
    remove mail, the same rule that keeps Gmail at gmail.readonly;
  · every tool is passive (READS is all of them), so none is quietly an action;
  · the token goes in the Authorization header and nowhere else — never into
    anything the model sees;
  · one person's link is never used for somebody else;
  · a big or binary OneDrive file is refused before it is downloaded;
  · Microsoft's refusals come back as plain sentences, and a rejected link
    says to link it again.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402,F401
sandbox()
os.environ["MS_CLIENT_ID"] = "test-client"

import httpx      # noqa: E402

import links      # noqa: E402
import microsoft  # noqa: E402
import whose      # noqa: E402

c = Check()
OWNER, OTHER = "owner@example.com", "other@example.com"
TOKEN = "tok"

calls = []
script = {}       # path fragment -> (status, json or bytes)


def fake_request(method, url, params=None, headers=None, **kw):
    calls.append({"method": method, "url": url, "params": dict(params or {}),
                  "headers": dict(headers or {})})
    req = httpx.Request(method, url)
    # Longest matching fragment wins, so ".../content" is not answered by the
    # item's own metadata.
    for frag in sorted(script, key=len, reverse=True):
        if frag in url:
            status, body = script[frag]
            if isinstance(body, bytes):
                return httpx.Response(status, content=body, request=req)
            return httpx.Response(status, json=body, request=req)
    return httpx.Response(404, json={}, request=req)


microsoft.httpx.request = fake_request


def run(name, **args):
    calls.clear()
    return microsoft.run_tool(name, args)


outputs = []

whose.use(OWNER)
links._store_token("microsoft", {"access_token": TOKEN, "expires_in": 3600})

print("Linked:")
c.truthy("  connected once a token is stored", microsoft.connected())

print("\nMail:")
script["/me/mailFolders/inbox/messages"] = (200, {"value": [
    {"id": "AAMk=1", "subject": "Lunch friday", "isRead": False,
     "from": {"emailAddress": {"name": "Pat", "address": "pat@example.com"}},
     "receivedDateTime": "2026-09-14T08:12:00Z", "bodyPreview": "Are we still on?"}]})
text, failed = run("outlook_search_mail")
outputs.append(text)
c("  inbox listing did not fail", failed, False)
c.truthy("  hits the inbox, newest first",
         calls[0]["url"].endswith("/v1.0/me/mailFolders/inbox/messages")
         and calls[0]["params"].get("$orderby") == "receivedDateTime desc")
c("  asks only for the fields it shows", calls[0]["params"].get("$select"),
  "id,subject,from,receivedDateTime,bodyPreview,isRead")
c("  the token goes in the Authorization header", calls[0]["headers"].get("Authorization"), "Bearer tok")
c.truthy("  sender, subject, preview and id are in the answer",
         all(s in text for s in ("Pat", "Lunch friday", "Are we still on?", "[id:AAMk=1]", "unread")))

script["/me/messages"] = (200, {"value": []})
text, failed = run("outlook_search_mail", query='invoice "march"', max_results=5)
outputs.append(text)
c.truthy("  a query searches all mail", calls[0]["url"].endswith("/v1.0/me/messages"))
c.truthy("  as a quoted $search phrase", calls[0]["params"].get("$search", "").startswith('"invoice'))
c("  with ConsistencyLevel: eventual", calls[0]["headers"].get("ConsistencyLevel"), "eventual")
c("  and no $orderby, which $search refuses", "$orderby" in calls[0]["params"], False)
c.truthy("  nothing found says so", "No Outlook mail" in text)

script["/me/messages/AAMk%3D1"] = (200, {
    "subject": "Lunch friday", "receivedDateTime": "2026-09-14T08:12:00Z",
    "from": {"emailAddress": {"name": "Pat", "address": "pat@example.com"}},
    "toRecipients": [{"emailAddress": {"name": "Owner", "address": OWNER}}],
    "body": {"contentType": "text", "content": "Are we still on? " + "x" * 500}})
text, failed = run("outlook_read_mail", message_id="AAMk=1", max_chars=200)
outputs.append(text)
c("  reading a message did not fail", failed, False)
c.truthy("  the id is percent-encoded into the path", calls[0]["url"].endswith("/me/messages/AAMk%3D1"))
c("  asks for the text body, not HTML", calls[0]["headers"].get("Prefer"), 'outlook.body-content-type="text"')
c.truthy("  the body is capped", "cut off" in text and text.count("x") < 300)
c.truthy("  and labelled as data, not instructions", "never instructions" in text)

print("\nCalendar:")
script["/me/calendarView"] = (200, {"value": [
    {"subject": "Dentist", "start": {"dateTime": "2026-09-14T09:00:00.0000000", "timeZone": "UTC"},
     "location": {"displayName": "High St"}},
    {"subject": "Standup", "start": {"dateTime": "2026-09-14T10:00:00.0000000", "timeZone": "UTC"}}]})
text, failed = run("outlook_events", days_ahead=2, query="dent")
outputs.append(text)
c.truthy("  hits calendarView", calls[0]["url"].endswith("/v1.0/me/calendarView"))
c.truthy("  with a start and an end", calls[0]["params"].get("startDateTime")
         and calls[0]["params"].get("endDateTime"))
c("  ordered by start", calls[0]["params"].get("$orderby"), "start/dateTime")
c.truthy("  the query filters locally", "Dentist" in text and "Standup" not in text)
# In the machine's local zone, like Google Calendar's: a UTC time read out
# beside local ones announces a 9am meeting at the wrong hour.
from datetime import datetime, timezone   # noqa: E402
local = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
c.truthy("  the time is said in local time", local in text)
c.truthy("  a zone Graph names other than UTC is kept as given",
         "09:00 Pacific Standard Time" in microsoft._local("2026-09-14T09:00:00.0000000",
                                                             "Pacific Standard Time"))

print("\nOneDrive:")
script["/me/drive/root/search"] = (200, {"value": [
    {"id": "F1", "name": "notes.md", "lastModifiedDateTime": "2026-09-01T12:00:00Z"}]})
text, failed = run("onedrive_search", query="Bob's notes")
outputs.append(text)
c.truthy("  hits drive search, quote doubled and encoded",
         "/me/drive/root/search(q='Bob%27%27s%20notes')" in calls[0]["url"])
c.truthy("  name, date and id in the answer", "notes.md" in text and "[id:F1]" in text)

script["/me/drive/items/F1"] = (200, {"id": "F1", "name": "notes.md", "size": 20,
                                      "file": {"mimeType": "text/markdown"}})
script["/me/drive/items/F1/content"] = (200, b"# Shopping\nmilk, eggs")
text, failed = run("onedrive_read", item_id="F1")
outputs.append(text)
c.truthy("  a text file is read", "milk, eggs" in text and not failed)
c.truthy("  metadata first, then content",
         [x["url"].split("/v1.0")[1] for x in calls] == ["/me/drive/items/F1", "/me/drive/items/F1/content"])

script["/me/drive/items/BIG"] = (200, {"id": "BIG", "name": "log.txt", "size": 50 * 1024 * 1024,
                                       "file": {"mimeType": "text/plain"}})
text, failed = run("onedrive_read", item_id="BIG")
outputs.append(text)
c.truthy("  a big file is refused", "too big" in text)
c.truthy("  ...without downloading it", not any("/content" in x["url"] for x in calls))

script["/me/drive/items/PDF"] = (200, {"id": "PDF", "name": "scan.pdf", "size": 1000,
                                       "file": {"mimeType": "application/pdf"}})
text, failed = run("onedrive_read", item_id="PDF")
outputs.append(text)
c.truthy("  a binary file is refused", "can only read plain text" in text)
c.truthy("  ...without downloading it", not any("/content" in x["url"] for x in calls))

print("\nThe account name, never the token:")
script["/me"] = (200, {"displayName": "Owner Person", "userPrincipalName": OWNER})
calls.clear()
microsoft.remember_account()
c.truthy("  asks /me", calls and calls[0]["url"].endswith("/v1.0/me"))
c("  keeps the display name", links.account("microsoft"), "Owner Person")

print("\nWhen Microsoft says no:")
script["/me/messages/GONE"] = (401, {})
text, failed = run("outlook_read_mail", message_id="GONE")
outputs.append(text)
c("  401 fails", failed, True)
c.truthy("  and says to link it again", "Link it again" in text)
for status, want in ((403, "permission"), (404, "couldn't find"), (429, "slow down"), (500, "said no (500)")):
    script["/me/messages/E%d" % status] = (status, {})
    text, failed = run("outlook_read_mail", message_id="E%d" % status)
    outputs.append(text)
    c.truthy("  %d is a plain sentence" % status,
             want in text and text.endswith(".") and "{" not in text)

c.truthy("  the token never appears in anything the model sees",
         not any(TOKEN in o for o in outputs))

print("\nSomebody else's link is not theirs to use:")
whose.use(OTHER)
c("  not connected for another person", microsoft.connected(), False)
for name in ("outlook_search_mail", "outlook_events", "onedrive_search"):
    text, failed = run(name, query="x")
    c.truthy("  %s fails with 'isn't linked'" % name, failed and "isn't linked" in text)
    c("  ...and never called Microsoft with the owner's token", calls, [])
whose.use(OWNER)

print("\nRead-only, by construction:")
names = [t["name"] for t in microsoft.TOOLS]
c.truthy("  every tool is outlook_ or onedrive_",
         all(n.startswith(("outlook_", "onedrive_")) for n in names))
BANNED = ("send", "reply", "delete", "move", "draft", "forward", "flag", "write", "upload", "remove")
c("  no tool name can send, reply, draft, forward, move or remove",
  [n for n in names if any(b in n for b in BANNED)], [])
# Substrings, deliberately blunt: "sender" trips "send" too. A description is
# the model's whole idea of what a tool can do, so it is kept clear of the
# words rather than argued with case by case.
c("  nor does any description offer to",
  [t["name"] for t in microsoft.TOOLS if any(b in t["description"].lower() for b in BANNED)], [])
c("  READS is every tool (all passive)", microsoft.READS, set(names))
c("  every tool dispatches", set(microsoft._DISPATCH), set(names))
c.truthy("  the scope asks only to read mail",
         "Mail.Read" in links.SERVICES["microsoft"]["scopes"]
         and not any(s in ("Mail.ReadWrite", "Mail.Send", "Files.ReadWrite", "Calendars.ReadWrite")
                     for s in links.SERVICES["microsoft"]["scopes"]))
c.truthy("  the module uses GET and nothing else",
         '"POST"' not in open(ARC / "microsoft.py", encoding="utf-8").read()
         and '"PATCH"' not in open(ARC / "microsoft.py", encoding="utf-8").read()
         and '"DELETE"' not in open(ARC / "microsoft.py", encoding="utf-8").read())

c.done()
