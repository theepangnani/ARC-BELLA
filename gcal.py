#!/usr/bin/env python3
"""
Google Calendar hands for ARC.

Sign-in lives in gauth.py and is shared with gmail.py — one consent screen,
one token, whatever set of tools is switched on.

The tools return plain prose rather than JSON, because the text goes straight
into a model that is about to say it out loud, and a wall of ISO timestamps
produces a wall of ISO timestamps read aloud.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import gauth
from gauth import NotConnected  # noqa: F401  (re-exported)


def connected() -> bool:
    return gauth.has(gauth.CAL_SCOPES)


# The model is told the user's timezone in the prompt, but tool results have
# to be unambiguous on their own — an event at "3 o'clock" with no zone is a
# bug waiting for the first time you travel.
def _tz():
    try:
        return dt.datetime.now().astimezone().tzinfo or ZoneInfo("UTC")
    except Exception:
        return ZoneInfo("UTC")


def _service():
    return gauth.service("calendar", "v3", gauth.CAL_SCOPES)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _parse(when: str) -> dt.datetime:
    """Accept what the model actually emits, not just textbook ISO 8601."""
    s = (when or "").strip().replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        # date only
        d = dt.datetime.combine(dt.date.fromisoformat(s), dt.time(9, 0))
    if d.tzinfo is None:
        d = d.replace(tzinfo=_tz())
    return d


def _speakable(ev) -> str:
    start = ev["start"].get("dateTime") or ev["start"].get("date")
    end = ev["end"].get("dateTime") or ev["end"].get("date")
    title = ev.get("summary", "(untitled)")
    where = ev.get("location", "")

    if "T" not in start:                      # all-day
        line = f"{start}, all day: {title}"
    else:
        s, e = _parse(start), _parse(end)
        same_day = s.date() == e.date()
        line = (
            f"{s.strftime('%a %d %b')} "
            f"{s.strftime('%H:%M')}–{e.strftime('%H:%M' if same_day else '%a %H:%M')}"
            f": {title}"
        )
    if where:
        line += f" (at {where})"
    return line + f"  [id:{ev['id']}]"


def upcoming_events(within_minutes: int = 15) -> list:
    """Timed events that START within the next `within_minutes` — structured, for
    the proactive meeting-nudge poller (not a spoken string). Skips all-day
    events. Returns [{id, title, minutes, start}]."""
    svc = _service()
    now = dt.datetime.now(_tz())
    end = now + dt.timedelta(minutes=max(1, min(int(within_minutes or 15), 180)))
    items = svc.events().list(
        calendarId="primary", timeMin=now.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=20,
    ).execute().get("items", [])
    out = []
    for e in items:
        start = e.get("start", {}).get("dateTime")   # timed only (all-day has 'date')
        if not start:
            continue
        st = _parse(start)
        mins = round((st - now).total_seconds() / 60)
        if mins < 0:
            continue
        out.append({"id": e.get("id", ""), "title": e.get("summary", "(untitled)"),
                    "minutes": mins, "start": start})
    return out


def agenda(hours: int = 24) -> list:
    """What is left of the day, for the panel on the HUD.

    Not upcoming_events with a bigger number. That one exists to nudge you
    before a meeting, so it skips all-day entries and anything already started
    — both of which are exactly what an agenda must show. "Leave for the
    airport" is on your day whether or not it has a clock on it, and a thing
    that started ten minutes ago is the most relevant row on the panel.

    Returns [{id, title, at, all_day, started, minutes}], soonest first.
    """
    svc = _service()
    now = dt.datetime.now(_tz())
    # From the start of TODAY, not from now, so something running since nine is
    # still on the list at ten. Trimmed below rather than by the query, because
    # only the query knows which entries are all-day.
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + dt.timedelta(hours=max(1, min(int(hours or 24), 72)))
    items = svc.events().list(
        calendarId="primary", timeMin=day0.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=25,
    ).execute().get("items", [])

    out = []
    for e in items:
        st = e.get("start", {}) or {}
        timed = st.get("dateTime")
        if timed:
            when = _parse(timed)
            mins = round((when - now).total_seconds() / 60)
            fin = (e.get("end", {}) or {}).get("dateTime")
            # Dropped only once it is genuinely OVER, not once it has begun.
            if fin and _parse(fin) < now:
                continue
            out.append({"id": e.get("id", ""), "title": e.get("summary", "(untitled)"),
                        "at": when.strftime("%H:%M"), "all_day": False,
                        "started": mins < 0, "minutes": mins})
        elif st.get("date"):
            # All-day. Only today's — tomorrow's would sit at the top of the
            # panel all afternoon claiming to be next.
            if st["date"] != now.strftime("%Y-%m-%d"):
                continue
            out.append({"id": e.get("id", ""), "title": e.get("summary", "(untitled)"),
                        "at": "all day", "all_day": True,
                        "started": False, "minutes": -1})
    # All-day first, then by clock. Sorting purely by minutes would bury an
    # all-day entry under every timed one, or float it above a meeting starting
    # in five, depending which sentinel you picked.
    out.sort(key=lambda r: (0 if r["all_day"] else 1, r["minutes"]))
    return out[:8]


# --------------------------------------------------------------------------
# the tools themselves
# --------------------------------------------------------------------------

def list_events(days_ahead: int = 1, query: str = "") -> str:
    svc = _service()
    now = dt.datetime.now(_tz())
    end = now + dt.timedelta(days=max(1, min(int(days_ahead or 1), 60)))

    params = dict(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=40,
    )
    if query:
        params["q"] = query

    items = svc.events().list(**params).execute().get("items", [])
    if not items:
        span = "today" if days_ahead <= 1 else f"the next {days_ahead} days"
        return f"Nothing in the calendar for {span}."

    header = f"{len(items)} event(s). Current local time is {now.strftime('%a %d %b %H:%M')}."
    return header + "\n" + "\n".join(_speakable(e) for e in items)


def create_event(title: str, start: str, duration_minutes: int = 60,
                 location: str = "", description: str = "") -> str:
    svc = _service()
    s = _parse(start)
    e = s + dt.timedelta(minutes=max(5, min(int(duration_minutes or 60), 1440)))

    body = {
        "summary": title,
        "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": e.isoformat()},
    }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    ev = svc.events().insert(calendarId="primary", body=body).execute()
    return f"Created: {_speakable(ev)}"


def move_event(event_id: str, new_start: str, duration_minutes: int = 0) -> str:
    svc = _service()
    ev = svc.events().get(calendarId="primary", eventId=event_id).execute()

    old_s = _parse(ev["start"].get("dateTime") or ev["start"]["date"])
    old_e = _parse(ev["end"].get("dateTime") or ev["end"]["date"])
    length = dt.timedelta(minutes=int(duration_minutes)) if duration_minutes else (old_e - old_s)

    s = _parse(new_start)
    ev["start"] = {"dateTime": s.isoformat()}
    ev["end"] = {"dateTime": (s + length).isoformat()}

    out = svc.events().update(calendarId="primary", eventId=event_id, body=ev).execute()
    return f"Moved: {_speakable(out)}"


def cancel_event(event_id: str) -> str:
    svc = _service()
    ev = svc.events().get(calendarId="primary", eventId=event_id).execute()
    title = ev.get("summary", "(untitled)")
    svc.events().delete(calendarId="primary", eventId=event_id).execute()
    return f"Deleted the event titled: {title}"


# --------------------------------------------------------------------------
# wire format for the Messages API
# --------------------------------------------------------------------------

TOOLS = [
    {
        "name": "list_events",
        "description": (
            "Read the user's Google Calendar. Call this whenever they ask what "
            "is on, whether they are free, what is next, or about any specific "
            "appointment. Also call it before moving or cancelling anything, to "
            "get the event's id. Returns each event with an [id:...] you can "
            "pass to move_event or cancel_event — never read an id aloud."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days forward to look. 1 is the rest of today. Use 7 for 'this week'.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional free-text filter, e.g. a person's name or 'dentist'.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_event",
        "description": (
            "Put a new event in the user's calendar. Call this when they ask to "
            "book, schedule, add, or put something in. Resolve relative dates "
            "yourself from the current date given in your context — never ask "
            "the user for an ISO timestamp."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short event name, e.g. 'Lunch with Sam'."},
                "start": {
                    "type": "string",
                    "description": "Local start time as ISO 8601, e.g. 2026-08-04T13:00. No timezone suffix needed.",
                },
                "duration_minutes": {"type": "integer", "description": "Length in minutes. Default 60."},
                "location": {"type": "string", "description": "Optional place."},
                "description": {"type": "string", "description": "Optional notes."},
            },
            "required": ["title", "start"],
        },
    },
    {
        "name": "move_event",
        "description": (
            "Reschedule an existing event. Call list_events first to find its id. "
            "Keeps the original length unless you pass a new one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The id from list_events."},
                "new_start": {"type": "string", "description": "New local start time, ISO 8601."},
                "duration_minutes": {"type": "integer", "description": "Optional new length in minutes."},
            },
            "required": ["event_id", "new_start"],
        },
    },
    {
        "name": "cancel_event",
        "description": (
            "Delete an event. Call list_events first to get the id, and confirm "
            "with the user in words before calling this — deletion cannot be undone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "The id from list_events."},
            },
            "required": ["event_id"],
        },
    },
]

_DISPATCH = {
    "list_events": list_events,
    "create_event": create_event,
    "move_event": move_event,
    "cancel_event": cancel_event,
}


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    """Execute one tool call. Returns (result_text, is_error).

    Never raises: a tool that throws should tell the model what went wrong so
    it can recover in words, not take the whole reply down with it.
    """
    fn = _DISPATCH.get(name)
    if not fn:
        return f"No such tool: {name}", True
    try:
        return str(fn(**(args or {}))), False
    except NotConnected as e:
        return str(e), True
    except TypeError as e:
        return f"Wrong arguments for {name}: {e}", True
    except Exception as e:
        return f"{type(e).__name__}: {e}", True


if __name__ == "__main__":
    # Kept so the documented command still works; the real thing lives in gauth.
    from gauth import connect
    connect()
