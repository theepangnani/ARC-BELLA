<!-- ARC — Ambient Response Core.  Copyright (c) 2026 Theepan Gnanasabapathy.
     All rights reserved. Proprietary; see LICENSE. Visibility is not permission. -->
# Checks on a real phone and laptop

Tests cover the code. They can't hold a phone. These are the checks that need
a real device after the restart that turns on the new features. Each one says
what to do, what you should see, and what to tell a Claude if you don't.

## Before you start

- Tell Claude 1 on the desktop which settings you want, then approve the restart:
  - `ARC_STREAM=1` on both Bellas.
  - `ARC_GUEST_IMAGES_PER_DAY` on shared ARC, with a number (for example 30).
- Wait about 75 seconds after the restart, then reload the page on each device.
- For the guest check you need a guest account, meaning an email address that is in
  `ARC_GUEST_EMAILS`.

## 1. Answers arrive as they are written (streaming)

Do this on the desktop, then again on your phone through the public address.

1. Ask something with a long answer, like "tell me about the history of Rome".
2. **You should see:** words appearing a few at a time, and Bella starting to speak
   before the whole answer is written.
3. **If the whole answer appears at once on the phone but streams on the desktop:**
   the public tunnel is holding the stream back. Answers still work, they are
   just not faster there. Tell Claude 2.
4. **If you get an error, or no answer at all:** tell Claude 2 right away. Turning off
   `ARC_STREAM` puts things back the way they were.

## 2. Live screen

**On the desktop:**
1. Press Live screen.
2. **You should see:** it works as before. Bella sees this computer's screen.

**On a laptop through the public address:**
1. Press Live screen.
2. **You should see:** the browser asks which screen or window to share. Pick one.
   A SYSTEM note appears saying she will see it with every message and can look,
   not click.
3. Ask "what's on my screen?" **You should see:** Bella describes the laptop's
   screen, not the desktop's.
4. Press the browser's own "Stop sharing". **You should see:** the button goes back
   to "Live screen: off", and a SYSTEM note says "Screen sharing stopped. I can't
   see your screen now."
5. Press Live screen again and cancel the picker. **You should see:** no message at
   all, and the button stays off.

**On a phone:**
1. **You should see:** the button already reads "Live screen: n/a", because most
   phone browsers can't share a screen.
2. Press it. **You should see:** a SYSTEM note saying this browser can't share its
   screen, and suggesting a computer or the camera button.

## 3. The guest picture limit

Only do this if you set `ARC_GUEST_IMAGES_PER_DAY`. A small number like 2 makes
the check quick; set it back afterwards.

1. Sign in as a guest.
2. Send a camera photo with a question, as many times as the limit.
3. **You should see:** Bella answers about each photo.
4. Send one more.
5. **You should see:** Bella still answers the words, says in one sentence that
   today's pictures are used up, and does not describe the photo.
6. Sign in as yourself and send a photo. **You should see:** it is never limited.

The count resets at midnight, and also on every restart.

## 4. Your phone still reaches Bella

Since the request gate (15 Sep 2026), Bella only answers to addresses she knows.

1. Open Bella on your phone the way you usually do.
2. **You should see:** Bella, as normal.
3. **If you see a short error page with the code 421:** add the address your phone
   uses (the part after `https://`, without any path) to `ARC_ALLOWED_HOSTS` in the
   desktop settings, then restart.

## 5. Mini Bella

On the desktop, with the private Bella running.

1. Minimise Bella's window.
2. **You should see:** a small circle with her logo at the bottom right, just above
   the taskbar.
3. Bring Bella back. **You should see:** the circle disappears.
4. Minimise her again and **left-click** the circle. **You should see:** a small
   "Mini Bella" chat window opens just above it.
5. Type "what time is it" and press Enter. **You should see:** an answer in text.
6. Click the circle again. **You should see:** the same chat comes to the front, not
   a second one.
7. **Right-click** the circle. **You should see:** Bella's full window comes back.

**If no circle appears:** check that `ARC_MINI_BELLA` isn't set to `off`, then tell
Claude 2.

## 6. A yes covers what you were asked about

With "Ask before acting" on.

1. Ask Bella to do something on the computer, like "open Notepad and type hello".
2. **You should see:** a SYSTEM note listing exactly what she wants to do, ending
   "Say "yes" to allow just that".
3. Say "yes". **You should see:** she does that, and more of the same kind (clicks and
   typing) in the same answer.
4. Ask for something of a different kind in the same breath, like running a command.
   **You should see:** she asks again for that one.

In Mini Bella the note ends "Reply "yes" to allow just that". There is no switch to
turn asking off.

## 7. Outside reading makes alarms and timers ask

1. Ask Bella to read your latest email or search the news.
2. In the same conversation, ask "set a timer for 1 minute".
3. **You should see:** instead of a timer starting, a note "Bella wants to start a
   60-second timer. This came up while reading something from outside. Do it?"
   with **Do it** and **Skip**.
4. Click **Do it**. **You should see:** the timer starts. **Skip** starts nothing.
5. After 15 minutes with no outside reading, a timer starts straight away again.

## Who to tell

- **Claude 1:** anything that needs a setting changed or a restart, and Live screen
  problems.
- **Claude 2:** streaming, the picture limit, Mini Bella's circle, or the Do it / Skip
  cards behaving wrongly.
- **Claude 4:** the Mini Bella chat page, or a yes covering too much or too little.
