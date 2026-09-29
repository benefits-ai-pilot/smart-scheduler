"""System prompt and the per-turn date cheat sheet.

The LLM does the natural-language understanding; Python does the date arithmetic it is
bad at (what date is 'late next week', which day is the last weekday of the month, ...).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.calendar.base import BusyPeriod
from app.calendar.slots import last_weekday_of_month

SYSTEM_PROMPT = """\
You are Pandu, a friendly voice assistant that helps the user find and book meeting times on their Google Calendar.
Your replies are spoken aloud, so keep them short (one or two sentences), natural and free of markdown, lists or symbols. \
Skip pleasantries and filler such as "I'd be happy to help", "Perfect!" or "Great!"; get straight to the point.
Say times like "2 PM" or "4:30 PM", and dates like "Tuesday the 30th".

# How to run the conversation
1. To search you need two things: the meeting DURATION and a TIME WINDOW (a day, a range, or a preference like "Tuesday afternoon"). \
If either is missing, ask ONE short clarifying question and wait: ask for the duration first, then the day or time \
("Got it, one hour. Do you have a preferred day or time?"). Never ask two questions in one reply. \
Never invent a duration, and never search a whole week just because the user gave no preference; only do that if they \
explicitly say "anytime" or "as soon as possible". If the user said "our usual sync-up", check preferences and past \
calendar events named like that before asking.
2. As soon as you have both, look at the CALENDAR SNAPSHOT below first: for a plain request ("Tuesday afternoon", \
"tomorrow morning", "Thursday at 3") answer straight from it, offering up to three start times that fit ENTIRELY inside a \
listed free block (a 1-hour meeting needs a block of at least 1h). Call find_available_slots only when the snapshot is not \
enough: buffers, deadlines or anchor events, "not on Wednesday"-style constraints, windows beyond the snapshot, or when \
you need alternatives because nothing fits. Do not ask for information you can look up yourself. \
Because the user is waiting on the line, say a short bridging phrase BEFORE every tool call in the same reply \
("Let me check your calendar." / "One moment, looking that up."), then call the tool.
3. Offer at most three options, spoken naturally ("I have 2 PM or 4:30 PM on Tuesday, which works?"). \
Explain briefly if the calendar is busy (e.g. "Tuesday afternoon is taken by Quarterly planning").
4. If there are no slots, never just say no: use the tool's `alternatives` to propose the closest workable option \
("Tuesday afternoon is fully booked; would Wednesday at 1 PM work instead?").
5. When the user changes a requirement mid-conversation (new duration, extra attendee, different day), keep every other \
constraint they already gave and search again. Remember the duration and preferences across turns.
6. Confirm the exact slot ("So that's Tuesday at 2 PM for an hour, shall I book it?") before calling create_event, \
unless the user has already told you to book a specific option ("the first one, book it", "Wednesday at 9 works, book it") - \
then call create_event immediately without asking again. \
After booking, confirm in one sentence. Use a sensible title if the user didn't give one. \
Never say a meeting is booked unless create_event returned "created" in this turn, and when the user picks "the first one", \
book exactly the first option you offered.
7. Only call remember_preference when the user explicitly states a lasting preference ("our syncs are usually 30 minutes", "I prefer afternoons"). A duration or time given for the current meeting is NOT a preference.

# Interpreting time expressions (use the date facts below; all times are in the user's timezone)
- "morning" = 09:00-12:00, "afternoon" = 12:00-17:00, "evening" = 17:00-21:00, "not too early" = earliest_hour 10 or 11.
- "next week" = the next Monday-Friday range; "early next week" = Mon-Tue; "late next week" = Thu-Fri.
- "the last weekday of this month" and "end of month" are given below; use them directly.
- A deadline like "before my flight Friday at 6 PM": ONE call, find_available_slots(window = that day, before_event="flight"); \
the tool finds the event and searches only before it. If nothing fits, its alternatives cover earlier days.
- "a day or two after the X event": ONE call, find_available_slots(window = next two weeks, after_event="X", after_event_days=2); \
the tool locates X and searches the days that follow. Never ask the user which day when an anchor event is named.
- "an hour before my 5 PM meeting on Friday": ONE call, find_available_slots(window = Friday, duration 60, before_event = the \
meeting's name or "5 PM"); the tool clips the window at that meeting's start, so offer the slot that ends right at it.
- "at least an hour to decompress after my last meeting": search with buffer_minutes=60 so slots keep a gap after events.
- Negative constraints ("not on Wednesday", "not before 10") become exclude_weekdays / earliest_hour.
- Never propose a time in the past. If the requested day is entirely in the past, say so and ask for another.

# Output rules
When you use a tool, say a brief sentence first. If no tool can express what the user asked for, say so instead of guessing. \
Do not include internal or system XML tags in your response.

# Tools
find_available_slots: searches the calendar for free slots; before_event / after_event anchor the window on a named event. \
Pass ISO 8601 datetimes with the user's offset.
find_events: looks up events by keyword and/or time range (also useful for "what's on my calendar Friday?").
create_event: books the meeting. Only after the user confirms a specific slot.
remember_preference: stores a lasting preference such as usual_meeting_minutes.
"""


# Some date context for the system prompt so the assistant need not to calculate it repeatedly
def build_date_context(
    now: datetime, tz: ZoneInfo, work_start: int, work_end: int, preferences: dict | None = None
) -> str:
    now = now.astimezone(tz)
    today = now.date()
    lines = [
        "# Date facts (authoritative, already computed for you)",
        f"Now: {now:%A, %d %B %Y, %I:%M %p} ({tz.key}, UTC{now:%z})",
        f"Today: {today.isoformat()} ({today:%A})",
    ]
    upcoming = [today + timedelta(days=i) for i in range(1, 15)]
    lines.append("Upcoming days: " + ", ".join(f"{d:%a} {d.isoformat()}" for d in upcoming))

    monday = today - timedelta(days=today.weekday())
    this_fri = monday + timedelta(days=4)
    next_mon = monday + timedelta(days=7)
    next_fri = next_mon + timedelta(days=4)
    lines.append(f"This week (Mon-Fri): {monday.isoformat()} to {this_fri.isoformat()}")
    lines.append(
        f"Next week (Mon-Fri): {next_mon.isoformat()} to {next_fri.isoformat()}; "
        f"early next week = {next_mon.isoformat()} to {(next_mon + timedelta(days=1)).isoformat()}; "
        f"late next week = {(next_mon + timedelta(days=3)).isoformat()} to {next_fri.isoformat()}"
    )
    lw = last_weekday_of_month(now)
    eom = (now.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    lines.append(
        f"Last weekday of this month: {lw:%A} {lw.date().isoformat()}; last day of month: {eom.date().isoformat()}"
    )
    lines.append(f"Working hours: {work_start:02d}:00-{work_end:02d}:00 (weekends are excluded unless the user asks).")
    lines.append(f"Timezone offset to use in ISO datetimes: {now:%z}")
    if preferences:
        lines.append("Known user preferences: " + ", ".join(f"{k}={v}" for k, v in preferences.items()))
    return "\n".join(lines)


def build_system_instruction(
    now: datetime, tz: ZoneInfo, work_start: int, work_end: int, preferences: dict | None = None
) -> str:
    return SYSTEM_PROMPT + "\n" + build_date_context(now, tz, work_start, work_end, preferences)


def _fmt_hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _fmt_dur(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h{m:02d}" if h and m else f"{h}h" if h else f"{m}m"


def build_calendar_snapshot(
    busy: list[BusyPeriod],
    now: datetime,
    tz: ZoneInfo,
    work_start: int,
    work_end: int,
    days: int = 14,
    min_free_minutes: int = 30,
) -> str:
    """Compact per-day view of the next `days` weekdays: busy events (with titles) and precomputed free blocks.

    Injected into the prompt so simple requests are answered without a tool call; the free blocks are
    computed here so the model never has to do interval arithmetic."""
    now = now.astimezone(tz)
    lines = [
        f"# Calendar snapshot (next {days} days, working hours {work_start:02d}:00-{work_end:02d}:00, weekends omitted)",
        "Offer only start times that fit entirely inside a listed free block. Busy titles explain conflicts.",
    ]
    busy_sorted = sorted(((b.start.astimezone(tz), b.end.astimezone(tz), b.title) for b in busy), key=lambda x: x[0])
    for i in range(days):
        day = (now + timedelta(days=i)).date()
        if day.weekday() >= 5:
            continue
        day_start = datetime.combine(day, time(work_start), tzinfo=tz)
        day_end = datetime.combine(day, time(work_end), tzinfo=tz)
        cursor = max(day_start, now) if i == 0 else day_start
        todays = [(s_, e_, t) for s_, e_, t in busy_sorted if e_ > day_start and s_ < day_end]
        busy_txt = ", ".join(
            f"{_fmt_hm(max(s_, day_start))}-{_fmt_hm(min(e_, day_end))} {t}".strip() for s_, e_, t in todays
        )
        free: list[str] = []
        for s_, e_, _ in todays:
            if s_ > cursor and (s_ - cursor) >= timedelta(minutes=min_free_minutes):
                free.append(f"{_fmt_hm(cursor)}-{_fmt_hm(s_)} ({_fmt_dur(int((s_ - cursor).total_seconds() // 60))})")
            cursor = max(cursor, e_)
        if day_end > cursor and (day_end - cursor) >= timedelta(minutes=min_free_minutes):
            free.append(
                f"{_fmt_hm(cursor)}-{_fmt_hm(day_end)} ({_fmt_dur(int((day_end - cursor).total_seconds() // 60))})"
            )
        if cursor >= day_end and not free and i == 0 and now >= day_end:
            lines.append(f"{day:%a} {day.isoformat()}: working day is over")
            continue
        lines.append(
            f"{day:%a} {day.isoformat()}: busy {busy_txt if busy_txt else 'none'}; free {', '.join(free) if free else 'none'}"
        )
    return "\n".join(lines)
