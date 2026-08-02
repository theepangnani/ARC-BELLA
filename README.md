# ARC — Ambient Response Core

A voice assistant that runs on your machine. Browser front end, FastAPI proxy,
Claude for reasoning, optional ElevenLabs for a voice that doesn't sound like a
browser.

## Run it

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # paste your Anthropic key in
    python run.py

It opens itself at http://localhost:8420.

Use **Chrome or Edge** — speech recognition is a Chromium API. Other browsers
still work through the text field.

## Why the proxy exists

The Anthropic API deliberately doesn't send CORS headers to browser origins, so
a page can't call it directly. The proxy also keeps your key off the client,
which matters the moment this stops being localhost.

## Controls

| Action | How |
|---|---|
| Push to talk | Hold the ring, or hold Space |
| Always listening | Toggle it on, then say your wake word + a request |
| Interrupt a reply | Tap the ring while it's speaking |
| Change the persona | Edit `SYSTEM_PROMPT` in `static/index.html` |

## Where to take it next

- **Offline wake word** — Picovoice Porcupine replaces the always-on browser
  recogniser, trains on a custom phrase, and runs on a Pi without a tab open.
- **Better ears** — faster-whisper (`small.en`, int8) beats the Web Speech API
  on accents and noise, and runs locally.
- **Hands** — give the proxy tools. Home Assistant's REST API is the shortest
  path from "it talks" to "it does things".
- **Streaming** — switch `/api/chat` to SSE and start speaking the first
  sentence before the rest arrives. Biggest perceived-latency win available.
