# -*- coding: utf-8 -*-
"""Does the browser client actually parse?

The HUD is one file with several thousand lines of inline JavaScript and no
build step, so nothing between an editor and a phone ever checks it. A stray
bracket does not degrade: the whole script fails to parse, every listener goes
with it, and the page loads as a dark rectangle that answers nothing. It has
happened, and from the server side everything looks perfect while it does.

`python -m compileall` covers the Python half. This is the other half.

Parser, in order of preference: esprima if installed (pip install -r
requirements-dev.txt), else `node --check`, else say clearly that nothing was
checked rather than reporting a pass nobody earned.
"""
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

c = Check()
page = io.open(HUD, encoding="utf-8").read()

blocks = [(m.start(), m.group(1)) for m in
          re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S | re.I)
          if m.group(1).strip()]

print("%s: %d inline script block(s), %d chars of JavaScript"
      % (HUD.name, len(blocks), sum(len(b) for _, b in blocks)))
c.truthy("there is a script block at all", blocks)
c("none of them is a module (node --check parses classic scripts)",
  'type="module"' in page, False)


def line_of(offset):
    return page.count("\n", 0, offset) + 1


def parse_esprima(src):
    import esprima
    esprima.parseScript(src)


def parse_node(src):
    fd, path = tempfile.mkstemp(suffix=".js")
    os.close(fd)
    try:
        io.open(path, "w", encoding="utf-8").write(src)
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if r.returncode != 0:
            raise SyntaxError((r.stderr or r.stdout or "").strip()[:400])
    finally:
        os.unlink(path)


parser = None
try:
    import esprima                                   # noqa: F401
    parser, how = parse_esprima, "esprima"
except ImportError:
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
        parser, how = parse_node, "node --check"
    except Exception:
        how = None

if parser is None:
    print("\n  SKIPPED — no JavaScript parser available.")
    print("  Install one:  pip install -r requirements-dev.txt   (or have node on PATH)")
    print("  Nothing was checked, which is not the same as nothing being wrong.")
    c.done()

print("  parsed with %s\n" % how)
for start, src in blocks:
    try:
        parser(src)
        c.truthy("block at line %-5d parses (%d chars)" % (line_of(start), len(src)), True)
    except Exception as e:
        c("block at line %d parses" % line_of(start), str(e), "")

# Cheap structural checks the parser cannot make, because both of these are
# valid JavaScript that happens to be wrong.
body = blocks[0][1]
print("\nAnd the things a parser would happily accept:")
c("no leftover debugger statement", re.search(r"^\s*debugger\b", body, re.M) is not None, False)
c("no bare console.log left in (console.warn/error are deliberate)",
  len(re.findall(r"\bconsole\.log\(", body)), 0)
# There is no boot() to look for — boot IS the INITIALISE handler, and the
# microphone is requested inside it before the first await, which is the only
# moment Android will show the permission prompt.
c.truthy("the boot entry point survives",
         'el.initBtn.addEventListener("click"' in body and "state.booted = true;" in body)
c.truthy("  and still asks for the mic before any await",
         body.index("const micOK = await initAudio();")
         < body.index("await new Promise(r => setTimeout(r, 340));"))

c.done()
