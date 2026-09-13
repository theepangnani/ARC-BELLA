# -*- coding: utf-8 -*-
"""Characters: a voice, a way of speaking, and a name people would use.

The owner asked for "a British male, for the Jarvis concept". Two British men
were already in the catalogue the whole time. The picker never showed them,
because it listed only the exact country the laptop reported, and on an en-CA
machine that meant Clara and Liam. So there are two fixes here, and this suite
holds both:

  · EVERY COUNTRY'S VOICES for the language are offered, grouped by country.
  · CHARACTERS: a voice plus a rate and a pitch, named for how they sound.
    The rate and pitch live on the server. The page names a character and
    never sends prosody, so nothing a browser sends reaches edge-tts as a
    speaking rate.

And Bella can switch when asked, by [[voice: butler]], but only to something the
picker is already offering.
"""
import asyncio
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

import edge_tts   # noqa: E402
import voices     # noqa: E402
import run        # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
PROMPT = io.open(ARC / "prompts" / "main.md", encoding="utf-8").read()
c = Check()

_ROOTS = [os.environ.get(v, "") for v in ("ProgramFiles", "ProgramFiles(x86)",
                                          "LOCALAPPDATA")]
BROWSERS = [os.path.join(r, *parts) for r in _ROOTS if r for parts in (
    ("Google", "Chrome", "Application", "chrome.exe"),
    ("Microsoft", "Edge", "Application", "msedge.exe"))]

print("Every character is well formed:")
ids = [p["id"] for p in voices.PERSONAS]
c("  no id is used twice", len(ids), len(set(ids)))
for p in voices.PERSONAS:
    ok = (re.fullmatch(r"[+-]\d{1,2}%", p["rate"]) and re.fullmatch(r"[+-]\d{1,2}Hz", p["pitch"])
          and p["gender"] in ("Male", "Female") and " — " in p["label"])
    c.truthy("  %-8s %s %s %s" % (p["id"], p["voice"], p["rate"], p["pitch"]), ok)
c.truthy("  there is a British man, which is what was asked for",
         any(p["voice"].startswith("en-GB") and p["gender"] == "Male" for p in voices.PERSONAS))
c("  Bella's own voice is the first character, and is the default voice",
  voices.PERSONAS[0]["voice"], run.TTS_VOICE)
c.truthy("  every alias points at a real character",
         all(voices.persona(a) for a in voices.ALIASES))

print("\nEvery character's voice is one Microsoft actually publishes:")
cat = {v["short"] for v in voices.catalogue()}
if len(cat) > len(voices.FALLBACK):
    for p in voices.PERSONAS:
        c.truthy("  %s" % p["voice"], p["voice"] in cat)
else:
    print("  (offline: only the fallback list is here, so this is checked where the network is)")
c.truthy("  and one missing from the catalogue is not offered",
         all(voices.is_valid(p["voice"]) for p in voices.personas_for("en-CA")))

print("\nA name resolves the way a person would say it:")
c("  jarvis", voices.persona("jarvis")["id"], "butler")
c("  British man", voices.persona("British man")["id"], "butler")
c("  the id as the page sends it", voices.persona("persona:thomas")["id"], "thomas")
c("  default goes home", voices.persona("default")["id"], "bella")
c("  a made-up name is nothing", voices.persona("hal9000"), None)
c("  and so is an attempt to smuggle a rate", voices.persona("persona:butler;rate=+90%"), None)

print("\nThe picker is offered every country, not just this one:")
others = voices.same_language_voices("en-CA")
c.truthy("  a Canadian laptop is now offered the British voices",
         {"en-GB-RyanNeural", "en-GB-SoniaNeural"} <= {v["short"] for v in others} or len(cat) <= 8)
c("  ...but not its own country twice", any(v["locale"] == "en-CA" for v in others), False)
c("  and no other language", {v["locale"].split("-")[0] for v in others} - {"en"}, set())
c("  Tamil is offered no English characters", voices.personas_for("ta-IN"), [])

print("\nThe server speaks with the character's own rate and pitch:")
said = []


class FakeCommunicate:
    def __init__(self, text, voice, rate="+0%", pitch="+0Hz", **kw):
        said.append((voice, rate, pitch))

    async def stream(self):
        yield {"type": "audio", "data": b"x"}


real = edge_tts.Communicate
edge_tts.Communicate = FakeCommunicate
try:
    asyncio.run(run._edge_tts("Good evening.", "persona:butler", "en-CA"))
    butler = voices.persona("butler")
    c("  the butler", said[-1], (butler["voice"], butler["rate"], butler["pitch"]))
    asyncio.run(run._edge_tts("Good evening.", "en-GB-ThomasNeural", "en-CA"))
    c("  a plain voice speaks plainly", said[-1], ("en-GB-ThomasNeural", "+0%", "+0Hz"))
    asyncio.run(run._edge_tts("Good evening.", "persona:nobody", "en-GB"))
    c("  an unknown character is the configured voice, not an error",
      said[-1], (run.TTS_VOICE, "+0%", "+0Hz"))
    asyncio.run(run._edge_tts("Good evening.", "persona:butler;rate=+90%", "en-GB"))
    c("  nothing sent from the page becomes a rate", said[-1][1:], ("+0%", "+0Hz"))
finally:
    edge_tts.Communicate = real
tts_route = io.open(ARC / "run.py", encoding="utf-8").read()
tts_route = tts_route[tts_route.index('@app.post("/api/tts")'):tts_route.index('@app.get("/api/voices")')]
c("  and /api/tts reads no rate or pitch from the request",
  bool(re.search(r"payload\.get\(\"(rate|pitch)\"", tts_route)), False)

print("\nBella is told, and the page listens:")
c.truthy("  the prompt names the directive", "[[voice: butler]]" in PROMPT)
for p in voices.PERSONAS:
    c.truthy("  the prompt knows %s" % p["id"], p["id"] + " (" in PROMPT)
c.truthy("  the page lifts [[voice:]] out of the reply", "chooseVoice(name)" in page)
c.truthy("  before the reply is spoken", "already in the new voice" in page)
c.truthy("  the browser fallback follows the chosen voice's sex", "chosenVoiceIsMale()" in page)


def run_picker(payload):
    """The shipped picker and voice switch, in a real browser.

    Stubbed at the fetch and at loadVoices (the browser's own voice list, which
    a headless run does not have). Everything else is cut straight out of the
    page, so this is the code people run, not a copy of it.
    """
    import shutil
    import subprocess
    import tempfile
    exe = next((b for b in BROWSERS if b and os.path.isfile(b)), None)
    if not exe:
        return None
    start = page.index("async function fillVoices(")
    end = page.index("return true;", page.index("function chooseVoice(")) + len("return true;\n  }")
    mod = page[start:end]
    probe = """
(async () => {
  const log = [];
  await fillVoices("en-CA");
  const groups = Array.from(neuralVoiceSel.querySelectorAll("optgroup")).map(g => g.label);
  log.push(groups.join(","));
  log.push(String(chooseVoice("jarvis")), neuralVoiceSel.value, String(chosenVoiceIsMale()));
  log.push(String(chooseVoice("hal9000")), neuralVoiceSel.value);
  log.push(String(chooseVoice("Liam")), neuralVoiceSel.value);
  log.push(String(chooseVoice("bella")), String(chosenVoiceIsMale()));
  chooseVoice("thomas");
  await fillVoices("en-CA");
  log.push(neuralVoiceSel.value, localStorage.getItem("arc.voice") || "");
  document.getElementById("probe").textContent = log.join("|");
})();
"""
    html = ('<!doctype html><meta charset="utf-8"><select id="neuralVoice"></select>'
            '<pre id="probe"></pre><script>'
            'let selectedVoice = ""; const neuralVoiceSel = document.getElementById("neuralVoice");'
            'let loads = 0; function loadVoices() { loads++; }'
            'window.fetch = async () => ({ ok: true, json: async () => (' + json.dumps(payload) + ') });'
            + mod + probe + '</script>')
    work = tempfile.mkdtemp(prefix="arcvoice")
    try:
        p = os.path.join(work, "voice.html")
        io.open(p, "w", encoding="utf-8").write(html)
        out = subprocess.run(
            [exe, "--headless=new", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--user-data-dir=" + os.path.join(work, "p"),
             "--virtual-time-budget=6000", "--dump-dom", "file:///" + p.replace("\\", "/")],
            capture_output=True, timeout=120, encoding="utf-8", errors="replace").stdout
        m = re.search(r'<pre id="probe">(.*?)</pre>', out, re.S)
        return m.group(1).split("|") if m and m.group(1).strip() else None
    finally:
        shutil.rmtree(work, ignore_errors=True)


print("\nIn a real browser, the picker and the switch:")
payload = {
    "voices": [{"short": "en-CA-LiamNeural", "locale": "en-CA", "gender": "Male",
                "name": "Liam", "label": "English (Canada)"}],
    "others": [{"short": "en-GB-RyanNeural", "locale": "en-GB", "gender": "Male",
                "name": "Ryan", "label": "English (United Kingdom)"}],
    "personas": [{"id": p["id"], "label": p["label"], "gender": p["gender"]}
                 for p in voices.PERSONAS],
}
got = run_picker(payload)
if got is None:
    print("  (no Chrome or Edge here — the browser half is checked where there is one)")
else:
    c("  characters first, then this country, then the others",
      got[0], "Characters,English (Canada),English (United Kingdom)")
    c("  'jarvis' picks the butler", got[1:4], ["true", "persona:butler", "true"])
    c("  a made-up name changes nothing", got[4:6], ["false", "persona:butler"])
    c("  a first name picks that voice", got[6:8], ["true", "en-CA-LiamNeural"])
    c("  back to Bella, and the fallback goes back to a woman", got[8:10], ["true", "false"])
    c("  a choice survives the list being rebuilt, and is remembered",
      got[10:12], ["persona:thomas", "persona:thomas"])

c.done()
