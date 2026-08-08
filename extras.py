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
import datetime as dt
from pathlib import Path

import httpx

ROOT = Path(__file__).parent.resolve()
TODO_FILE = ROOT / "todos.json"

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
]

_DISPATCH = {"weather": weather, "add_todo": add_todo,
             "list_todos": list_todos, "complete_todo": complete_todo}


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
