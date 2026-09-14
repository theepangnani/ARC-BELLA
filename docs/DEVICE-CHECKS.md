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
2. **You should see:** the browser asks which screen or window to share. Pick one,
   then ask "what's on my screen?"
3. **You should see:** Bella describes the laptop's screen, not the desktop's, and
   says she can look but not click.
4. Press the browser's own "Stop sharing". The button should turn off.
5. Cancel the picker once. There should be no error message.

**On a phone:**
1. Press Live screen.
2. **You should see:** most phone browsers can't share a screen. Bella should say so
   and suggest the camera button, not show an error.

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

## Who to tell

- **Claude 1:** anything that needs a setting changed or a restart, and Live screen
  problems.
- **Claude 2:** streaming or the picture limit behaving wrongly.
