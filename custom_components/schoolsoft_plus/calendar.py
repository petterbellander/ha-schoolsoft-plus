"""Calendars for SchoolSoft Plus: schedule, homework, tests and lunch."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Assignment, ChildData
from .const import LUNCH_END, LUNCH_START
from .coordinator import SchoolSoftPlusConfigEntry
from .entity import SchoolSoftChildEntity, due_date, is_exam, lesson_times


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolSoftPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[CalendarEntity] = []
    for child in coordinator.children:
        entities += [
            ScheduleCalendar(coordinator, child, "schema"),
            AssignmentCalendar(coordinator, child, "uppgifter", exams_only=False),
            AssignmentCalendar(coordinator, child, "prov", exams_only=True),
            LunchCalendar(coordinator, child, "lunch"),
        ]
    async_add_entities(entities)


def _pick_current(events: list[CalendarEvent]) -> CalendarEvent | None:
    """The ongoing event, else the next one."""
    now = dt_util.now()
    upcoming: list[tuple[dt.datetime, CalendarEvent]] = []
    for ev in events:
        start = ev.start_datetime_local
        end = ev.end_datetime_local
        if end <= now:
            continue
        upcoming.append((start, ev))
    if not upcoming:
        return None
    upcoming.sort(key=lambda p: p[0])
    return upcoming[0][1]


class _ChildCalendar(SchoolSoftChildEntity, CalendarEntity):
    def _events(self, data: ChildData) -> list[CalendarEvent]:
        raise NotImplementedError

    def _all_events(self) -> list[CalendarEvent]:
        data = self.child_data
        return self._events(data) if data else []

    @property
    def event(self) -> CalendarEvent | None:
        return _pick_current(self._all_events())

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        return [
            ev
            for ev in self._all_events()
            if ev.end_datetime_local > start_date and ev.start_datetime_local < end_date
        ]


class ScheduleCalendar(_ChildCalendar):
    _attr_name = "Schema"
    _attr_icon = "mdi:calendar-clock"

    def _events(self, data: ChildData) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        for item in data.lessons:
            if item.lesson.is_break:
                continue
            times = lesson_times(item)
            if times is None:
                continue
            les = item.lesson
            details = [
                p
                for p in (
                    f"Lärare: {les.teacher}" if les.teacher else "",
                    f"Grupp: {les.teaching_group}" if les.teaching_group else "",
                    les.notes,
                    f"Närvaro: {les.attendance_status}" if les.attendance_status else "",
                )
                if p
            ]
            out.append(
                CalendarEvent(
                    start=times[0],
                    end=times[1],
                    summary=les.subject or "Lektion",
                    location=les.room or None,
                    description="\n".join(details) or None,
                    uid=f"lesson-{les.lesson_id}-{item.date}" if les.lesson_id else None,
                )
            )
        for ev in data.all_day_events:
            try:
                start = dt.date.fromisoformat(ev.start_day)
            except ValueError:
                continue
            try:
                end = dt.date.fromisoformat(ev.end_day) if ev.end_day else start
            except ValueError:
                end = start
            out.append(
                CalendarEvent(
                    start=start,
                    end=max(end, start) + dt.timedelta(days=1),
                    summary=ev.title,
                    description=ev.description or None,
                )
            )
        return out


def _assignment_event(item: Assignment) -> CalendarEvent | None:
    day = due_date(item)
    if day is None:
        return None
    label = "Prov" if is_exam(item) else (item.kind or "Uppgift")
    summary = f"{label} {item.subject}: {item.title}" if item.subject else f"{label}: {item.title}"
    details = [
        p
        for p in (
            f"Typ: {item.kind}" if item.kind else "",
            f"Lärare: {item.teacher}" if item.teacher else "",
            f"Start: {item.start}" if item.start and item.start != day else "",
            f"Inlämning: {item.submission_status}" if item.submission_status else "",
            item.body,
        )
        if p
    ]
    return CalendarEvent(
        start=day,
        end=day + dt.timedelta(days=1),
        summary=summary,
        description="\n".join(details) or None,
        uid=f"assignment-{item.assignment_id}" if item.assignment_id else None,
    )


class AssignmentCalendar(_ChildCalendar):
    def __init__(self, coordinator, child, key: str, *, exams_only: bool) -> None:
        super().__init__(coordinator, child, key)
        self._exams_only = exams_only
        self._attr_name = "Prov" if exams_only else "Läxor och uppgifter"
        self._attr_icon = "mdi:file-document-edit" if exams_only else "mdi:book-open-variant"

    def _events(self, data: ChildData) -> list[CalendarEvent]:
        out: list[CalendarEvent] = []
        for item in data.assignments:
            if self._exams_only and not is_exam(item):
                continue
            ev = _assignment_event(item)
            if ev is not None:
                out.append(ev)
        return out


class LunchCalendar(_ChildCalendar):
    _attr_name = "Lunch"
    _attr_icon = "mdi:silverware-fork-knife"

    def _events(self, data: ChildData) -> list[CalendarEvent]:
        tz = dt_util.get_default_time_zone()
        out: list[CalendarEvent] = []
        for day, meal in sorted(data.lunch.items()):
            main, _, veg = meal.partition(" | Veg: ")
            out.append(
                CalendarEvent(
                    start=dt.datetime.combine(day, dt.time(*LUNCH_START), tz),
                    end=dt.datetime.combine(day, dt.time(*LUNCH_END), tz),
                    summary=main.strip() or meal,
                    description=f"Vegetariskt: {veg.strip()}" if veg else None,
                )
            )
        return out
