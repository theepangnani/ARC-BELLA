# -*- coding: utf-8 -*-
"""The market outlook, and the promise it must never break.

"Where is this going" is a fair question with no honest confident answer, so
what this checks hardest is not the arithmetic — it is that the arithmetic is
never dressed up as foresight. A range with odds on it is useful. A price
target is theatre, and the moment ARC starts producing them it is worse than
useless, because someone might act on it.

No network: the indicators run on made-up series with known answers, and the
prose runs against a stubbed measurement.
"""
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import ARC, HUD, sandbox, Check, prompt_text   # noqa: E402
sandbox()


# ARC's own instructions live server-side now (prompts/main.md), where a
# browser cannot edit them. These checks ask what ARC is TOLD, so they read
# the prompt rather than the page it used to be pasted into.
PROMPT = prompt_text()
import market   # noqa: E402
import extras   # noqa: E402

c = Check()

print("Indicators, against series whose answers are known:")
flat = [100.0] * 60
c("a flat line has no volatility", round(market._volatility(flat) or 0, 6), 0.0)
c("  its average is itself", market._sma(flat, 20), 100.0)
c("  RSI of no movement is 100 by convention", market._rsi(flat), 100.0)

rising = [100.0 * (1.01 ** i) for i in range(60)]
c.truthy("a rising series is above its own average", rising[-1] > market._sma(rising, 20))
c.truthy("  and RSI is pinned high", market._rsi(rising) > 90)
falling = list(reversed(rising))
c.truthy("a falling series is below its average", falling[-1] < market._sma(falling, 20))
c.truthy("  and RSI is pinned low", market._rsi(falling) < 10)

# A stock that moves 1% a day, up or down, annualises to about 16%.
import random  # noqa: E402
random.seed(7)
wobble = [100.0]
for _ in range(200):
    wobble.append(wobble[-1] * (1.01 if random.random() < 0.5 else 0.99))
vol = market._volatility(wobble)
c.truthy("1%%-a-day noise annualises near 16%% (got %.1f%%)" % (vol * 100),
         0.13 < vol < 0.19)

print("\nThe band widens with time, and never goes negative:")
lo1, hi1 = market.band(100, 0.30, 5)
lo2, hi2 = market.band(100, 0.30, 21)
lo3, hi3 = market.band(100, 0.30, 252)
c.truthy("a week is narrower than a month", (hi1 - lo1) < (hi2 - lo2))
c.truthy("a month is narrower than a year", (hi2 - lo2) < (hi3 - lo3))
c.truthy("a year at 30%% vol is roughly +/-30%% (%.0f to %.0f)" % (lo3, hi3),
         70 < lo3 < 76 and 130 < hi3 < 136)
c.truthy("even absurd volatility cannot produce a negative price",
         market.band(100, 5.0, 252)[0] > 0)
c.truthy("two sigma is wider than one",
         market.band(100, .3, 21, 2.0)[1] > market.band(100, .3, 21, 1.0)[1])
c("no volatility, no band", market.band(100, None, 21), None)
# The centre of the band is today's price, which IS the honest estimate.
lo, hi = market.band(100, 0.30, 21)
c.truthy("the band is centred on today's price (geometrically)",
         abs(math.sqrt(lo * hi) - 100) < 0.01)

print("\nHorizons people actually say:")
for said, want in [("a week", 5), ("next month", 21), ("this quarter", 63),
                   ("6 months", 126), ("a year", 252), ("", 21), ("gibberish", 21)]:
    c("  %-12s -> %d trading days" % (repr(said), want), market.horizon_days(said)[0], want)

print("\nThe prose it produces — this is the part that matters:")
FAKE = {"symbol": "TEST", "currency": "USD", "price": 100.0,
        "sma20": 98.0, "sma50": 95.0, "sma200": 90.0, "rsi": 55.0, "vol": 0.30,
        "chg_week": 1.2, "chg_month": 4.0, "chg_quarter": 9.0, "chg_year": 22.0,
        "high": 110.0, "low": 70.0, "drawdown": -9.1, "days": 250}
market.measure = lambda sym: FAKE
extras.yahoo_search = lambda q: "TEST"
out = market.market_outlook("anything", "month")
print("      " + out[:110] + "...")

c.truthy("it gives a range", "between" in out)
c.truthy("with the odds attached", "two thirds" in out and "nineteen times in twenty" in out)
c.truthy("it says the centre is roughly today's price", "roughly where it is now" in out)
c.truthy("it says outright that it is not a prediction", "NOT A PREDICTION" in out)
c.truthy("it names the assumption that fails", "if the future behaves like the past" in out)
c.truthy("it admits ARC does not know", "Nobody, including me, knows" in out)
c.truthy("it says it is not advice", "not as advice" in out)

# The failure that would matter: a sentence that tells someone what happens next.
FORECAST = re.compile(r"\b(will (rise|fall|hit|reach|go|drop|climb)|"
                      r"expect(ed)? to (rise|fall|hit|reach)|"
                      r"target of|price target|guaranteed|certain to|"
                      r"should buy|should sell|recommend)\b", re.I)
c("no sentence predicts or advises", FORECAST.search(out), None)
c.truthy("trend is described in the past tense", "The chart is" in out)

print("\nIt degrades honestly rather than inventing:")
market.measure = lambda sym: None
c.truthy("no data -> says so, gives no numbers",
         "worth hearing" in market.market_outlook("ZZZZ"))
c.truthy("no symbol -> asks", "Which one" in market.market_outlook(""))
market.measure = lambda sym: dict(FAKE, vol=None)
c.truthy("no volatility -> refuses to put a range on it",
         "not enough clean data" in market.market_outlook("TEST"))
market.measure = lambda sym: FAKE

print("\nComparing several:")
out = market.market_compare("a, b")
c.truthy("ranks them", "best first" in out)
c.truthy("and says what a ranking of the past is worth",
         "not a ranking of what will" in out)
c.truthy("asks when given nothing", "Which ones" in market.market_compare(""))

print("\nWired into ARC:")
import run  # noqa: E402
c.truthy("both tools registered", {"market_outlook", "market_compare"} <=
         set(run.TOOL_OWNER))
c.truthy("owned by the market module", run.TOOL_OWNER["market_outlook"] is market)
c.truthy("a guest may use them (public prices, no owner data)",
         {"market_outlook", "market_compare"} <= run.GUEST_TOOLS)
c.truthy("passive — analysis changes nothing, so no consent prompt",
         {"market_outlook", "market_compare"} <= run.PASSIVE_TOOLS)
c.truthy("still offered over the tunnel (nothing local about it)",
         "market_outlook" in {t["name"] for t in run.all_tools(local=False)})

print("\nAnd ARC is told how to speak about it:")
page = open(HUD, encoding="utf-8").read()
c.truthy("never say 'will'", 'NEVER say a stock "will" do anything' in PROMPT)
c.truthy("no price targets", "never give a price target as a fact" in PROMPT)
c.truthy("speak the odds with the range", "Speak the RANGE with its odds" in PROMPT)
c.truthy("past tense for indicators", "Never present them as what will happen" in PROMPT)
c.truthy("refuses to tell them what to do with their money",
         "not going to tell them what to do with their money" in PROMPT)
c.truthy("always calls the tool rather than reciting stale training",
         "prices move and your training is old" in PROMPT)

c.done()
