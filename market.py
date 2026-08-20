#!/usr/bin/env python3
"""
Market outlook for ARC — what the numbers actually support, and no more.

"Bella, where's Nvidia going?" is a fair question to ask and an unfair question
to answer. Nobody can tell you where a stock will be next month. What can be
done honestly is this: measure what the price has been doing, measure how much
it moves about, and turn that into a RANGE with a probability attached — then
say plainly that it is an extrapolation of the past and not knowledge of the
future.

So the model here is a random walk, deliberately. The central estimate for any
horizon is roughly today's price, because that is what the evidence supports;
everything interesting is in the width of the band around it, which comes from
the stock's own realised volatility. A 1-sigma band is right about two thirds
of the time and a 2-sigma band about nineteen times in twenty — those are real
numbers a person can act on, unlike a price target, which is theatre.

The trend, momentum and RSI figures are reported as CONTEXT — a description of
what has happened — never as a forecast. That distinction is the whole point of
this module, and the system prompt holds ARC to it too: no "it will", no target
stated as fact, and the uncertainty said out loud rather than buried.

Free data, no key: the same Yahoo chart endpoint the quote widget already uses.
"""

import math
import statistics

import httpx

import extras   # yahoo_search / yahoo_quote — one quote source for the whole app

# A year of daily closes: enough for a 200-day average and a 52-week range,
# short enough that the volatility figure still describes the stock as it is
# now rather than as it was three regimes ago.
HISTORY_RANGE = "1y"

# Trading days, for annualising and for horizons. Calendar days would overstate
# the drift of a market that is shut at weekends.
YEAR_DAYS = 252

HORIZONS = {
    "week": 5, "1 week": 5, "a week": 5,
    "fortnight": 10, "2 weeks": 10,
    "month": 21, "1 month": 21, "a month": 21,
    "quarter": 63, "3 months": 63,
    "6 months": 126, "half a year": 126,
    "year": 252, "1 year": 252, "a year": 252,
}


def connected() -> bool:
    # Public data, no account, no key. Available wherever there is a network.
    return True


# --- data ------------------------------------------------------------------

def _history(symbol: str):
    """Daily closes for the last year. Returns (symbol, currency, [closes]) or
    None. Closes are cleaned of the nulls Yahoo returns for halted days."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        with httpx.Client(timeout=12, headers={"User-Agent": "Mozilla/5.0"}) as c:
            d = c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                      params={"interval": "1d", "range": HISTORY_RANGE}).json()
    except Exception:
        return None
    res = (d.get("chart", {}).get("result") or [None])[0]
    if not res:
        return None
    meta = res.get("meta", {})
    quotes = (res.get("indicators", {}).get("quote") or [{}])[0]
    closes = [c for c in (quotes.get("close") or []) if isinstance(c, (int, float))]
    if len(closes) < 30:
        return None
    return meta.get("symbol", sym), meta.get("currency", ""), closes


# --- the measurements ------------------------------------------------------

def _sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _returns(closes):
    """Daily log returns. Log rather than simple: they add up across days,
    which is what makes the square-root-of-time scaling below legitimate."""
    out = []
    for a, b in zip(closes, closes[1:]):
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def _volatility(closes, window=126):
    """Annualised volatility from the last six months of daily moves.

    Six months rather than the full year on purpose: volatility changes regime,
    and a figure that still remembers last autumn will understate a market that
    has turned jumpy this week.
    """
    r = _returns(closes)[-window:]
    if len(r) < 20:
        return None
    return statistics.pstdev(r) * math.sqrt(YEAR_DAYS)


def _rsi(closes, n=14):
    """Relative strength index. Above 70 is conventionally 'overbought', below
    30 'oversold' — descriptions of recent buying pressure, not predictions,
    and reported here as such."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100 - (100 / (1 + rs))


def _change(closes, days):
    if len(closes) <= days:
        return None
    old = closes[-days - 1]
    return (closes[-1] - old) / old * 100 if old else None


def _drawdown(closes):
    """How far below the last year's high it is now, as a percentage."""
    hi = max(closes)
    return (closes[-1] - hi) / hi * 100 if hi else None


def measure(symbol: str):
    """Everything the outlook is built from. Separated out so it can be tested
    without a network round trip and reused by anything else that wants the
    numbers rather than the prose."""
    got = _history(symbol)
    if not got:
        return None
    sym, currency, closes = got
    price = closes[-1]
    return {
        "symbol": sym,
        "currency": currency,
        "price": price,
        "sma20": _sma(closes, 20),
        "sma50": _sma(closes, 50),
        "sma200": _sma(closes, 200),
        "rsi": _rsi(closes),
        "vol": _volatility(closes),
        "chg_week": _change(closes, 5),
        "chg_month": _change(closes, 21),
        "chg_quarter": _change(closes, 63),
        "chg_year": _change(closes, min(len(closes) - 1, 252)),
        "high": max(closes),
        "low": min(closes),
        "drawdown": _drawdown(closes),
        "days": len(closes),
    }


# --- turning measurements into words --------------------------------------

def band(price, vol, days, sigmas=1.0):
    """The range this is likely to be in after `days` trading days.

    A random walk with the stock's own volatility: sigma scales with the square
    root of time, and the band is lognormal so it cannot produce a negative
    price. No drift term, deliberately — estimating drift from a year of data
    produces a number dominated by noise, and dressing that up as an expected
    return would be exactly the false precision this module exists to avoid.
    """
    if not vol or not price:
        return None
    s = vol * math.sqrt(days / YEAR_DAYS) * sigmas
    return price * math.exp(-s), price * math.exp(s)


def _trend(m):
    """What the moving averages say has been happening. Past tense on purpose."""
    p, s50, s200 = m["price"], m["sma50"], m["sma200"]
    if not s50 or not s200:
        return "not enough history to call a trend"
    if p > s50 > s200:
        return "in an uptrend — above both its fifty and two-hundred day averages"
    if p < s50 < s200:
        return "in a downtrend — below both its fifty and two-hundred day averages"
    if p > s200:
        return "above its long-run average but choppy underneath it"
    return "below its long-run average, without a clean trend either way"


def _vol_words(vol):
    if vol is None:
        return "unmeasurable volatility"
    pct = vol * 100
    if pct < 15:
        return f"quiet, at about {pct:.0f} percent annualised volatility"
    if pct < 30:
        return f"normal, at about {pct:.0f} percent annualised volatility"
    if pct < 55:
        return f"jumpy, at about {pct:.0f} percent annualised volatility"
    return f"wild, at about {pct:.0f} percent annualised volatility"


def _rsi_words(rsi):
    if rsi is None:
        return ""
    if rsi >= 70:
        return f" RSI is {rsi:.0f}, which is stretched after a run up."
    if rsi <= 30:
        return f" RSI is {rsi:.0f}, which is stretched after a sell-off."
    return f" RSI is {rsi:.0f}, which is unremarkable."


def horizon_days(horizon: str) -> tuple[int, str]:
    h = (horizon or "").strip().lower()
    for key in sorted(HORIZONS, key=len, reverse=True):
        if key in h:
            return HORIZONS[key], key.replace("1 ", "").replace("a ", "")
    return HORIZONS["month"], "month"


def market_outlook(symbol: str = "", horizon: str = "1 month") -> str:
    """The honest answer to 'where is this going'."""
    name = (symbol or "").strip()
    if not name:
        return "Which one? Give me a ticker or a company name."

    sym = extras.yahoo_search(name) or name.upper()
    m = measure(sym)
    if not m:
        return (f"I couldn't get enough price history for {name} to say anything "
                f"worth hearing. Check the ticker.")

    days, label = horizon_days(horizon)
    cur = (" " + m["currency"]) if m["currency"] else ""
    one = band(m["price"], m["vol"], days, 1.0)
    two = band(m["price"], m["vol"], days, 2.0)

    lines = [
        f"{m['symbol']} is at {m['price']:.2f}{cur}.",
        f"Recently: {_fmt_pct(m['chg_week'])} over the past week, "
        f"{_fmt_pct(m['chg_month'])} over the month, "
        f"{_fmt_pct(m['chg_year'])} over the year.",
        f"The chart is {_trend(m)}. Movement is {_vol_words(m['vol'])}."
        f"{_rsi_words(m['rsi'])}",
    ]
    if m["drawdown"] is not None and m["drawdown"] < -1:
        lines.append(f"It is {abs(m['drawdown']):.0f} percent below its twelve-month "
                     f"high of {m['high']:.2f}.")

    if one and two:
        lines.append(
            f"OUTLOOK over the next {label}: the honest central estimate is roughly "
            f"where it is now, {m['price']:.2f}. On its own volatility, about two "
            f"thirds of the time it should land between {one[0]:.2f} and {one[1]:.2f}, "
            f"and about nineteen times in twenty between {two[0]:.2f} and {two[1]:.2f}.")
    else:
        lines.append("OUTLOOK: not enough clean data to put a range on it.")

    lines.append(
        "THIS IS NOT A PREDICTION. It is what the last year of prices implies if "
        "the future behaves like the past, which is exactly the assumption that "
        "fails on the days that matter. One announcement voids all of it. Nobody, "
        "including me, knows where this goes — treat the range as a measure of "
        "uncertainty, not a forecast, and not as advice.")
    return " ".join(lines)


def _fmt_pct(v):
    if v is None:
        return "unknown"
    return f"up {v:.1f} percent" if v >= 0 else f"down {abs(v):.1f} percent"


def market_compare(symbols: str = "") -> str:
    """Several tickers side by side on the same measures — which has actually
    been doing better, and which is the wilder ride."""
    names = [s.strip() for s in (symbols or "").replace(" and ", ",").split(",") if s.strip()]
    if not names:
        return "Which ones? Give me two or more tickers."
    if len(names) > 6:
        names = names[:6]

    rows = []
    for n in names:
        sym = extras.yahoo_search(n) or n.upper()
        m = measure(sym)
        if m:
            rows.append(m)
    if not rows:
        return "I couldn't get history for any of those. Check the tickers."

    rows.sort(key=lambda r: (r["chg_year"] if r["chg_year"] is not None else -999),
              reverse=True)
    out = []
    for r in rows:
        out.append(f"{r['symbol']}: {_fmt_pct(r['chg_year'])} over the year, "
                   f"{_fmt_pct(r['chg_month'])} over the month, "
                   f"{_vol_words(r['vol'])}")
    return ("Over the last year, best first. " + ". ".join(out) +
            ". Past performance is a description of what happened, not a ranking "
            "of what will.")


TOOLS = [
    {"name": "market_outlook",
     "description": (
         "Analyse where a stock, index or crypto stands and what its own volatility "
         "implies for a given horizon. Use for 'where is Nvidia going', 'what do you "
         "think Tesla does this month', 'is bitcoin going up', 'should I be worried "
         "about my Apple shares', 'predict the market'. Returns trend, momentum, RSI, "
         "volatility and a PROBABILITY RANGE — never a price target. The result "
         "includes an honest statement about the limits of it; do not drop that when "
         "you speak it."),
     "input_schema": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "Ticker or company name (NVDA, Apple, BTC-USD, ^GSPC)"},
         "horizon": {"type": "string", "description": "week, month, quarter, 6 months, year. Default a month."}},
         "required": ["symbol"]}},

    {"name": "market_compare",
     "description": (
         "Compare several stocks on the same measures — yearly and monthly move, and "
         "how volatile each is. Use for 'Apple or Microsoft', 'how are my holdings "
         "doing against each other', 'which of these has done best'. Pass a "
         "comma-separated list."),
     "input_schema": {"type": "object", "properties": {
         "symbols": {"type": "string", "description": "Comma-separated tickers or names"}},
         "required": ["symbols"]}},
]

_DISPATCH = {"market_outlook": market_outlook, "market_compare": market_compare}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"Market lookup failed: {e}", True
