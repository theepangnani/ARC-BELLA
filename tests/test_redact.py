# -*- coding: utf-8 -*-
"""Passwords, keys and card numbers, kept out of ARC's own files.

Two places wrote them down without anybody deciding they should:

  · THE SERVER LOG. Every tool call was printed with its arguments and the
    guardian appends the output to arc-server.log, so "type my password" became
    keyboard {'text': 'hunter2'} in a plain file on disk.
  · MEMORY. A remembered fact is sent back to the model at the top of every
    turn afterwards, from a plain file. A secret remembered is a secret
    repeated, for as long as the fact lives.

The detector is deliberately NARROW, and half of this suite is about what it
must NOT catch. A redactor that fires on every long number strips phone numbers
and order references out of the log — and a log with the useful half redacted
is no longer worth reading, which is its own way of being unsafe.
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import redact   # noqa: E402
import memory   # noqa: E402

c = Check()

print("What IS a secret:")
for text in ["my visa is 4111 1111 1111 1111",
             "card 4111-1111-1111-1111",
             "5500005555555559",
             "my wifi password is hunter2",
             "the PIN: 4412",
             "passcode=8812",
             "the verification code is 552199",
             "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
             "ghp_abcdefghijklmnopqrstuvwxyz0123",
             "AKIAABCDEFGHIJKLMNOP"]:
    c("  %-42s" % text[:42], redact.looks_secret(text), True)

print("\nWhat is NOT, and must not be treated as one:")
for text in ["call me on 416 555 0199",
             "order ref 1234567890123",            # 13 digits, fails Luhn
             "4111 1111 1111 1112",                # a card shape that fails Luhn
             "my password manager is 1Password",
             "I forgot my password",
             "the meeting is at 1430",
             "I like my coffee black",
             "flight AC 856 departs 21:40"]:
    c("  %-42s" % text[:42], redact.looks_secret(text), False)

print("\nScrubbing keeps the sentence and loses the value:")
c("  a card", redact.scrub("pay with 4111 1111 1111 1111 please"),
  "pay with [redacted card] please")
c("  a password", redact.scrub("the password is hunter2 ok"),
  "the password is [redacted] ok")
c("  a key", redact.scrub("use sk-ant-api03-abcdefghijklmnopqrstuv now"),
  "use [redacted key] now")
c("  ordinary text is untouched", redact.scrub("call me on 416 555 0199"),
  "call me on 416 555 0199")

print("\nThe log line:")
c("  what was typed is never printed", redact.for_log("keyboard", {"text": "hunter2"}),
  "{'text': '<7 chars>'}")
c("  nor what went on the clipboard",
  redact.for_log("clipboard", {"action": "write", "text": "my secret note"}),
  "{'action': 'write', 'text': '<14 chars>'}")
c.truthy("  nor a drafted message",
         "<" in redact.for_log("tg_draft_message", {"to": "Sam", "text": "hi there"}))
c("  an ordinary tool is logged as it was", redact.for_log("weather", {"place": "Markham"}),
  "{'place': 'Markham'}")
c.truthy("  a secret in any OTHER tool's arguments is still caught",
         "[redacted card]" in redact.for_log("web_search", {"q": "4111 1111 1111 1111"}))
c.truthy("  and a long opaque token is too, in the log only",
         "[redacted token]" in redact.for_log("read_drive", {"id": "a" * 40}))
c("  it never raises on odd input", isinstance(redact.for_log("x", None), str), True)
src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  run.py prints through it", "redact.for_log(call.name, call.input)" in src)
c("  ...and nowhere prints the raw arguments", "str(call.input)" in src, False)

print("\nMemory refuses a secret, whole:")
memory.use("owner@example.com")
said = memory.remember("my wifi password is hunter2")
c.truthy("  refused", said.startswith("I don't keep passwords"))
c.truthy("  saying why", "every turn" in said)
c("  and not stored", any("hunter2" in (f.get("text") or "") for f in memory.facts()), False)
c.truthy("  a card number too", memory.remember("my card is 4111 1111 1111 1111")
         .startswith("I don't keep"))
c.truthy("  an ordinary fact is still kept", memory.remember("I take my coffee black")
         .startswith("Noted"))
c.truthy("  and so is a fact ABOUT a password",
         memory.remember("my password manager is 1Password").startswith("Noted"))
# An import must not report a refused secret as imported.
n = memory.import_facts(["my PIN: 4412", "I live in Markham"])
c("  an import counts only what was actually kept", n, 1)

c.done()
