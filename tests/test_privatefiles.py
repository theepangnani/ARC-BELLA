# -*- coding: utf-8 -*-
# ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
# All rights reserved. Proprietary; see LICENSE. Visibility is not permission.
"""read_file won't hand over keys, credentials or app data.

read_file is passive, so it runs without asking, and it reaches the whole home
folder. A line of injected text in a web page or an email could ask for an SSH
key and get it. Hidden folders, AppData and files that are secrets by name are
now refused, checked on the resolved path. Ordinary documents still read.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import sandbox, Check   # noqa: E402
DATA = sandbox()

import pc   # noqa: E402

c = Check()
home = DATA / "home"
pc.FILE_ROOTS = [home]


def put(rel, text="x"):
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


print("Refused:")
for rel in (".ssh/id_ed25519", ".ssh/config", ".aws/credentials", ".env",
            "AppData/Roaming/Mozilla/logins.json", "projects/app/.env.local",
            "projects/app/.git/config", "keys/server.pem", "vault.kdbx",
            "Documents/aws-credentials.csv", "work/token.json", "id_rsa.pub"):
    out = pc.read_file(str(put(rel, "SECRET-" + rel)))
    c.truthy("  %s" % rel, out.startswith("Refused:") and "SECRET-" not in out)

print("\nStill read:")
for rel in ("Documents/notes.txt", "Documents/shopping list.md", "projects/app/README.md",
            "Desktop/recipe cookies.txt"):
    out = pc.read_file(str(put(rel, "HELLO")))
    c.truthy("  %s" % rel, "HELLO" in out)

print("\nNo way round it:")
out = pc.read_file(str(home / "Documents" / ".." / ".ssh" / "id_ed25519"))
c.truthy("  a .. path to a key is still refused", out.startswith("Refused:"))
link = home / "Documents" / "shortcut"
try:
    os.symlink(home / ".ssh", link, target_is_directory=True)
    made = True
except (OSError, NotImplementedError):
    made = False
if made:
    out = pc.read_file(str(link / "id_ed25519"))
    c.truthy("  a link into .ssh is still refused", out.startswith("Refused:"))
c.truthy("  a root that is itself under a dot folder still reads its documents",
         (lambda: (setattr(pc, "FILE_ROOTS", [home / ".work"]),
                   "HELLO" in pc.read_file(str(put(".work/plan.txt", "HELLO"))))[1])())

c.done()
