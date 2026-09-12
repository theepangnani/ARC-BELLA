# -*- coding: utf-8 -*-
"""What gets heard, what gets consent, and how long the voice may go quiet.

THE GATE. isNoise() exists to drop coughs and doors, and it dropped answers.
"no" is two letters and "yes", "yep", "sure", "okay" are lone words of four or
fewer, and the gate called both of those shapes a cough. So Bella asked "shall
I send it?", heard "yes", and threw it away before the model saw it. A SECOND
"yes" got through — the gate lets a repeat past — which is why it read as
intermittent rather than as broken.

WORSE, AND FOUND ON THE WAY: the normaliser kept letters and numbers and turned
everything else into a space, and in Tamil and Hindi most vowels and the virama
are COMBINING MARKS, which are neither. "வானிலை என்ன" (what's the weather)
reached the gate as five fragments of one or two characters, and "every word is
two letters or fewer" called that noise. Ask Bella the weather in Tamil and she
heard nothing at all.

test_langs.py was meant to guard exactly this and passed straight through it,
because it checked that the right regex was WRITTEN, not what it did to a Tamil
word. So this suite runs the real functions, cut out of index.html, in a real
JavaScript engine — node if present, else headless Chrome or Edge — on real
words. If none of them is available it says so rather than reporting a pass
nobody earned, the same stance as test_js_syntax.py.

THE VOICE. A sentence renders in one to three seconds, measured. The renderer
allowed twenty seconds per attempt and three attempts — a minute of silence
mid-reply for one stalled sentence — and the stuck-watchdog had been widened to
seventy seconds to accommodate it. And one failed first sentence switched the
engine to the browser voice for good, because nothing ever switched it back.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

c = Check()
page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]


def between(start, end):
    i = body.index(start)
    return body[i:body.index(end, i)]


WORDS = between("  const YES_WORDS = new Set([", "  /* ---------------- persistent memory")
GATE = between("  const FILLERS = new Set([", "  // If you say roughly the same thing again")

# (kind, text, confidence, expected). Every one of these is something a person
# says to an assistant — the point is real input, not boundary arithmetic.
CASES = [
    # --- the reported bug: one-word answers ----------------------------------
    ("noise", "yes", None, False), ("noise", "no", None, False),
    ("noise", "yep", None, False), ("noise", "nope", None, False),
    ("noise", "sure", None, False), ("noise", "okay", None, False),
    ("noise", "ok", None, False), ("noise", "yeah okay", None, False),
    ("noise", "uh yes", None, False), ("noise", "yes please", None, False),
    # "which of the three?" -- Chrome writes the answer as a digit
    ("noise", "2", None, False), ("noise", "two", None, False),
    # Chrome reports low confidence for a clipped "yes" as readily as a cough
    ("noise", "yes", 0.2, False),
    # --- other languages, where a yes is just as short -----------------------
    ("noise", "ஆம்", None, False), ("noise", "இல்லை", None, False),
    ("noise", "oui", None, False), ("noise", "да", None, False),
    ("noise", "हाँ", None, False), ("noise", "नहीं", None, False),
    ("noise", "是", None, False), ("noise", "はい", None, False),
    # --- whole questions in scripts with combining marks: the bigger bug -----
    ("noise", "வானிலை என்ன", None, False),          # what's the weather
    ("noise", "நிறுத்து", None, False),             # stop
    ("noise", "मौसम कैसा है", None, False),          # how's the weather
    # --- what SHOULD still be dropped: the gate still has a job --------------
    ("noise", "uh", None, True), ("noise", "hmm", None, True),
    ("noise", "um uh", None, True), ("noise", "a", None, True),
    ("noise", "the", None, True), ("noise", "", None, True),
    ("noise", "...", None, True), ("noise", "uh", 0.9, True),
    # --- and ordinary English is untouched -----------------------------------
    ("noise", "what's the weather", None, False),
    ("noise", "what time is it", 0.9, False),
    # --- isAnswer, used by the one-digit floor and the wake-word hint --------
    ("answer", "yes", None, True), ("answer", "uh no", None, True),
    ("answer", "2", None, True), ("answer", "what is it", None, False),
    ("answer", "uh", None, False),
    # --- CONSENT: the yes half only ------------------------------------------
    ("consent", "yes", None, True), ("consent", "yep", None, True),
    ("consent", "sure", None, True), ("consent", "ok", None, True),
    ("consent", "go ahead", None, True), ("consent", "ஆம்", None, True),
    ("consent", "சரி", None, True), ("consent", "हाँ", None, True),
    ("consent", "oui", None, True), ("consent", "はい", None, True),
    # A list where a "no" could count as permission is not a list to have.
    ("consent", "no", None, False), ("consent", "nope", None, False),
    ("consent", "இல்லை", None, False), ("consent", "नहीं", None, False),
    ("consent", "non", None, False), ("consent", "いいえ", None, False),
    # An answer is not necessarily a yes.
    ("consent", "right", None, False), ("consent", "maybe", None, False),
    ("consent", "2", None, False),
]

CALL = """
const __cases = %s;
const __out = __cases.map(([k, t, conf]) =>
  k === "noise" ? isNoise(t, conf === null ? undefined : conf)
  : k === "answer" ? isAnswer(t) : isAffirmation(t));
""" % json.dumps([[k, t, conf] for k, t, conf, _ in CASES], ensure_ascii=False)


def run_node(js):
    node = shutil.which("node")
    if not node:
        return None
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(js + "\nconsole.log('RESULT:' + JSON.stringify(__out));\n")
    try:
        out = subprocess.run([node, f.name], capture_output=True, timeout=60,
                             encoding="utf-8").stdout
        return out, "node"
    finally:
        os.unlink(f.name)


# Built from the environment rather than written out: test_meta.py forbids a
# path off this machine, and rightly — Program Files is not always on C:, and a
# per-user Chrome lives under LOCALAPPDATA, which a hard-coded list never finds.
_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                          "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))] + [
    shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser",
                                    "microsoft-edge")]


def run_browser(js):
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    work = tempfile.mkdtemp(prefix="arcgate")
    try:
        html = os.path.join(work, "gate.html")
        io.open(html, "w", encoding="utf-8").write(
            '<!doctype html><meta charset="utf-8"><body><script>\ntry {\n' + js +
            '\ndocument.body.textContent = "RESULT:" + JSON.stringify(__out);\n'
            '} catch (e) { document.body.textContent = "ERROR:" + e; }\n'
            '</script></body>')
        # A throwaway profile: never the user's own, and never one a running
        # browser already holds.
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--user-data-dir=" + os.path.join(work, "p"),
             "--dump-dom", "file:///" + html.replace("\\", "/")],
            capture_output=True, timeout=90, encoding="utf-8", errors="replace").stdout
        return out, os.path.basename(exe)
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("The real gate, run in a real engine:")
got = run_node(WORDS + GATE + CALL) or run_browser(WORDS + GATE + CALL)
if not got:
    print("  NOTE  no node, Chrome or Edge here. NOT CONFIRMED — the behaviour")
    print("        below is a gap, not a pass. CI runs node, which closes it.")
else:
    out, engine = got
    m = re.search(r"(RESULT|ERROR):(.*?)(?:</body>|$)", out, re.S)
    c.truthy("  it ran (%s)" % engine, m and m.group(1) == "RESULT")
    if m and m.group(1) == "ERROR":
        print("        " + m.group(2)[:300])
    results = json.loads(m.group(2).strip()) if m and m.group(1) == "RESULT" else []
    section = None
    for (kind, text, conf, want), have in zip(CASES, results):
        if kind != section:
            section = kind
            print({"noise": "\n  is it noise?", "answer": "\n  is it an answer?",
                   "consent": "\n  does it count as consent?"}[kind])
        label = ("    %-16s" % (repr(text) if text else "''")) + \
                (" @%.1f" % conf if conf is not None else "")
        c(label, have, want)
    c("  every case produced a result", len(results), len(CASES))

print("\nThe gate, as written:")
c("  no normaliser in the speech path drops combining marks",
  len(re.findall(r"\[\^\\p\{L\}\\p\{N\}", body)), 0)
c.truthy("  they keep \\p{M} (%d places)" % len(re.findall(r"\\p\{L\}\\p\{M\}\\p\{N\}", body)),
         len(re.findall(r"\\p\{L\}\\p\{M\}\\p\{N\}", body)) >= 9)
c("  no answer word is also a filler, which was the contradiction",
  sorted(set(re.findall(r'"([a-z]+)"', GATE[:GATE.index("]);")])) &
         set(re.findall(r'"([^"]+)"', WORDS[:WORDS.index("const AFFIRMATIONS")]))), [])
c.truthy("  the yes and no halves are separate sets",
         "const YES_WORDS" in body and "const NO_WORDS" in body)
c("  consent reads only the yes half", "NO_WORDS.has" in
  body[body.index("function isAffirmation"):body.index("function isAffirmation") + 800], False)
c.truthy("  a one-digit answer passes the two-character floor",
         "(text.length < 2 && !isAnswer(text))" in body)
c.truthy("  and an unaddressed yes still gets the wake-word hint", "|| isAnswer(text))" in body)

print("\nThe voice renderer:")
fc = between("    async function fetchChunk(", "    function playUrl(")
c.truthy("  one budget for the whole sentence", "TTS_BUDGET_MS = 14000" in body)
c.truthy("  each attempt capped by what is left of it", "Math.min(TTS_ATTEMPT_MS, left)" in fc)
c("  the twenty-second attempts are gone", "20000" in fc, False)
c.truthy("  a refusal (4xx) is not asked again", "err.permanent" in fc and "if (e.permanent) throw e;" in fc)
c.truthy("  ...except the two that mean 'not yet'", "!== 408" in fc and "!== 429" in fc)
c.truthy("  saying stop abandons the download at once", "superseded = true; tctrl.abort();" in fc)
c.truthy("  and the timers are always cleared", "clearInterval(watch);" in fc and "finally" in fc)
wd = int(re.search(r'const limit = m === "thinking" \? \d+ : (\d+);', body).group(1))
c("  the stuck-watchdog follows the new budget", wd, 30000)
c.truthy("  ...with room for a full sentence budget", wd > 14000)

print("\nOne failed sentence no longer costs the voice for the session:")
c("  the engine is never switched to the browser voice after boot",
  len(re.findall(r'state\.engine = "browser"', body)), 0)
c.truthy("  it is a cooldown instead", "neuralDownUntil = Date.now() + NEURAL_COOLDOWN_MS" in body)
c.truthy("  of two minutes", "NEURAL_COOLDOWN_MS = 120000" in body)
c.truthy("  speak() honours it", "Date.now() >= neuralDownUntil" in body)
c.truthy("  this reply falls back without changing the engine", "speak(text, onDone, true);" in body)
c.truthy("  and the readout says when the proper voice is back",
         'el.rVoice.textContent = "NEURAL";       // back after a cooldown' in body)

print("\nA clip that will not PLAY is not the voice service failing:")
# It was treated as one: any error inside the playback loop reached the same
# catch as a failed render, so the reply restarted from the top in the browser
# voice — repeating what had been said — and the neural voice was benched for
# two minutes for a fault on the device, not the service.
sr = between("  async function speakRemote(", "  let speakGen = 0;")
c.truthy("  a playback failure is marked apart", "err.playback = true;" in sr)
c.truthy("  ...and carries on from the sentence that failed, not the top",
         'speak(chunks.slice(e.at).join(" ").trim() || text, onDone, true);' in sr)
c.truthy("  ...before, and so without, the two-minute bench",
         sr.index("if (e && e.playback)") < sr.index("neuralDownUntil = Date.now()"))
c.truthy("  the clip already fetched for the next sentence is released",
         "nextFetch.then(u => { if (u) URL.revokeObjectURL(u); });" in sr)
c.truthy("  a real service failure says why",
         "\"The voice service didn't answer (\" + why + \")" in sr)
run_src = io.open(os.path.join(str(ARC), "run.py"), encoding="utf-8").read()
tts_route = run_src[run_src.index('@app.post("/api/tts")'):run_src.index('@app.get("/api/voices")')]
c.truthy("  and the server writes the reason down", "voice render failed:" in tts_route)
c("  ...never the sentence, which may be anything",
  re.search(r"print\([^\n]*\{text", tts_route) is not None, False)

print("\nThe model is told:")
import prompt   # noqa: E402
rule = prompt.base("main")
c.truthy("  a one-word reply is an answer", "A ONE-WORD REPLY" in rule)
c.truthy("  and never to go silent on one", "Never go silent on a yes or a no" in rule)

c.done()
