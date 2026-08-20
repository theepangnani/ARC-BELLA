# -*- coding: utf-8 -*-
"""Exercise the board/progress/learner directive regexes against the kind of
replies the model will actually produce."""
import re
import sys
import os

FENCED = re.compile(r"\[\[\s*board\s*:\s*([^\]\n]*?)\s*\]\][ \t]*\r?\n?([\s\S]*?)\[\[\s*/\s*board\s*\]\]", re.I)
OPEN   = re.compile(r"\[\[\s*board\s*:\s*([^\]\n]*?)\s*\]\][ \t]*\r?\n?([\s\S]*)\Z", re.I)
PROG   = re.compile(r"\[\[\s*progress\s*:\s*([^\]]+?)\s*\]\]", re.I)
LEARN  = re.compile(r"\[\[\s*learner\s*:\s*([^\]]+?)\s*\]\]", re.I)
STRIP  = re.compile(r"[*_#`]")

boards = []

def norm(body):
    """What addBoard() does to the captured body before rendering it."""
    body = re.sub(r"^\s*\n", "", body)
    return re.sub(r"\s+$", "", body)

def extract(text):
    del boards[:]
    out = FENCED.sub(lambda m: boards.append((m.group(1), norm(m.group(2)))) or "", text)
    out = OPEN.sub(lambda m: boards.append((m.group(1), norm(m.group(2)))) or "", out)
    prog = PROG.findall(out); out = PROG.sub("", out)
    lrn  = LEARN.findall(out); out = LEARN.sub("", out)
    return STRIP.sub("", out).strip(), list(boards), prog, lrn

def check(name, got, want):
    print(("  PASS  " if got == want else "  FAIL  ") + name)
    if got != want:
        print("        got  %r" % (got,))
        print("        want %r" % (want,))
    return got == want

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox   # noqa: E402
sandbox()

ok = True

# 1 — the ordinary case: prose spoken, code shown, symbols intact.
spoken, b, _, _ = extract(
    'Right. Type that on your screen and run it.\n'
    '[[board: your first line]]\n'
    'name = input("What is your name? ")\n'
    'print("Hello " + name)\n'
    '[[/board]]')
ok &= check("prose kept", spoken, "Right. Type that on your screen and run it.")
ok &= check("snake_case + quotes survive", b[0][1],
            'name = input("What is your name? ")\nprint("Hello " + name)')
ok &= check("title", b[0][0], "your first line")

# 2 — code containing ]] , which is exactly why the fence exists.
spoken, b, _, _ = extract('Look at line two.\n[[board: nested]]\nx = grid[i[j]]\ny = a[b[c[d]]]\n[[/board]]\nRun it.')
ok &= check("]] inside code not truncated", b[0][1], "x = grid[i[j]]\ny = a[b[c[d]]]")
ok &= check("prose either side kept", spoken, "Look at line two.\n\nRun it.")

# 3 — a # comment and an * survive the board but are stripped from speech.
spoken, b, _, _ = extract('That is a comment.\n[[board: comments]]\n# this line is ignored\ntotal = 3 * 4\n[[/board]]')
ok &= check("hash and star survive on board", b[0][1], "# this line is ignored\ntotal = 3 * 4")

# 4 — model forgot the closing fence.
spoken, b, _, _ = extract('Try this.\n[[board: oops]]\nfor i in range(3):\n    print(i)')
ok &= check("unclosed fence still captured", b[0][1], "for i in range(3):\n    print(i)")
ok &= check("no raw directive spoken", spoken, "Try this.")

# 5 — two boards in one reply.
spoken, b, _, _ = extract('[[board: before]]\nx = 1\n[[/board]]\nand after:\n[[board: after]]\nx = 2\n[[/board]]')
ok &= check("two boards", [x[1] for x in b], ["x = 1", "x = 2"])

# 6 — indentation preserved exactly (the thing Python cannot survive losing).
spoken, b, _, _ = extract('[[board: indent]]\ndef f():\n    if True:\n        return 1\n[[/board]]')
ok &= check("indentation exact", b[0][1], "def f():\n    if True:\n        return 1")

# 7 — progress and learner.
spoken, _, prog, lrn = extract(
    'Nice - you spotted that yourself.\n'
    '[[progress: coding | loops | got-it | writes a for loop unaided, still shaky on while]]')
ok &= check("progress captured", prog, ["coding | loops | got-it | writes a for loop unaided, still shaky on while"])
ok &= check("progress not spoken", spoken, "Nice - you spotted that yourself.")
spoken, _, _, lrn = extract("Hello Maya.\n[[learner: Maya]]")
ok &= check("learner captured", lrn, ["Maya"])

# 8 — a reply with no directives at all is untouched apart from markdown.
spoken, b, _, _ = extract("It is twenty-two degrees and *quite* bright.")
ok &= check("plain reply", spoken, "It is twenty-two degrees and quite bright.")
ok &= check("no board", b, [])

# 9 — the field split the client does on a progress directive.
parts = [p.strip() for p in prog[0].split("|")] if prog else []
spoken, _, prog, _ = extract("[[progress: digital | scams | learning | can spot a fake sender]]")
parts = [p.strip() for p in prog[0].split("|")]
ok &= check("progress fields", parts, ["digital", "scams", "learning", "can spot a fake sender"])

# 10 — a board that QUOTES a directive must display it, not execute it.
#      (Before the reordering, the remember rule ran first and ate this.)
REMEMBER = re.compile(r"\[\[\s*remember\s*:\s*([^\]]+?)\s*\]\]", re.I)
remembered = []
def extract_with_remember(text):
    del boards[:], remembered[:]
    out = FENCED.sub(lambda m: boards.append((m.group(1), norm(m.group(2)))) or "", text)
    out = REMEMBER.sub(lambda m: remembered.append(m.group(1)) or "", out)
    out = OPEN.sub(lambda m: boards.append((m.group(1), norm(m.group(2)))) or "", out)
    return STRIP.sub("", out).strip(), list(boards), list(remembered)

spoken, b, rem = extract_with_remember(
    'This is how I save a fact.\n'
    '[[board: the memory marker]]\n'
    '[[remember: Maya is learning Python]]\n'
    '[[/board]]')
ok &= check("quoted directive shown verbatim", b[0][1], "[[remember: Maya is learning Python]]")
ok &= check("and NOT executed", rem, [])

# but a real one outside the board still fires
spoken, b, rem = extract_with_remember("Noted.\n[[remember: Maya prefers short answers]]")
ok &= check("real remember still fires", rem, ["Maya prefers short answers"])

print("\nALL PASS" if ok else "\nFAILURES ABOVE")
sys.exit(0 if ok else 1)
