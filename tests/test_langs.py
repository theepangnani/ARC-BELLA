# -*- coding: utf-8 -*-
"""Every language, not just English.

ARC listened in en-US and answered with one of eight English voices. For anyone
whose first language is not English that is not a limitation, it is a wall: the
recogniser hands back English-shaped nonsense for every other language and
never reports an error, so the only symptom is an assistant that does not
understand you.

Three things have to agree — what the recogniser listens FOR, what voice
answers, and what language the model writes in — and the checks below are
mostly about them agreeing.

The voice catalogue is fetched from Microsoft, so this suite needs a network.
Without one it falls back to the eight English voices, which is what ARC had
before, and says so rather than failing.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check   # noqa: E402
sandbox()

import voices   # noqa: E402

page = io.open(HUD, encoding="utf-8").read()
body = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", page, re.S)[0]
c = Check()

cat = voices.catalogue()
offline = cat is voices.FALLBACK or len(cat) <= len(voices.FALLBACK)
print("catalogue: %d voices, %d locales%s"
      % (len(cat), len(voices.languages()), "  (OFFLINE — fallback list)" if offline else ""))

print("\nThe catalogue is fetched, not typed from memory:")
src = io.open(ARC / "voices.py", encoding="utf-8").read()
c.truthy("  it asks edge-tts what exists", "edge_tts.list_voices()" in src)
c.truthy("  and says why that matters", "list of my" in src and "guesses" in src)
c.truthy("  language names come from Microsoft too, not a table anyone wrote",
         "FriendlyName" in src)
c.truthy("  it caches, so startup is not a network round trip", "CACHE_TTL" in src)
c.truthy("  and degrades to the eight English voices rather than crashing",
         "FALLBACK" in src)

if not offline:
    print("\nA language resolves to a real voice in that language:")
    for tag, want_locale in [("ta", "ta-IN"), ("ta-LK", "ta-LK"), ("si", "si-LK"),
                             ("hi", "hi-IN"), ("ar", "ar-SA"), ("zh", "zh-CN"),
                             ("ja", "ja-JP"), ("ko", "ko-KR"), ("he", "he-IL"),
                             ("th", "th-TH"), ("el", "el-GR"), ("ru", "ru-RU"),
                             ("sw", "sw-KE"), ("ur", "ur-PK"), ("vi", "vi-VN")]:
        got = voices.for_lang(tag)
        c("  %-6s -> %s" % (tag, want_locale), got.startswith(want_locale + "-"), True)
        c.truthy("       and it is a voice that exists", voices.is_valid(got))

    # Picking alphabetically is how "French" became Belgian and "German"
    # Austrian — right language, wrong country, and a native speaker hears it
    # in the first syllable.
    print("\nA bare language picks the country a speaker would expect:")
    for tag, want in [("fr", "fr-FR"), ("de", "de-DE"), ("es", "es-ES"),
                      ("pt", "pt-BR"), ("en", "en-GB"), ("nl", "nl-NL"),
                      ("it", "it-IT")]:
        c("  %-4s -> %s" % (tag, want), voices.for_lang(tag).startswith(want + "-"), True)

    print("\nIt refuses rather than answering in the wrong language:")
    c("  a language with no voice returns nothing", voices.for_lang("xx"), "")
    c("  ...so the caller keeps its own default", voices.for_lang(""), "")
    c("  an invented voice is not accepted", voices.is_valid("xx-XX-NopeNeural"), False)
    c.truthy("  a real one is", voices.is_valid("en-GB-SoniaNeural"))

    print("\nEnough of the world to mean it:")
    langs = {v["locale"].split("-")[0] for v in cat}
    c.truthy("  more than 50 languages (%d)" % len(langs), len(langs) > 50)
    for must in ["ta", "si", "hi", "bn", "ur", "ar", "zh", "ja", "ko", "sw", "am", "my"]:
        c.truthy("  %s is there" % must, must in langs)

print("\nThe server picks the voice from the language:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  /api/tts takes a lang", 'lang = (payload.get("lang")' in run_src)
c.truthy("  and uses it when no voice is named", "v = voices.for_lang(lang)" in run_src)
c.truthy("  the whitelist is now every real voice", "voices.is_valid(voice)" in run_src)
c("  the old eight-name list is gone", "EDGE_VOICES" in run_src, False)
c.truthy("  /api/voices serves the list", '@app.get("/api/voices")' in run_src)
# One ElevenLabs voice is one accent, whatever the model claims.
c.truthy("  ElevenLabs stands aside for a language its voice cannot speak",
         "base != ELEVEN_LANG" in run_src)
c.truthy("  ...and that is explained", "an English mouth" in run_src)

print("\nThe page listens in it, speaks in it, and writes in it:")
c.truthy("  the recogniser follows the setting", "r.lang = speechLang();" in body)
c("  en-US is no longer hard-coded", 'r.lang = "en-US"' in body, False)
c.truthy("  changing language rebuilds the recogniser",
         "rebuildRecognition();" in body and "arc.lang" in body)
c.truthy("  ...and why it must", "keeps listening in the old language" in body)
c.truthy("  the voice request carries the language", "lang: speechLang()" in body)
c.truthy("  the model is told which language", "function langBlock()" in body)
c.truthy("  ...by name, not by tag", "langName(tag)" in body)
c.truthy("  and is told to match a mid-conversation switch",
         "match whatever they just used" in body)
c.truthy("  English needs no such block", 'if (base === "en") return "";' in body)
c.truthy("  the prompt says it too", "ANSWER IN THE LANGUAGE YOU ARE SPOKEN TO IN" in page)
c.truthy("  including how numbers are read in that language",
         "READ ALOUD in THAT language" in page)

print("\nThe picker offers only what will work:")
c.truthy("  languages come from the server", 'fetch("/api/voices")' in body)
c("  no hard-coded language list in the page", 'value="fr-FR"' in page, False)
c.truthy("  voices are refetched per language", "async function fillVoices(" in body)
c.truthy("  a voice from another language is dropped, not kept",
         "an English voice reading Tamil" in body)
c.truthy("  auto means the device's own language", 'langPref !== "auto"' in body)

print("\nEcho detection survives leaving the Latin alphabet:")
# The old rule deleted every non-Latin character, so in Tamil or Arabic every
# word normalised to nothing and ARC cheerfully answered its own voice.
c("  no Latin-only filter is left anywhere in the speech path",
  len(re.findall(r"\[\^a-z0-9", body)), 0)
c.truthy("  replaced by unicode property escapes (%d places)"
         % len(re.findall(r"\\p\{L\}\\p\{N\}", body)),
         len(re.findall(r"\\p\{L\}\\p\{N\}", body)) >= 6)
c.truthy("  with the u flag they need", "/gu" in body)
# The worst of the seven: an empty string reads as a cough, so every Tamil,
# Arabic and Chinese utterance was dropped before the model ever saw it.
c.truthy("  including the noise gate, which silently ate every word",
         "string is treated as a cough" in body)
c("  and BOTH speech paths, not just the one that was easy to find",
  len(re.findall(r"lastSpokenText = \(text", body)), 2)
c.truthy("  and the reason recorded", "answered its own voice coming back" in body)

print("\nIt renders, and it reads the right way round:")
c.truthy("  fonts fall back to what the OS ships for other scripts",
         "--fallback:" in page and "Nirmala UI" in page)
c.truthy("  ...for the display face", 'var(--fallback)' in page)
c.truthy("  and the reason is on record", "rows of empty boxes" in page)
c.truthy("  transcript lines pick their own direction", 'setAttribute("dir", "auto")' in body)
c.truthy("  so do the live caption and the text box",
         'id="live" dir="auto"' in page and 'id="typed"' in page)
c.truthy("  the document declares its language", "document.documentElement.lang" in body)

print("\nStopping ARC does not require English:")
c.truthy("  the user can add their own stop words", "arc.stopwords" in body)
c.truthy("  matched before the English filler-stripping", "BEFORE the tidying" in body)
c.truthy("  there is a field for it", 'id="stopWords"' in page)
# Guessing at "stop" in seventy languages would be wrong somewhere, and wrong
# here means the interruption does not land.
c.truthy("  and no invented translations", "a list of my guesses at" in body)
c.truthy("  tapping still works in any language", "Tapping the ring always works" in body)

c.done()
