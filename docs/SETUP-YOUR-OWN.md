# Running your own ARC

This is for someone setting ARC up on **their own Windows PC**, from scratch,
with none of the original owner's accounts or keys.

## Why you need your own copy

Signing in to somebody else's ARC gives you a window onto *their* machine. ARC's
screen features — live screen, watch mode, opening apps, the shell — all act on
the computer ARC is *running on*. Over the internet those are switched off, and
deliberately so: enabling them would show you the owner's desktop, not yours.

Run it on your own PC and every one of them points at your screen instead.

## What you need first

| | |
| --- | --- |
| **Windows 10 or 11** | ARC's computer control is Windows-only. |
| **Python 3.11 or newer** | [python.org/downloads](https://www.python.org/downloads/). **Tick "Add python.exe to PATH"** on the first screen of the installer — almost every setup problem traces back to this box. |
| **Chrome or Edge** | Speech recognition is a Chrome/Edge feature. Firefox will load the page but won't hear you. |
| **A microphone** | See the note below — this is the one people get caught by. |
| **An Anthropic API key** | [console.anthropic.com](https://console.anthropic.com) → API keys. It is a paid account; ARC is a client, not a subscription. |

### The microphone, if you're on a desktop tower

Laptops have a microphone built in. **Desktop towers almost never do**, and
neither do most monitors — a few have one built into a webcam, most don't. If
your setup is tower + monitor + keyboard + mouse, you very likely have no
microphone at all, and ARC is a voice assistant.

Anything works: a USB headset, a gaming headset with a USB adapter, a clip-on
USB mic, or a webcam that has a mic in it. Plug it in *before* you start, then
check Windows can hear it — **Settings → System → Sound → Input**, and watch the
bar move while you talk. If that bar doesn't move, ARC won't hear you either,
and no amount of fiddling with ARC will change that.

You can still type to ARC without a mic. You just won't get the interesting half.

## Install

Open PowerShell and run:

```powershell
git clone https://github.com/theepangnani/ARC-BELLA.git arc
cd arc
pip install -r requirements.txt
```

No git? Download the ZIP from the same page and unzip it anywhere. The launchers
work out their own paths, so the folder can live wherever you like.

Optional, but you'll want it — Windows volume control needs one extra package
that isn't in `requirements.txt`:

```powershell
pip install pycaw comtypes
```

Skip it and everything else still works; ARC just can't change your volume.

## Configure

Copy the example config and open it:

```powershell
copy env.example.txt .env
notepad .env
```

Set these two lines:

```ini
ANTHROPIC_API_KEY=sk-ant-...your own key...
ARC_AUTH_MODE=open
```

`ARC_AUTH_MODE=open` means no sign-in at all. That is safe **only** because ARC
binds to localhost — nothing outside your PC can reach it. ARC enforces this
itself: it refuses to start in open mode on any non-loopback address, rather
than trusting you to have thought about it. In this mode you don't need a Google
OAuth client, an allowlist, or any of the sign-in setup.

Leave everything else at its defaults for now.

## Run it

```powershell
python run.py
```

A window opens on `http://localhost:8420`. The startup banner tells you what's
live:

```
  reasoning   online (claude-sonnet-5, effort medium)
  voice       online (browser fallback)
  google      not configured
  telegram    not configured
  computer    online (full control — localhost only)
  tools       45 live
  access      OPEN — localhost only, no sign-in
```

`computer online` is the line that matters for screen features. Grant the
microphone permission when Chrome asks, then say something.

**Use `localhost:8420`, not `127.0.0.1:8420`.** They're the same machine, but
several things key off the exact hostname.

## Check the screen features

This is the whole reason for installing your own copy:

1. Click **Live screen** until it says *on*. Now ask "what's on my screen?" — it
   should describe what you're actually looking at.
2. Click **Watch screen**. ARC checks every few seconds and speaks up only when
   something meaningfully changes.

Two monitors? Live screen cycles **off → primary → all screens → off**. "All
screens" attaches one image per monitor on every message, so it's slower and
costs more — leave it on primary unless you need the other screen read.

If **Live screen** is greyed out, the banner will have said `computer` was not
online. On a normal local install that shouldn't happen.

## Optional extras

Each of these is genuinely optional — ARC runs fine with none of them.

**A real voice.** Out of the box you get free Edge neural TTS, which is decent.
For the better voice, add an [ElevenLabs](https://elevenlabs.io) key and voice id
to `.env` as `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`.

**Your calendar and email.** This needs your own Google Cloud project — do not
use anyone else's `credentials_web.json`, it carries their client secret.

1. [console.cloud.google.com](https://console.cloud.google.com) → new project
2. **APIs & Services → Library** → enable Google Calendar API and Gmail API
3. **Credentials → Create credentials → OAuth client ID → Desktop app**
4. Download the JSON, rename it `credentials.json`, put it in the `arc` folder
5. Run `python gauth.py` once and sign in

ARC asks for calendar read/write and **Gmail read-only**. It cannot send, reply,
delete or label mail — that's deliberate, not a limitation to work around.

**Telegram.** `python tg_login.py`, then follow the prompts.

## When something's wrong

| Symptom | Cause |
| --- | --- |
| `python` not recognised | PATH box unticked during install. Reinstall Python and tick it. |
| ARC can't hear you | Check the input bar moves in Settings → System → Sound → Input. Then check Chrome's mic permission for `localhost:8420`. |
| Firefox hears nothing | Expected. Use Chrome or Edge. |
| **Live screen** greyed out | Banner said `computer` not online, or you're not on `localhost`. |
| Refuses to start, mentions loopback | `ARC_AUTH_MODE=open` with a non-loopback `ARC_HOST`. Remove `ARC_HOST`. |
| Refuses to start, mentions Google | You set `ARC_AUTH_MODE=google` without the sign-in files. Use `open` instead. |
| 401 on every request | Same as above — you're in `google` mode by accident. |

## One thing to keep in mind

ARC can read your files, see your screen and run shell commands on the machine
it's installed on. That's the point of it, and it's why it stays on localhost by
default. Don't expose port 8420 to the internet without reading the deployment
section of the main README first — `ARC_AUTH_MODE=open` in particular hands your
whole machine to anyone who finds the port, which is exactly why ARC refuses to
combine it with a public bind.
