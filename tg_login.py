#!/usr/bin/env python3
"""
One-time Telegram sign-in for ARC.

    python tg_login.py

You need an API id and hash first — they are free and take two minutes:

    1. Go to https://my.telegram.org and log in with your phone number
    2. Click "API development tools"
    3. Create an app (any name, e.g. ARC; platform: Desktop)
    4. Copy the api_id and api_hash

Put them in .env next to run.py:

    TG_API_ID=1234567
    TG_API_HASH=abcdef0123456789abcdef0123456789

Then run this script. It asks for your phone number, texts you a code, and (if
you have two-step verification on) your password. That writes arc.session —
which is full access to your Telegram account, so it is gitignored and must
never leave this machine.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

api_id = os.getenv("TG_API_ID", "").strip()
api_hash = os.getenv("TG_API_HASH", "").strip()

if not api_id or not api_hash:
    print("\n  TG_API_ID / TG_API_HASH are not set in .env.")
    print("  Get them from https://my.telegram.org -> API development tools,")
    print("  add them to .env, then run this again.\n")
    raise SystemExit(1)

from telethon import TelegramClient

print("\n  A code will be texted to your Telegram. Enter your phone in full")
print("  international form, e.g. +14165551234\n")

with TelegramClient(str(ROOT / "arc"), int(api_id), api_hash) as client:
    me = client.get_me()
    print(f"\n  Connected as {me.first_name}"
          f"{' @' + me.username if me.username else ''}.")
    print("  Restart ARC and it will have your Telegram.\n")
