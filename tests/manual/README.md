# Manual suites

These need hardware the test runner does not have, so they print measurements
rather than passing or failing, and `run_all.py` leaves them alone. Run one
when you are changing that part of ARC and can look at what it prints.

| Suite | Needs | Shows |
|---|---|---|
| `test_monitors.py` | a real desktop, ideally two screens | What each `screenshot()` selector captures, and the size it sends |
| `test_watch.py` | two screens | Whether a change on the second screen can reach the watch gate |
| `test_noise.py` | a microphone, and a quiet room then a noisy one | Live input levels, to set the noise gate against your room |

From the repo root:

    python tests/manual/test_noise.py
