#!/usr/bin/env python3
"""
Every language Edge can speak, taken from Edge rather than from memory.

ARC used to offer eight voices, all English, hard-coded. Anyone whose first
language is not English got an assistant that could not say their name, let
alone answer them in it.

The list here is fetched from Microsoft at runtime — 300-odd neural voices
across 140-odd locales — and cached to disk. That is deliberate and it is the
whole design: a hand-written table of voice names would be a list of my
guesses, and a wrong guess is not a typo, it is text-to-speech that fails at
the moment somebody speaks. Fetching it means every name in the catalogue is a
name that exists.

The language LABELS come from the same place: Microsoft's own FriendlyName ends
with "- Tamil (India)", so ARC can name a language without anyone inventing a
translation table for 75 of them.

Offline, or if Microsoft changes the endpoint, it falls back to the original
eight English voices — which is the behaviour ARC had before, so the worst case
is where it started rather than a crash.
"""

import asyncio
import json
import os
import time
import threading
from pathlib import Path

import edge_tts

ROOT = Path(__file__).parent.resolve()
DATA_DIR = Path(os.getenv("ARC_DATA_DIR") or ROOT).resolve()
CACHE = DATA_DIR / "voices.json"

# A week. New neural voices appear a few times a year; refetching on every
# restart would add a network round trip to startup for nothing.
CACHE_TTL = 7 * 24 * 3600

# What ARC had before any of this. Also the answer when the network is not
# there: eight English voices is worse than 322, and far better than none.
FALLBACK = [
    {"short": "en-GB-SoniaNeural", "locale": "en-GB", "gender": "Female",
     "name": "Sonia", "label": "English (United Kingdom)"},
    {"short": "en-GB-LibbyNeural", "locale": "en-GB", "gender": "Female",
     "name": "Libby", "label": "English (United Kingdom)"},
    {"short": "en-GB-RyanNeural", "locale": "en-GB", "gender": "Male",
     "name": "Ryan", "label": "English (United Kingdom)"},
    {"short": "en-US-AriaNeural", "locale": "en-US", "gender": "Female",
     "name": "Aria", "label": "English (United States)"},
    {"short": "en-US-JennyNeural", "locale": "en-US", "gender": "Female",
     "name": "Jenny", "label": "English (United States)"},
    {"short": "en-US-GuyNeural", "locale": "en-US", "gender": "Male",
     "name": "Guy", "label": "English (United States)"},
    {"short": "en-AU-NatashaNeural", "locale": "en-AU", "gender": "Female",
     "name": "Natasha", "label": "English (Australia)"},
    {"short": "en-IE-EmilyNeural", "locale": "en-IE", "gender": "Female",
     "name": "Emily", "label": "English (Ireland)"},
]

_lock = threading.RLock()
_voices = None          # [{short, locale, gender, name, label}, ...]
_fetched_at = 0.0


def _clean(v):
    """One voice, in the shape the rest of ARC wants."""
    short = (v.get("ShortName") or "").strip()
    locale = (v.get("Locale") or "").strip()
    if not short or not locale:
        return None
    friendly = v.get("FriendlyName") or ""
    # "Microsoft Pallavi Online (Natural) - Tamil (India)"
    label = friendly.split(" - ")[-1].strip() if " - " in friendly else locale
    name = short.split("-")[-1].replace("Neural", "")
    return {"short": short, "locale": locale, "gender": v.get("Gender") or "",
            "name": name, "label": label}


async def _fetch():
    raw = await edge_tts.list_voices()
    out = [c for c in (_clean(v) for v in raw) if c]
    return sorted(out, key=lambda v: (v["locale"], v["short"]))


def _read_cache():
    try:
        data = json.loads(CACHE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("voices"):
            return data["voices"], float(data.get("at", 0))
    except Exception:
        pass
    return None, 0.0


def _write_cache(voices):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"at": time.time(), "voices": voices}),
                       encoding="utf-8")
        os.replace(tmp, CACHE)
    except Exception:
        pass


def catalogue(refresh: bool = False):
    """Every voice, cached. Never raises — an empty network gives the fallback."""
    global _voices, _fetched_at
    with _lock:
        if _voices and not refresh and time.time() - _fetched_at < CACHE_TTL:
            return _voices
        if not refresh:
            cached, at = _read_cache()
            if cached and time.time() - at < CACHE_TTL:
                _voices, _fetched_at = cached, at
                return _voices
        try:
            # A thread of our own: this is called from sync code, and there may
            # already be a running loop (the server's) that we must not touch.
            box = {}

            def run():
                try:
                    box["v"] = asyncio.run(_fetch())
                except Exception as e:
                    box["e"] = e

            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(timeout=20)
            fresh = box.get("v")
        except Exception:
            fresh = None
        if fresh:
            _voices, _fetched_at = fresh, time.time()
            _write_cache(fresh)
        elif not _voices:
            cached, at = _read_cache()          # stale is better than nothing
            _voices, _fetched_at = (cached or FALLBACK), at or time.time()
        return _voices


def loaded():
    """What is in hand right now, without fetching anything. None if nothing.

    catalogue() will go to the network, and on a dead one it blocks for up to
    twenty seconds before giving up. That is fine when somebody is waiting for
    speech and useless for a health check, which has to be able to say "the
    voice list looks wrong" without becoming the reason the page is hanging.
    """
    with _lock:
        return list(_voices) if _voices else None


def is_valid(short: str) -> bool:
    """Whether this is a real voice. The whitelist, but earned rather than
    typed — a client still cannot pass anything Microsoft does not publish."""
    if not short:
        return False
    return any(v["short"] == short for v in catalogue())


# Which country a bare language should mean. Only for languages spoken in
# several places, where picking alphabetically gets it wrong in a way a native
# speaker would notice at once — "French" landing on Belgian French, "German"
# on Austrian. Most languages have one locale and never reach this table.
PRIMARY = {
    "en": "en-GB",     # ARC is a British butler; the accent is part of it
    "ar": "ar-SA", "zh": "zh-CN", "pt": "pt-BR", "ta": "ta-IN",
    "bn": "bn-BD", "ur": "ur-PK", "ms": "ms-MY", "sw": "sw-KE",
}


# Microsoft ships child voices in several locales and does not flag them as
# anything but "Female". Picked automatically they are alarming — an assistant
# that answers you in a small child's voice is not a quirk, it is unusable —
# and alphabetical order put one of them second in line for English.
#
# A hand-kept list, and named as one. It is short because it only has to cover
# what could be chosen AUTOMATICALLY; anyone who actually wants one of these
# can still select it by name in the voice picker.
CHILD = {"en-GB-MaisieNeural", "en-US-AnaNeural"}

# Where the choice would otherwise be arbitrary, name it. Alphabetical order
# says nothing about how a voice sounds — the same reason PRIMARY exists for
# picking a country. en-GB is ARC's own voice and the reason this table does.
PREFERRED = {
    "en-GB": "en-GB-SoniaNeural",
    "en-US": "en-US-AriaNeural",
}


def _score(v, want_gender):
    # Female first only because ARC's own voice has always been Sonia, so an
    # unspecified switch to another language keeps the same character. A named
    # preference beats both, and a child voice is never chosen for you.
    return (0 if v["short"] == PREFERRED.get(v["locale"]) else 1,
            1 if v["short"] in CHILD else 0,
            0 if (v["gender"] or "").lower() == want_gender else 1,
            v["short"])


def same_language(lang: str, voice: str) -> bool:
    """Do a language tag and a voice name share a base language?

    So a caller can ask "can my configured voice speak this?" without pulling
    the whole catalogue apart. en-GB-SoniaNeural speaks "en", "en-US" and
    "en-GB"; it does not speak "ta".
    """
    a = (lang or "").strip().replace("_", "-").split("-")[0].lower()
    b = (voice or "").strip().split("-")[0].lower()
    return bool(a) and a == b


def for_lang(lang: str, gender: str = "female") -> str:
    """The voice to use for a language tag. "ta", "ta-IN" and "ta_LK" all work.

    Exact locale first, then any locale of the same language, then nothing —
    the caller falls back to its own default rather than being handed a voice
    that speaks a different language, which is worse than English.
    """
    tag = (lang or "").strip().replace("_", "-")
    if not tag:
        return ""
    voices = catalogue()
    exact = [v for v in voices if v["locale"].lower() == tag.lower()]
    if exact:
        return sorted(exact, key=lambda v: _score(v, gender))[0]["short"]
    base = tag.split("-")[0].lower()
    same = [v for v in voices if v["locale"].split("-")[0].lower() == base]
    if not same:
        return ""
    # A bare language: pick the country a speaker would expect rather than the
    # first alphabetically, which is how "French" became Belgian.
    want = PRIMARY.get(base, base + "-" + base.upper())
    preferred = [v for v in same if v["locale"].lower() == want.lower()]
    pool = preferred or same
    return sorted(pool, key=lambda v: _score(v, gender))[0]["short"]


def languages():
    """One entry per locale, for the picker: what ARC can hear and speak.

    Keyed by locale rather than language, because "Portuguese" is a choice
    between Brazil and Portugal that changes both the accent and the words, and
    because the speech recogniser wants a locale anyway.
    """
    out = {}
    for v in catalogue():
        loc = v["locale"]
        if loc not in out:
            out[loc] = {"locale": loc, "label": v["label"], "voices": 0}
        out[loc]["voices"] += 1
    return sorted(out.values(), key=lambda x: x["label"])


def voices_for(locale: str):
    """The voices available in one locale, for the voice picker."""
    loc = (locale or "").strip().lower()
    return [v for v in catalogue() if v["locale"].lower() == loc]
