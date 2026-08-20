# -*- coding: utf-8 -*-
"""The guest tier, checked against the real run.py rather than a copy of it.

Two things matter and they are different questions:
  1. Is the guest OFFERED only safe tools?          (all_tools)
  2. Is the guest STOPPED if a denied tool is run?  (dispatch_tool)
A test that only covers (1) proves nothing — the tool list is rebuilt per turn
from a client-shaped request, so (2) is the check that actually holds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"
os.environ["ARC_GUEST_EMAILS"] = "guest@example.com"

import run  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok = ok and good
    print(("  PASS  " if good else "  FAIL  ") + label +
          ("" if good else "   got %r want %r" % (got, want)))


print("A guest listed in ARC_GUEST_EMAILS can still sign in:")
check("guest is on the allowlist", "guest@example.com" in run.ALLOWED_EMAILS, True)
check("owner is on the allowlist", "owner@example.com" in run.ALLOWED_EMAILS, True)
check("owner is NOT a guest", "owner@example.com" in run.GUEST_EMAILS, False)

print("\nWhat each account is offered (local desktop, every kit up):")
owner_tools = {t["name"] for t in run.all_tools(local=True, guest=False)}
guest_tools = {t["name"] for t in run.all_tools(local=True, guest=True)}
print("    owner: %d tools     guest: %d tools" % (len(owner_tools), len(guest_tools)))
check("guest offered strictly fewer", len(guest_tools) < len(owner_tools), True)
check("guest list is a subset of what exists", guest_tools <= owner_tools, True)

# The point of the whole exercise, named one by one so a future edit that
# re-opens one of these fails loudly instead of quietly.
print("\nThe owner's life is off limits — offered AND dispatched:")
FORBIDDEN = [
    ("tg_send_pending", "send Telegram as the owner"),
    ("tg_draft_message", "draft Telegram as the owner"),
    ("tg_read_chat", "read the owner's Telegram"),
    ("tg_list_chats", "list the owner's chats"),
    ("add_note", "write to the owner's memory"),
    ("list_notes", "read the owner's memory"),
    ("delete_note", "delete the owner's memory"),
    ("add_todo", "add to the owner's todos"),
    ("list_todos", "read the owner's todos"),
    ("complete_todo", "complete the owner's todos"),
    ("set_reminder", "set a reminder on the owner"),
    ("list_reminders", "read the owner's reminders"),
    ("cancel_reminder", "cancel the owner's reminders"),
    ("notify_phone", "push to the owner's phone"),
    ("show_on_display", "take over the owner's second screen"),
    ("clear_display", "clear the owner's second screen"),
    ("set_price_alert", "edit the owner's watchlist"),
    ("list_price_alerts", "read the owner's watchlist"),
    ("clear_price_alert", "clear the owner's watchlist"),
    ("run_prepared", "run shell on the owner's PC"),
    ("prepare_command", "stage shell on the owner's PC"),
    ("screenshot", "see the owner's screen"),
    ("read_file", "read the owner's files"),
    ("find_files", "search the owner's disk"),
    ("keyboard", "type on the owner's PC"),
    ("mouse_control", "click on the owner's PC"),
    ("clipboard", "read the owner's clipboard"),
    ("open_app", "launch apps on the owner's PC"),
    ("system_control", "change the owner's system settings"),
    ("spotify", "control the owner's playback"),
    ("youtube", "control the owner's playback"),
]
for name, what in FORBIDDEN:
    offered = name in guest_tools
    out, failed = run.dispatch_tool(name, {}, local=True, guest=True)
    refused = failed and "guest account" in out.lower()
    check("%-18s not offered" % name, offered, False)
    check("%-18s refused at dispatch (%s)" % (name, what), refused, True)

print("\nWhat a guest DOES get — their own Google account and public lookups:")
for name in ["list_events", "create_event", "move_event", "cancel_event",
             "search_email", "read_email", "find_contact", "find_drive",
             "read_drive", "weather", "stock", "news", "web_search"]:
    check("%-14s allowed" % name, name in run.GUEST_TOOLS, True)

print("\nThe owner is not affected by any of this:")
for name, _ in FORBIDDEN:
    out, failed = run.dispatch_tool(name, {}, local=True, guest=False)
    blocked = failed and "guest account" in out.lower()
    check("%-18s still reaches the owner's toolkit" % name, blocked, False)

print("\nDefault-deny: an unknown//future tool is refused to a guest:")
out, failed = run.dispatch_tool("some_tool_added_next_year", {}, local=True, guest=True)
check("unknown tool refused", failed and "guest account" in out.lower(), True)

print("\nNo guests configured means nothing changes for anybody:")
saved = run.GUEST_EMAILS
run.GUEST_EMAILS = set()
check("all_tools(guest=False) unchanged",
      {t["name"] for t in run.all_tools(local=True)} == owner_tools, True)
run.GUEST_EMAILS = saved

print("\nEvery guest tool is a real tool that exists:")
known = {t["name"] for kit in run.TOOLKITS for t in kit.TOOLS} | {"web_search"}
unknown = run.GUEST_TOOLS - known
check("no typos in GUEST_TOOLS (%s)" % (sorted(unknown) or "none"), unknown, set())

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
