# -*- coding: utf-8 -*-
"""What caching saved, counted net of what it cost to write the cache.

Until 14 Sep 2026 "saved" counted only the reads, at nine tenths of the input
price, while turn_cost charged every write at 1.25x. Every turn that wrote
cache reported more saving than there was, and Arc Watch's "Saved by caching"
card took the figure on trust.

The rule these guards hold: saved is exactly what the same tokens would have
cost uncached, minus what they did cost. That can be below zero. The stored
figure keeps it, and only what is spoken or shown stops at zero, so a later
"fix" cannot hide a real loss in the data.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, sandbox, Check   # noqa: E402
DATA = sandbox()

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["ARC_ALLOWED_EMAILS"] = "owner@example.com"

import run     # noqa: E402
import stats   # noqa: E402

c = Check()
SONNET, HAIKU = "claude-sonnet-5", "claude-haiku-4-5"


def near(a, b):
    return abs(a - b) < 1e-12


print("Saved is the uncached price minus the price actually paid:")
p_in = run.prices_for(SONNET)[0]
for r, w in ((17000, 0), (0, 17000), (17000, 2000), (400, 9000), (0, 0)):
    uncached = run.turn_cost(SONNET, tok_in=r + w)
    paid = run.turn_cost(SONNET, cache_read=r, cache_write=w)
    c("  read %-5d write %-5d" % (r, w),
      near(run.cache_saved(SONNET, r, w), uncached - paid), True)

print("\nThe write premium is taken off, not ignored:")
c("  reads alone save nine tenths",
  near(run.cache_saved(SONNET, 1_000_000, 0), p_in * 0.9), True)
c.truthy("  a turn that only wrote cache is a loss",
         run.cache_saved(SONNET, 0, 17000) < 0)
c("  ...of exactly the quarter premium",
  near(run.cache_saved(SONNET, 0, 1_000_000), -p_in * 0.25), True)
c.truthy("  so reads plus writes save less than the reads alone",
         run.cache_saved(SONNET, 17000, 2000) < run.cache_saved(SONNET, 17000, 0))

print("\nAt the rate of the model that answered:")
c.truthy("  Haiku's saving is smaller than Sonnet's for the same tokens",
         run.cache_saved(HAIKU, 17000, 0) < run.cache_saved(SONNET, 17000, 0))

print("\nThe write rate matches the cache that is actually asked for:")
# 1.25 is a 5-minute write. A 1-hour write ("ttl": "1h") is billed at 2x, so
# anyone adding a ttl must change CACHE_WRITE_RATE at the same time.
c("  the rate is still the 5-minute one", run.CACHE_WRITE_RATE, 1.25)
# Only what actually bills at 2x: a ttl key inside a cache_control dict, or the
# old beta header that switched 1h caching on. A bare "ttl" would also match
# settle, little and throttle, and a ttl key in some unrelated dict is no
# business of this rate, so neither is looked for. Comment lines are skipped
# because run.py's note beside the rate names the 1h form on purpose.
TTL_IN_CACHE = re.compile(
    r"""cache_control["']?\]?\s*[:=]\s*(?:dict\([^)]*\bttl\s*=|\{[^}]*["']ttl["'])"""
    r"|extended-cache-ttl", re.S)
c.truthy("  the pattern catches a ttl in a cache_control dict",
         TTL_IN_CACHE.search('head["cache_control"] = {"type": "ephemeral", "ttl": "1h"}'))
c.truthy("  ...written as a keyword argument too",
         TTL_IN_CACHE.search('dict(b, cache_control={"type": "ephemeral",\n "ttl": "1h"})'))
c.truthy("  ...and the beta header",
         TTL_IN_CACHE.search('"anthropic-beta": "extended-cache-ttl-2025-04-11"'))
c("  but not ordinary words, or a ttl key elsewhere",
  [s for s in ('# let the cache settle a little', 'throttle = {"ttl": 60}',
               'cache_control={"type": "ephemeral"}; opts = {"ttl": 5}')
   if TTL_IN_CACHE.search(s)], [])
ttl_hits, scanned = [], []
for p in sorted(ARC.glob("*.py")):
    src = io.open(p, encoding="utf-8").read()
    if "cache_control" not in src and "extended-cache-ttl" not in src:
        continue
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    if TTL_IN_CACHE.search(code):
        ttl_hits.append(p.name)
    scanned.append(p.name)
c.truthy("  run.py is among the files scanned", "run.py" in scanned)
c("  no cache_control asks for a longer ttl", ttl_hits, [])

print("\nchat() uses the helper at both places it counts a saving:")
run_src = io.open(ARC / "run.py", encoding="utf-8").read()
c.truthy("  the Haiku rounds of a stepped-up turn",
         re.search(r"early_saved \+= cache_saved\(model, cache_read - at_step\[2\],\s*"
                   r"cache_write - at_step\[3\]\)", run_src) is not None)
c.truthy("  and the rest of the turn",
         re.search(r"saved = cache_saved\(model, cache_read - at_step\[2\],\s*"
                   r"cache_write - at_step\[3\]\) \+ early_saved", run_src) is not None)
c("  no reads-only formula is left behind",
  run_src.count("(1 - CACHE_READ_RATE)"), 1)

print("\nThe stored figure keeps a loss:")
stats._days.clear()
stats._loaded = True
stats.record(cost=0.03, cache_write=17000, saved=run.cache_saved(SONNET, 0, 17000),
             model=SONNET)
day = stats.day()
c.truthy("  a day that only wrote cache stores a negative saving", day["saved"] < 0)
c.truthy("  and the week's total is not clamped either", stats.totals(7)["saved"] < 0)

print("\n...but never says a negative one out loud:")
said = stats.summary(7)
c("  the saving is left out", "Caching saved" in said, False)
c("  and no negative dollars appear", "$-" in said, False)
stats.record(cost=0.01, cache_read=170000, saved=run.cache_saved(SONNET, 170000, 0),
             model=SONNET)
c.truthy("  once reads outweigh the writes it is said again",
         "Caching saved" in stats.summary(7))
stats._days.clear()

print("\nArc Watch shows a loss as nothing saved, not as the old estimate:")
watch = io.open(ARC / "static" / "watch.html", encoding="utf-8").read()
c.truthy("  a negative total becomes zero", "t.saved < 0 ? 0" in watch)
c.truthy("  and the estimate for old days takes off the writes too",
         "(t.tok_cache_write || 0) * (d.prices.cache_write - d.prices.in)" in watch)
c.truthy("  ...at the rates the server sends, not numbers typed into the page",
         "* 0.9" not in watch and "* 0.25" not in watch)

c.done()
