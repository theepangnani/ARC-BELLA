#!/usr/bin/env python3
"""
Small self-contained capabilities that need no accounts or keys:

  · weather   — current conditions + a short forecast, via Open-Meteo (free,
                no API key, includes free geocoding).
  · to-do     — a persistent list, kept in todos.json. Distinct from ARC's
                long-term memory: memory is durable facts about you, this is
                a list of things to do that you tick off.

Everything returns prose, since it is spoken aloud.
"""

import json
import time
import datetime as dt
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.resolve()
TODO_FILE = ROOT / "todos.json"
REMIND_FILE = ROOT / "reminders.json"

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
WX_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes → plain words. Coarse on purpose; it's spoken.
_WMO = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
    81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "severe thunderstorms",
}


def connected() -> bool:
    return True     # no setup needed


# --- weather ---------------------------------------------------------------

def weather(location: str = "", when: str = "today") -> str:
    loc = (location or "").strip()
    if not loc:
        return "Tell me which place you want the weather for."
    try:
        with httpx.Client(timeout=12) as c:
            g = c.get(GEO_URL, params={"name": loc, "count": 1}).json()
            results = g.get("results") or []
            if not results:
                return f"I couldn't find a place called {loc}."
            place = results[0]
            lat, lon = place["latitude"], place["longitude"]
            label = ", ".join(x for x in (place.get("name"), place.get("admin1"),
                                          place.get("country")) if x)
            w = c.get(WX_URL, params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,apparent_temperature,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 3,
            }).json()
    except Exception as e:
        return f"Couldn't reach the weather service: {e}"

    cur = w.get("current", {})
    daily = w.get("daily", {})
    want = (when or "today").strip().lower()
    idx = 1 if "tomorrow" in want else 0

    if idx == 0 and cur:
        desc = _WMO.get(cur.get("weather_code"), "unclear")
        t = round(cur.get("temperature_2m", 0))
        feels = round(cur.get("apparent_temperature", t))
        hi = round(daily["temperature_2m_max"][0]); lo = round(daily["temperature_2m_min"][0])
        pop = daily.get("precipitation_probability_max", [None])[0]
        rain = f", {pop} percent chance of rain" if pop is not None else ""
        feelsbit = f", feels like {feels}" if abs(feels - t) >= 2 else ""
        return (f"In {label} it's {t} degrees and {desc}{feelsbit}. "
                f"Today's high is {hi}, low {lo}{rain}.")
    # tomorrow (or fallback)
    if idx < len(daily.get("time", [])):
        desc = _WMO.get(daily["weather_code"][idx], "unclear")
        hi = round(daily["temperature_2m_max"][idx]); lo = round(daily["temperature_2m_min"][idx])
        pop = daily.get("precipitation_probability_max", [None]*3)[idx]
        rain = f", {pop} percent chance of rain" if pop is not None else ""
        return f"Tomorrow in {label}: {desc}, high {hi}, low {lo}{rain}."
    return f"I don't have a forecast that far out for {label}."


# --- to-do list ------------------------------------------------------------

def _load():
    try:
        return json.loads(TODO_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(items):
    TODO_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def add_todo(item: str) -> str:
    text = (item or "").strip()
    if not text:
        return "Nothing to add."
    items = _load()
    items.append({"text": text[:200], "added": dt.datetime.now().isoformat(timespec="seconds")})
    _save(items)
    return f"Added to your list: {text}. That's {len(items)} item{'s' if len(items) != 1 else ''}."


def list_todos() -> str:
    items = _load()
    if not items:
        return "Your to-do list is empty."
    lines = [f"{i+1}. {it['text']}" for i, it in enumerate(items)]
    return f"You have {len(items)} thing{'s' if len(items)!=1 else ''} to do:\n" + "\n".join(lines)


def complete_todo(which: str) -> str:
    items = _load()
    if not items:
        return "Your list is already empty."
    w = (which or "").strip().lower()
    idx = None
    if w.isdigit() and 1 <= int(w) <= len(items):
        idx = int(w) - 1
    else:
        for i, it in enumerate(items):
            if w and w in it["text"].lower():
                idx = i
                break
    if idx is None:
        return f"I couldn't find '{which}' on your list."
    done = items.pop(idx)
    _save(items)
    return f"Ticked off: {done['text']}. {len(items)} left."


# --- reminders (persistent; fire even after a reload) ----------------------

def _load_rem():
    try:
        return json.loads(REMIND_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_rem(items):
    REMIND_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _human_delay(s):
    s = max(0, int(s))
    if s < 60:
        return f"in {s} second{'s' if s != 1 else ''}"
    m = round(s / 60)
    if m < 60:
        return f"in {m} minute{'s' if m != 1 else ''}"
    h = s / 3600
    if h < 24:
        return f"in about {round(h, 1)} hours"
    return f"in about {round(h / 24, 1)} days"


def set_reminder(label: str = "", seconds=None, at: str = "") -> str:
    """Set a reminder. Give EITHER seconds (delay from now) OR at (an ISO local
    datetime like 2026-08-09T18:00). It persists and fires even after a reload."""
    text = (label or "").strip()
    if not text:
        return "What should I remind you about?"
    now = time.time()
    fire = None
    if seconds is not None:
        try:
            fire = now + float(seconds)
        except (TypeError, ValueError):
            return "Tell me how many seconds from now, or a time."
    elif at:
        try:
            fire = dt.datetime.fromisoformat(str(at).replace("Z", "")).timestamp()
        except Exception:
            return f"I couldn't understand the time '{at}'. Use an ISO time like 2026-08-09T18:00."
    else:
        return "Tell me when — in how many seconds, or at what time."
    if fire < now - 1:
        return "That time is already in the past."
    items = _load_rem()
    items.append({"id": str(int(now * 1000)), "fire_at": fire,
                  "label": text[:200], "delivered": False})
    _save_rem(items)
    return f"I'll remind you {_human_delay(fire - now)}: {text}."


def list_reminders() -> str:
    now = time.time()
    items = sorted([r for r in _load_rem() if not r.get("delivered")],
                   key=lambda r: r["fire_at"])
    if not items:
        return "You have no reminders set."
    lines = [f"{i+1}. {r['label']} ({_human_delay(r['fire_at'] - now)})"
             for i, r in enumerate(items)]
    return f"You have {len(items)} reminder{'s' if len(items) != 1 else ''}:\n" + "\n".join(lines)


def cancel_reminder(which: str = "") -> str:
    w = (which or "").strip().lower()
    items = _load_rem()
    pend = [r for r in items if not r.get("delivered")]
    if not pend:
        return "You have no reminders to cancel."
    target = None
    if w.isdigit() and 1 <= int(w) <= len(pend):
        target = pend[int(w) - 1]
    else:
        for r in pend:
            if w and w in r["label"].lower():
                target = r
                break
    if not target:
        return f"I couldn't find a reminder matching '{which}'."
    _save_rem([r for r in items if r["id"] != target["id"]])
    return f"Cancelled the reminder: {target['label']}."


def due_reminders():
    """For the poller: reminders whose time has come. Marks them delivered so
    they fire once, and prunes ones delivered over a day ago."""
    items = _load_rem()
    now = time.time()
    due, changed = [], False
    for r in items:
        if not r.get("delivered") and r["fire_at"] <= now:
            r["delivered"] = True
            changed = True
            due.append({"id": r["id"], "label": r["label"]})
    if changed:
        items = [r for r in items
                 if not (r.get("delivered") and r["fire_at"] < now - 86400)]
        _save_rem(items)
    return due


# --- stocks ----------------------------------------------------------------

def yahoo_quote(symbol):
    """Live quote for a ticker via Yahoo Finance (free, no key). Returns a dict
    or None. Symbols: AAPL, TSLA, ^GSPC (S&P 500), BTC-USD, etc."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as c:
            d = c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                      params={"interval": "1d", "range": "1d"}).json()
    except Exception:
        return None
    res = (d.get("chart", {}).get("result") or [None])[0]
    if not res:
        return None
    m = res.get("meta", {})
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    if price is None:
        return None
    change = pct = None
    if prev:
        change = price - prev
        pct = change / prev * 100 if prev else None
    return {"symbol": m.get("symbol", sym), "price": price, "prev": prev,
            "change": change, "pct": pct, "currency": m.get("currency", "")}


def stock(symbol: str = "") -> str:
    """Spoken stock quote. Pass a ticker (AAPL, TSLA, BTC-USD). Convert company
    names to tickers yourself before calling."""
    sym = (symbol or "").strip()
    if not sym:
        return "Which stock? Give me a ticker like AAPL or TSLA."
    q = yahoo_quote(sym)
    if not q:
        return f"I couldn't get a quote for {sym}. Check the ticker symbol."
    price = round(q["price"], 2)
    cur = q["currency"] or ""
    if q["pct"] is not None:
        dirn = "up" if q["change"] >= 0 else "down"
        return (f"{q['symbol']} is at {price} {cur}, {dirn} "
                f"{abs(round(q['pct'], 2))} percent today.").replace("  ", " ")
    return f"{q['symbol']} is at {price} {cur}.".replace("  ", " ")


# --- news ------------------------------------------------------------------

def news(topic: str = "", count: int = 5) -> str:
    """Top headlines, via Google News RSS (free, no key). With a topic it
    searches for it; without, it's the general top stories."""
    topic = (topic or "").strip()
    try:
        n = max(1, min(int(count or 5), 8))
    except (TypeError, ValueError):
        n = 5
    if topic:
        url = "https://news.google.com/rss/search"
        params = {"q": topic, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    else:
        url = "https://news.google.com/rss"
        params = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        with httpx.Client(timeout=12, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True) as c:
            xml = c.get(url, params=params).text
    except Exception as e:
        return f"Couldn't reach the news service: {e}"
    import re as _re
    import html as _html
    titles = _re.findall(r"<item>.*?<title>(.*?)</title>", xml, _re.S)
    heads = []
    for t in titles:
        t = _re.sub(r"<!\[CDATA\[|\]\]>", "", t)
        t = _html.unescape(t).strip()
        # Google appends " - Source"; keep the headline, drop the trailing source.
        t = _re.sub(r"\s+-\s+[^-]+$", "", t)
        if t:
            heads.append(t)
        if len(heads) >= n:
            break
    if not heads:
        return f"I couldn't find any headlines{' for ' + topic if topic else ''} just now."
    label = f"Top headlines on {topic}" if topic else "Top headlines"
    return label + ":\n" + "\n".join(f"- {h}" for h in heads)


# --- wire format -----------------------------------------------------------

TOOLS = [
    {"name": "weather",
     "description": "Current weather and a short forecast for a place. Use for any weather question. 'when' can be 'today' or 'tomorrow'.",
     "input_schema": {"type": "object", "properties": {
         "location": {"type": "string", "description": "City or place name."},
         "when": {"type": "string", "description": "'today' (default) or 'tomorrow'."}},
         "required": ["location"]}},
    {"name": "add_todo",
     "description": "Add an item to the user's to-do list. Use when they say to add, remind, or put something on their list. This is a task list, distinct from long-term memory (durable facts).",
     "input_schema": {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}},
    {"name": "list_todos",
     "description": "Read back the user's to-do list.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "complete_todo",
     "description": "Tick an item off the list, by its number or by matching words in it.",
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": ["which"]}},
    {"name": "set_reminder",
     "description": ("Set a reminder that persists and fires even after a page reload (better than the [[timer]] "
                     "marker for anything the user should be reminded of). Give EITHER 'seconds' (delay from now — "
                     "compute it from the current time you're given) OR 'at' (ISO local datetime, e.g. "
                     "2026-08-09T18:00). Always include a clear 'label'."),
     "input_schema": {"type": "object", "properties": {
         "label": {"type": "string"},
         "seconds": {"type": "integer", "description": "Delay from now, in seconds."},
         "at": {"type": "string", "description": "ISO local datetime, e.g. 2026-08-09T18:00."}},
         "required": ["label"]}},
    {"name": "list_reminders",
     "description": "Read back the user's pending reminders and when each will fire.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cancel_reminder",
     "description": "Cancel a pending reminder by its number (from list_reminders) or by matching words in its label.",
     "input_schema": {"type": "object", "properties": {"which": {"type": "string"}}, "required": ["which"]}},
    {"name": "stock",
     "description": ("Live stock/crypto/index quote — current price and today's move. Pass a ticker symbol (AAPL, "
                     "TSLA, NVDA, BTC-USD, ^GSPC for the S&P 500). Convert company names to their ticker yourself."),
     "input_schema": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}},
    {"name": "news",
     "description": ("Top news headlines. With a 'topic' it searches for that subject; without, it's the general "
                     "top stories. Use for 'what's the news', 'any news on X', and in a morning briefing."),
     "input_schema": {"type": "object", "properties": {
         "topic": {"type": "string"}, "count": {"type": "integer"}}}},
]

_DISPATCH = {"weather": weather, "add_todo": add_todo,
             "list_todos": list_todos, "complete_todo": complete_todo,
             "set_reminder": set_reminder, "list_reminders": list_reminders,
             "cancel_reminder": cancel_reminder, "stock": stock, "news": news}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True
