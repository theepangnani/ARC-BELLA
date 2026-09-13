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
# NARROWED ON THE OWNER'S INSTRUCTION ("all except my pc", 11 Sep 2026), and
# the list below is what survived that instruction rather than what is left
# over from it. Telegram READING, notes, todos, reminders, the phone push, the
# alarm clock, the watchlist and standing rules all moved to ALLOWED further
# down; each one is now asserted there, so nothing fell out of the suite.
#
# The three kinds of thing that did NOT move:
#   · tg_send_pending — a sent message goes out under the owner's name and
#     cannot be taken back. Drafting is a guest's; sending stays the owner's,
#     and that split is the whole reason this entry is still here.
#   · the PC and everything that is the PC wearing a different hat — the
#     shell, the keyboard, the screen, the second screen, the music.
#   · administration — export_everything is every file ARC holds.
FORBIDDEN = [
    ("tg_send_pending", "send Telegram as the owner"),
    # Lent on 11 Sep 2026, taken back on 12 Sep: there is one phone behind the
    # push and it is the owner's, so a guest's "send it to my phone" buzzed the
    # owner. Per-person storage cannot make it the guest's phone.
    ("notify_phone", "buzz the owner's phone"),
    ("export_everything", "export every file ARC holds"),
    ("self_repair", "rewrite the owner's files"),
    ("usage_report", "read the owner's spending"),
    ("show_on_display", "take over the owner's second screen"),
    ("clear_display", "clear the owner's second screen"),
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
    check("%-14s allowed" % name, name in run.guest_tools(), True)

print("\nAnd what the owner opened up afterwards, named just as explicitly —")
print("their OWN notes and memory (per account, so nothing of the owner's):")
for name in ["add_note", "list_notes", "delete_note", "list_memory", "forget"]:
    check("%-18s allowed" % name, name in run.guest_tools(), True)
check("notes really are per account", "whose.use(who)" in
      open(ARC / "run.py", encoding="utf-8").read(), True)

print("\n...their OWN lists, clocks and rules (per person since 12 Sep 2026 —")
print("   test_perperson.py proves none of it reaches the owner's), and the")
print("   owner's Telegram, which is the part with a cost:")
for name, what in [("add_todo", "their own list"),
                   ("list_todos", "their own list"),
                   ("complete_todo", "their own list"),
                   ("set_reminder", "said in their own tab"),
                   ("list_reminders", "their own reminders"),
                   ("cancel_reminder", "their own reminders"),
                   ("tg_list_chats", "the owner's chat list"),
                   ("tg_read_chat", "the owner's messages, read"),
                   ("tg_draft_message", "composed, not sent"),
                   ("set_alarm", "their own alarm clock"),
                   ("list_alarms", "their own alarms"),
                   ("cancel_alarm", "their own alarms"),
                   ("snooze_alarm", "their own ringing alarm"),
                   ("dismiss_alarm", "their own ringing alarm"),
                   ("set_price_alert", "their own watchlist"),
                   ("list_price_alerts", "their own watchlist"),
                   ("clear_price_alert", "their own watchlist"),
                   ("add_trigger", "their own standing rule; it can only notify"),
                   ("list_triggers", "their own standing rules"),
                   ("clear_trigger", "their own standing rules")]:
    check("%-18s allowed — %s" % (name, what), name in run.guest_tools(), True)

print("\nThe owner is not affected by any of this:")
# Three are checked for the guest only: dispatching them AS THE OWNER would
# really run them — a full export, a repair pass, a spend report — and a test
# that performs the act it is describing is a test that will one day do it
# somewhere it matters.
for name, _ in [x for x in FORBIDDEN
                if x[0] not in ("export_everything", "self_repair", "usage_report")]:
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
unknown = run.guest_tools() - known
check("no typos in GUEST_TOOLS (%s)" % (sorted(unknown) or "none"), unknown, set())

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
