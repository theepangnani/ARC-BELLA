# -*- coding: utf-8 -*-
"""Does the browser client actually parse?

The HUD is one file with several thousand lines of inline JavaScript and no
build step, so nothing between an editor and a phone ever checks it. A stray
bracket does not degrade: the whole script fails to parse, every listener goes
with it, and the page loads as a dark rectangle that answers nothing. It has
happened, and from the server side everything looks perfect while it does.

`python -m compileall` covers the Python half. This is the other half.

Parser, in order of preference: `node --check` if node is on PATH, else esprima
(pip install -r requirements-dev.txt), else say clearly that nothing was checked
rather than reporting a pass nobody earned.

node leads because esprima is from 2017 and rejects unicode property escapes —
`\\p{L}` — which is how the echo checks are written now so they work in scripts
other than Latin. Where only esprima is present the block is re-parsed with
those escapes neutralised, so everything else is still genuinely checked and
the gap is named rather than papered over.
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


# node first, esprima second. esprima is from 2017 and rejects anything newer
# than ES2017 — including \p{L} unicode property escapes, which is how the
# echo checks are written now so they work in scripts other than Latin. A
# parser that calls valid code invalid is worse than no parser, so where node
# exists it wins.
parser = None
try:
    subprocess.run(["node", "--version"], capture_output=True, check=True)
    parser, how = parse_node, "node --check"
except Exception:
    try:
        import esprima                               # noqa: F401
        parser, how = parse_esprima, "esprima"
    except ImportError:
        how = None


# Constructs esprima cannot parse but every browser ARC supports can. Used only
# to explain an esprima failure, never to excuse node's.
ES_NEWER = ("Invalid regular expression", "Unexpected token )")

# \p{L}, \p{N}, \P{Lu} ... — swapped out so the rest of the block can still
# be parsed by a 2017 parser.
NEUTRALISE = re.compile(r"\\[pP]\{[A-Za-z_]+\}")


def modern_only(src, msg):
    """Is this esprima being old rather than the code being wrong?"""
    BS = chr(92)
    return how == "esprima" and any(m in msg for m in ES_NEWER) and (BS + "p{") in src

if parser is None:
    print("\n  SKIPPED — no JavaScript parser available.")
    print("  Install one:  pip install -r requirements-dev.txt   (or have node on PATH)")
    print("  Nothing was checked, which is not the same as nothing being wrong.")
    c.done()

print("  parsed with %s\n" % how)
unconfirmed = 0
for start, src in blocks:
    try:
        parser(src)
        c.truthy("block at line %-5d parses (%d chars)" % (line_of(start), len(src)), True)
    except Exception as e:
        if modern_only(src, str(e)):
            # Parse a copy with the property escapes swapped for a plain class.
            # Everything ELSE in the block is then genuinely checked — a stray
            # bracket three thousand lines later still fails here, which is the
            # whole point of the suite. Only the escapes go unverified.
            try:
                parser(NEUTRALISE.sub("a-zA-Z0-9", src))
                unconfirmed += 1
            except Exception as e2:
                c("block at line %d parses (escapes neutralised)" % line_of(start),
                  str(e2), "")
                continue
            print("  NOTE  block at line %d uses unicode property escapes, which "
                  "esprima (2017) cannot parse.\n        NOT CONFIRMED here — "
                  "install node, or trust CI, which runs node.\n        It said: %s"
                  % (line_of(start), str(e)[:80]))
        else:
            c("block at line %d parses" % line_of(start), str(e), "")

# The HUD is not the only page with inline JavaScript any more. Arc Watch has
# its own, written the same way and with the same failure: a stray bracket does
# not degrade it, it stops the whole page rendering. It was outside this suite
# entirely, which meant the one page that exists to tell you what ARC costs had
# nothing checking it at all.
print("\nThe other pages that carry their own script:")
others = sorted(p for p in (ARC / "static").glob("*.html") if p.name != HUD.name)
checked = 0
for path in others:
    text = io.open(path, encoding="utf-8").read()
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S | re.I):
        src = m.group(1)
        if not src.strip():
            continue
        checked += 1
        where = "%s line %d" % (path.name, text.count("\n", 0, m.start()) + 1)
        try:
            parser(src)
            c.truthy("  %-28s parses (%d chars)" % (where, len(src)), True)
        except Exception as e:
            if modern_only(src, str(e)):
                try:
                    parser(NEUTRALISE.sub("a-zA-Z0-9", src))
                    unconfirmed += 1
                except Exception as e2:
                    c("  %s parses (escapes neutralised)" % where, str(e2), "")
            else:
                c("  %s parses" % where, str(e), "")
c.truthy("  and Arc Watch is one of them",
         any(p.name == "watch.html" for p in others))
if not checked:
    print("  (none of them has inline script)")

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

if unconfirmed:
    print("\n%d block(s) could not be confirmed by this parser. That is a gap, not a"
          " pass;\nCI runs node, which closes it." % unconfirmed)

c.done()
