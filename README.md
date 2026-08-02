# ARC — Ambient Response Core

A voice assistant you run yourself. Browser front end, FastAPI proxy, Claude
for reasoning, optional ElevenLabs for a voice that doesn't sound like a
browser.

---

## Run it locally

```
pip install -r requirements.txt
cp .env.example .env          # paste your Anthropic key in
python run.py
```

Opens itself at <http://localhost:8420>.

On Windows, `start-arc.vbs` does the same thing with no console window, and
`stop-arc.vbs` shuts it down. Both live next to `run.py`.

**Use Chrome or Edge.** Speech recognition is a Chromium API. Other browsers
still work through the text field, just without voice input.

---

## Talking to it

| Action | How |
|---|---|
| Push to talk | Hold the ring. Or tab to it and hold Space |
| Hands free | Tick **Always listening**, then say the wake word — `bella` by default |
| Follow-up question | Just keep talking. No wake word needed for ~16 seconds after a reply |
| Interrupt a reply | Tap the ring while it's speaking |
| Spin the ball (3D) | Drag across the ring. Flick and it coasts |
| Poke it | Touch or hover — nearby bars swell under your finger |
| Type instead | The box on the right |

### Reading the ring

The hint under the ring reflects the microphone's real state, not an
intention:

| It says | Meaning |
|---|---|
| `SAY "BELLA"` | Live, waiting for the wake word |
| `LISTENING…` | Live and collecting what you say |
| `MIC RECONNECTING…` | Genuinely down for a moment — wait a beat |
| `SAY "BELLA" FIRST` | Heard you, but you weren't addressing it |

---

## Room mode

For when several people are present. Browsers give no way to tell who is
speaking, so instead of guessing, this makes misfiring impossible:

- The wake word is required for **every** question — no free follow-ups
- It must come at the **start** of the sentence, so someone saying the name
  mid-conversation doesn't trigger it
- ARC stops assuming the same person is speaking between turns
- Nothing is written to memory, since it can't tell whose facts they are

Say the wake word and your question together: *"bella, what's the weather."*

---

## Timers

The one thing ARC can actually *do*. Ask for a timer and it sets one; it
chimes and announces itself when it finishes, and survives a page reload.

Deliberately implemented entirely in the browser. Nothing runs on your
machine — which is what keeps it safe to put on the internet. Launching
applications or running commands would mean handing a web-reachable server the
ability to execute things on your PC, and that is a back door the moment the
site is shared.

ARC can't cancel a timer; reload the page to clear them.

## Staying quiet

ARC can decide it wasn't being spoken to and say nothing at all. It's told to
use this for half-sentences, one side of someone else's conversation, thinking
aloud, and audio picked up from a television — but explicitly *not* merely
because a message is short or badly transcribed, since a garbled question is
still a question. When unsure it answers, on the grounds that a needless reply
is a smaller failure than ignoring someone.

The transcript still records that it heard you, so silence is never
indistinguishable from a fault.

## Memory

ARC keeps up to 120 durable facts about you — your name, how you like
answers pitched, projects that keep coming up — and feeds them back on every
request. Stored in your browser on your own machine, never anywhere else.

The `MEMORY` readout shows how many it's holding. **Forget** wipes them.

It's told explicitly never to store passwords, keys, health details, or
one-off questions.

---

## Handling messy speech

Three layers, because a rough transcript is not a written sentence:

1. **Cleanup before sending** — stutters, doubled words and leading filler are
   stripped. `"um what what is the the weather"` becomes `"what is the weather"`.
   Deliberately conservative: `"i had had enough"` survives intact.
2. **Alternatives passed through** — the recogniser always produces several
   candidates and normally discards all but the top one. ARC keeps four and
   hands the runners-up to Claude as a private hint. This is the single
   biggest win for accents.
3. **A noise gate** — claps, coughs and breathing arrive as filler words or
   lone fragments. Those are dropped without disturbing the sentence you were
   halfway through. Say the same thing twice and it goes through regardless.

---

## Appearance

Under the transcript: seven colour themes, four core shapes (HUD, rings, hex,
waveform), a flat/3D toggle, and a reactivity slider from 0.4× to 4×. All
persist locally.

**3D** replaces the flat shape with an actual sphere: 150 points scattered
evenly over its surface by Fibonacci spiral (latitude bands would bunch at the
poles), each growing outward along its own normal, wrapped in a wireframe of
three latitudes and three longitudes. It turns slowly, faster while speaking.

Drag to spin it and to tilt it up or down. A press that stays put is
push-to-talk exactly as before; a press that moves is treated as a drag and
cancels the recording, so spinning the ball never sends a stray clip. Touch or
hover pushes the nearby surface outward, easing back when you let go.

Depth is driven off z rather than perspective — at this scale perspective
barely separates front from back, so the far hemisphere goes thin and dim
instead. That is what makes it read as a solid ball rather than a scatter of
dots. The flat backdrop layers fade right back in 3D.

---

## Deploying it

See [DEPLOY.md](DEPLOY.md). The short version:

Set `ARC_PASSWORD` and every route goes behind a login — the page, the chat
endpoint, the voice endpoint, the health check. Sessions are signed cookies,
HttpOnly, no session store. Also set `ARC_SECRET` so logins survive a restart.

Rate limits default to 12 requests per person per minute, 120 per hour, and
600 per day across the whole deployment. Tune with `ARC_RATE_PER_MIN`,
`ARC_RATE_PER_HOUR`, `ARC_DAILY_CAP`.

**HTTPS is mandatory in production.** Browsers refuse microphone access on
insecure origins. Localhost is the only exception, which is why development
works without it.

With no `ARC_PASSWORD` set, it stays open — correct for localhost, dangerous
anywhere else. The startup banner tells you which mode you're in.

---

## Why the proxy exists

The Anthropic API deliberately doesn't send CORS headers to browser origins,
so a page can't call it directly. The proxy also keeps your key off the
client, which matters the moment this stops being localhost.

---

## Where to take it next

- **Offline wake word** — Picovoice Porcupine replaces the always-on browser
  recogniser, trains on a custom phrase, and runs on a Pi without a tab open.
- **Better ears** — faster-whisper (`small.en`, int8) beats the Web Speech API
  on accents and noise, and runs locally.
- **Speaker identification** — genuinely impossible in-browser. Needs
  diarization server-side: AssemblyAI or Deepgram for speaker labels, Azure
  Speaker Recognition for actual names against enrolled voiceprints.
- **Hands** — give the proxy tools. Home Assistant's REST API is the shortest
  path from "it talks" to "it does things".
- **Streaming** — switch `/api/chat` to SSE and start speaking the first
  sentence before the rest arrives. Biggest perceived-latency win available.
