#!/usr/bin/env python3
"""Passwords, keys and card numbers: noticed, and kept out of plain text.

Two places were writing them down without anybody deciding they should:

  · THE SERVER LOG. Every tool call is printed with its arguments, and the
    guardian appends everything printed to arc-server.log. So "type my
    password" became a line reading  keyboard {'text': 'hunter2'}  in a plain
    file on disk, next to 134 identical connection tracebacks where nobody
    would ever look — which is not the same thing as safe.
  · MEMORY. "[[remember: my wifi password is ...]]" went into memory.json and
    was then sent back to the model at the top of every turn afterwards. A
    secret remembered is a secret repeated, indefinitely.

WHAT THIS CAN'T DO, said plainly because it is the part people assume: by the
time ARC has been asked to type a password, the password has already been said
to her, and a spoken or typed request goes to the model to be understood. This
keeps it out of ARC's own files. It does not un-send it. The only way to keep a
password entirely between you and the keyboard is to type it yourself.

Detection is deliberately narrow — card numbers that pass the Luhn check, key
shapes the providers actually issue, and a secret word followed by a value. A
detector that fired on every long number would redact phone numbers and order
references out of the log, and then the log would be no use for the one thing
it is for.
"""

import re

# 13-19 digits, optionally grouped by spaces or dashes. Checked with Luhn below,
# which is what separates a card number from a long order reference.
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")

# Key shapes as issued: OpenAI/Anthropic/Stripe style, GitHub, Slack, AWS, Google.
_KEY = re.compile(
    r"\b(?:sk|pk|rk)[-_](?:[A-Za-z0-9]+[-_])*[A-Za-z0-9]{12,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bAIza[0-9A-Za-z_\-]{30,}")

# "password is X", "PIN: 4412", "my passcode = hunter2". The value is what goes.
_PHRASE = re.compile(
    r"(\b(?:password|passwd|passcode|passphrase|pin|pin code|api key|secret|"
    r"security code|cvv|cvc|otp|one[- ]time code|2fa code|verification code)\b"
    r"(?:\s+(?:is|was|=|:)|\s*[:=])\s*)(\S+)", re.I)

# For the LOG only: long opaque tokens (session ids, bearer tokens). Too broad
# for memory, where it would refuse ordinary facts that happen to hold a URL.
_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _cards(text: str):
    for m in _CARD.finditer(text):
        d = re.sub(r"\D", "", m.group(0))
        if 13 <= len(d) <= 19 and _luhn(d):
            yield m


def looks_secret(text) -> bool:
    """Whether this holds a card number, an issued key, or a secret word with a
    value. Narrow on purpose — see the module docstring."""
    t = text if isinstance(text, str) else str(text or "")
    return bool(any(True for _ in _cards(t)) or _KEY.search(t) or _PHRASE.search(t))


def scrub(text, tokens: bool = False) -> str:
    """The same text with anything secret-looking replaced by [redacted]."""
    t = text if isinstance(text, str) else str(text or "")
    for m in reversed(list(_cards(t))):
        t = t[:m.start()] + "[redacted card]" + t[m.end():]
    t = _KEY.sub("[redacted key]", t)
    t = _PHRASE.sub(lambda m: m.group(1) + "[redacted]", t)
    if tokens:
        t = _TOKEN.sub("[redacted token]", t)
    return t


# Tools whose text arguments are never printed at all, secret-looking or not.
# What someone asked ARC to type or put on the clipboard is theirs; the log
# needs to know that it happened and how long it was, not what it said.
_TEXT_ARGS = {
    "keyboard": ("text",),
    "clipboard": ("text",),
    "message_app": ("text",),
    "tg_draft_message": ("text", "message"),
}


def for_log(name: str, args) -> str:
    """A tool call's arguments, safe to print to a file on disk."""
    try:
        a = dict(args or {})
    except Exception:
        return scrub(str(args), tokens=True)[:90]
    for k in _TEXT_ARGS.get(name, ()):
        if isinstance(a.get(k), str):
            a[k] = "<%d chars>" % len(a[k])
    return scrub(str(a), tokens=True)[:90]
