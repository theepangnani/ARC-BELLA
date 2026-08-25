# Tests

```
python tests/run_all.py            # everything
python tests/run_all.py alarm      # just the suites matching "alarm"
python tests/test_session.py       # one suite, full output
```

No pytest, no fixtures, no conftest. Each suite is a script that prints what it
checked and exits non-zero if anything failed, because the output is meant to
be read: `PASS  31 minutes idle -> still live` says what the system promises in
a way `test_idle_expiry PASSED` does not.

Everything here runs with **no network, no API keys, no Google account and no
microphone**. Claude is stubbed where a suite needs a reply; the Google flow
runs against a fake client the harness writes.

## The sandbox

`_harness.py` points `ARC_DATA_DIR` at a fresh temporary directory before ARC
is imported, and deletes it afterwards. This is not tidiness. The suites call
`revoke_all()`, delete Google token files, write alarms and clear reminders —
run against a live install they would sign you out of your own assistant,
delete the refresh token behind it, and cancel the alarm set for the morning,
from a command whose whole promise is that it changes nothing.

`sandbox()` refuses to start if `ARC_DATA_DIR` points anywhere that looks live.
If you see that refusal, unset it — do not work around it.

## What is covered

| Suite | What it holds ARC to |
|---|---|
| `test_session` | Idle expiry means *you* stopped, not the tab. Background polls don't hold a session open |
| `test_unlimited` | The owner has no session clock; guests keep all three; an unlimited session is still revocable |
| `test_hygiene` | Sessions can't pile up; de-allowlisting ends access now; orphaned Google tokens are cleared |
| `test_leave` | Closing the tab signs out, refreshing does not |
| `test_login` | The sign-in round trip, including the `Max-Age=0` header that once binned every session |
| `test_guest` / `test_guestchat` / `test_routes` | A guest gets a cut-down ARC and cannot promote itself by asking |
| `test_alarm` / `test_alarm_regress` / `test_alarm_routes` | Alarms fire, recur across DST, survive a reload, and are not consumed by polling |
| `test_promptsize` | The system-prompt ceiling clears a realistic turn — it once didn't, and every message 400'd |
| `test_echo` | ARC ignores its own voice coming back, and does *not* ignore you |
| `test_stop` / `test_tail` | "Stop" stops it; the tail of its own sentence doesn't |
| `test_board` / `test_learn` | The whiteboard and lesson mode, and the markdown strip that broke both |
| `test_inputlevel` | The INPUT LVL meter reads the microphone, not ARC's own output |
| `test_speaker` | Speaker mode: wake lock, loudspeaker routing, nothing read before it exists |
| `test_mobile` | Notches, gesture bars, soft keyboards, and iOS being WebKit whatever the icon says |
| `test_js_syntax` | The 280 KB of inline JavaScript actually parses |
| `test_selfheal` | Self-repair restores from a copy, walks past a corrupt copy, keeps the damaged file, and stops at its own source, the secrets and the sign-in store |

## Two dependencies you may want

`test_js_syntax` needs a JavaScript parser: `pip install -r requirements-dev.txt`
for esprima, or have `node` on PATH. Without either it says clearly that it
checked nothing, rather than reporting a pass nobody earned.

## tests/manual

Suites that need real hardware — a second monitor, a live screen, a microphone
in a room. They print measurements rather than pass or fail, and `run_all.py`
does not touch them. Run one when you are changing that part and can look at
the result.
