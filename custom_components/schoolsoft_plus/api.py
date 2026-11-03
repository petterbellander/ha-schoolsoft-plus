"""SchoolSoft data access for Home Assistant.

Ports the data-fetching parts of schoolsoft-mcp's ``server.py`` tools
(list_children, get_schedule, get_lunch_menu, assignments, get_exam_schedule,
get_open_work, get_assessments, get_unreported_absence, get_news,
get_fritids_times) onto the vendored client and parsers, without MCP.

A parent session has exactly one selected child, so every per-child fetch
holds ``self.lock`` from selecting the child until the last request.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from .const import BODY_WINDOW_FUTURE, BODY_WINDOW_PAST, MAX_BODY_CHARS
from .vendor.client import (
    SchoolSoftAccessError,
    SchoolSoftAuthError,
    SchoolSoftClient,
    SchoolSoftConnectionError,
)
from .vendor.models import (
    WEEKDAYS,
    AllDayEvent,
    Child,
    ExamEntry,
    Lesson,
    NewsItem,
    SubjectAssessment,
    UnreportedAbsenceList,
)
from .vendor.parsers import assessment as asm
from .vendor.parsers import fritids as fr
from .vendor.parsers import subjectrooms as sr
from .vendor.parsers.attendance import (
    UNREPORTED_ABSENCE_PATHS,
    parse_unreported_absence,
)
from .vendor.parsers.children import parse_parent_header
from .vendor.parsers.lunch import (
    LUNCH_PATH,
    LUNCH_REST_PATH_TEMPLATE,
    parse_lunch,
    parse_lunch_json,
)
from .vendor.parsers.news import NEWS_PATHS, parse_news
from .vendor.parsers.schedule import (
    SCHEDULE_PATHS,
    SCHEDULE_REST_EVENTS_PATH_TEMPLATE,
    SCHEDULE_REST_LESSONS_PATH_TEMPLATE,
    parse_schedule,
    parse_schedule_json,
)

_LOGGER = logging.getLogger(__name__)

# Errors that make one section unavailable without failing the whole update.
SECTION_ERRORS = (
    SchoolSoftConnectionError,
    SchoolSoftAccessError,
    httpx.HTTPError,
    ValueError,
)

__all__ = [
    "ChildData",
    "SchoolSoftAccessError",
    "SchoolSoftAuthError",
    "SchoolSoftConnectionError",
    "SchoolSoftHub",
]


@dataclass(slots=True)
class DatedLesson:
    """A lesson placed on its calendar date."""

    date: dt.date
    lesson: Lesson


@dataclass(slots=True)
class Assignment:
    """One assignment (läxa, prov, inlämning) with real dates."""

    assignment_id: int | None
    title: str
    subject: str
    kind: str
    teacher: str
    start: dt.date | None
    end: dt.date | None
    status: str
    submission_status: str
    result_status: str
    read: bool
    body: str = ""


@dataclass(slots=True)
class ChildData:
    """Everything fetched for one child."""

    child: Child
    lessons: list[DatedLesson] = field(default_factory=list)
    all_day_events: list[AllDayEvent] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    exams: list[ExamEntry] = field(default_factory=list)
    open_work: list[dict[str, Any]] = field(default_factory=list)
    lunch: dict[dt.date, str] = field(default_factory=dict)
    assessments: list[SubjectAssessment] = field(default_factory=list)
    absence: UnreportedAbsenceList | None = None
    news: list[NewsItem] = field(default_factory=list)
    fritids: dict[str, Any] | None = None
    errors: dict[str, str] = field(default_factory=dict)
    slow_updated: dt.datetime | None = None


class SchoolSoftHub:
    """Fetches SchoolSoft data for every child on a parent account."""

    def __init__(self, client: SchoolSoftClient, school: str, usertype: int) -> None:
        self.client = client
        self.school = school
        self.usertype = usertype
        self.lock = asyncio.Lock()
        # assignment_id -> (fingerprint, body)
        self._bodies: dict[int, tuple[tuple[Any, ...], str]] = {}

    async def async_close(self) -> None:
        await self.client.close()

    async def async_login(self) -> None:
        async with self.lock:
            await self.client.login()

    async def async_children(self) -> list[Child]:
        async with self.lock:
            payload = await self.client.fetch_json(SchoolSoftClient.PARENT_HEADER_PATH)
        result = parse_parent_header(payload, school=self.school)
        if not result.children and result.note:
            _LOGGER.warning("SchoolSoft child list: %s", result.note)
        return result.children

    async def _select(self, child: Child, *, only_child: bool) -> None:
        """Point the session at ``child``. Lock must be held."""
        current = self.client.active_child
        if current is not None and current[0] == child.student_id:
            return
        if child.org_id is None:
            if only_child:
                # One child: SchoolSoft's default selection is that child.
                return
            raise ValueError(
                f"No orgId for child {child.student_id}; cannot select it safely"
            )
        await self.client.select_child(child.student_id, child.org_id)

    async def async_fetch_child(
        self,
        child: Child,
        previous: ChildData | None,
        *,
        only_child: bool,
        slow: bool,
        today: dt.date,
    ) -> ChildData:
        """Fetch one child's data. Sections that fail keep their previous value."""
        data = ChildData(child=child)
        if previous is not None:
            # Start from the last good data so a failing section degrades
            # to "stale" rather than "empty".
            data.lessons = previous.lessons
            data.all_day_events = previous.all_day_events
            data.assignments = previous.assignments
            data.exams = previous.exams
            data.open_work = previous.open_work
            data.lunch = previous.lunch
            data.assessments = previous.assessments
            data.absence = previous.absence
            data.news = previous.news
            data.fritids = previous.fritids
            data.slow_updated = previous.slow_updated

        async with self.lock:
            await self._select(child, only_child=only_child)

            async def section(name: str, coro: Any) -> Any:
                try:
                    return await coro
                except SchoolSoftAuthError as err:
                    if isinstance(err, SchoolSoftAccessError):
                        data.errors[name] = str(err)
                        _LOGGER.warning("SchoolSoft %s for %s: %s", name, child.name, err)
                        return None
                    raise
                except SECTION_ERRORS as err:
                    data.errors[name] = str(err)
                    _LOGGER.warning("SchoolSoft %s for %s failed: %s", name, child.name, err)
                    return None

            iso = today.isocalendar()
            next_week_day = today + dt.timedelta(days=7)
            iso_next = next_week_day.isocalendar()

            lessons: list[DatedLesson] = []
            all_day: list[AllDayEvent] = []
            ok = True
            for year, week in ((iso.year, iso.week), (iso_next.year, iso_next.week)):
                res = await section("schema", self._schedule(year, week))
                if res is None:
                    ok = False
                    break
                lessons.extend(res[0])
                all_day.extend(res[1])
            if ok:
                data.lessons = lessons
                data.all_day_events = all_day

            res = await section("uppgifter", self._assignments(today))
            if res is not None:
                data.assignments = res

            res = await section("provschema", self._exams())
            if res is not None:
                data.exams = res

            res = await section("öppna uppgifter", self._open_work())
            if res is not None:
                data.open_work = res

            if slow:
                lunch: dict[dt.date, str] = {}
                ok = True
                for year, week in ((iso.year, iso.week), (iso_next.year, iso_next.week)):
                    res = await section("lunch", self._lunch(year, week))
                    if res is None:
                        ok = False
                        break
                    lunch.update(res)
                if ok:
                    data.lunch = lunch

                res = await section("bedömningar", self._assessments())
                if res is not None:
                    data.assessments = res

                res = await section("frånvaro", self._absence())
                if res is not None:
                    data.absence = res

                res = await section("nyheter", self._news())
                if res is not None:
                    data.news = res

                res = await section("fritids", self._fritids(child))
                if res is not None:
                    data.fritids = res

                data.slow_updated = dt.datetime.now(dt.UTC)

        return data

    # -- sections (lock held) -------------------------------------------------

    async def _schedule(
        self, year: int, week: int
    ) -> tuple[list[DatedLesson], list[AllDayEvent]]:
        monday, _ = sr.week_bounds(year, week)
        try:
            lessons_payload = await self.client.fetch_json(
                SCHEDULE_REST_LESSONS_PATH_TEMPLATE.format(week=week)
            )
        except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError) as err:
            _LOGGER.debug("Schedule REST failed (%s), falling back to JSP", err)
            html = await self._fetch_first(SCHEDULE_PATHS, {"requestid": str(week)})
            parsed = parse_schedule(
                html, school=self.school, requested_week=week, requested_year=year
            )
        else:
            try:
                events_payload = await self.client.fetch_json(
                    SCHEDULE_REST_EVENTS_PATH_TEMPLATE.format(week=week, year=year)
                )
            except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError) as err:
                _LOGGER.debug("No all-day events for w%d/%d: %s", week, year, err)
                events_payload = None
            parsed = parse_schedule_json(
                lessons_payload, events_payload, school=self.school, week=week, year=year
            )

        dated: list[DatedLesson] = []
        for lesson in parsed.lessons:
            if lesson.day not in WEEKDAYS:
                continue
            dated.append(
                DatedLesson(
                    date=monday + dt.timedelta(days=WEEKDAYS.index(lesson.day)),
                    lesson=lesson,
                )
            )
        return dated, list(parsed.all_day_events)

    async def _assignments(self, today: dt.date) -> list[Assignment]:
        rows = await self.client.fetch_json(sr.path(sr.ASSIGNMENT_ROWS, self.usertype))
        out: list[Assignment] = []
        first = today - BODY_WINDOW_PAST
        last = today + BODY_WINDOW_FUTURE
        for entry in rows if isinstance(rows, list) else []:
            if not isinstance(entry, dict):
                continue
            row = sr.parse_assignment_row(entry)
            start = sr.parse_iso_date(row["start_date"])
            end = sr.parse_iso_date(row["end_date"])
            if start is None and end is None:
                continue
            item = Assignment(
                assignment_id=row["assignment_id"],
                title=row["title"],
                subject=row["subject"],
                kind=row["kind"],
                teacher=row["teacher"],
                start=start,
                end=end,
                status=row["status"],
                submission_status=row["submission_status"],
                result_status=row["result_status"],
                read=row["read"],
            )
            if item.assignment_id is not None and sr.overlaps(start, end, first, last):
                item.body = await self._assignment_body(item.assignment_id, row)
            out.append(item)
        out.sort(key=lambda a: (a.end or a.start or dt.date.max, a.subject))
        return out

    async def _assignment_body(self, assignment_id: int, row: dict[str, Any]) -> str:
        fingerprint = (
            row["title"],
            row["start_date"],
            row["end_date"],
            row["publish_date"],
            row["status"],
        )
        cached = self._bodies.get(assignment_id)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]
        try:
            payload = await self.client.fetch_json(
                sr.path(sr.ASSIGNMENT_VIEW, self.usertype, assignment_id=assignment_id)
            )
        except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError) as err:
            _LOGGER.debug("Assignment %s body unavailable: %s", assignment_id, err)
            return cached[1] if cached else ""
        body = sr.parse_detail_view(payload, max_body_chars=MAX_BODY_CHARS).get("body", "")
        self._bodies[assignment_id] = (fingerprint, body)
        return body

    async def _exams(self) -> list[ExamEntry]:
        payload = await self.client.fetch_json(sr.path(sr.EXAM_SCHEDULE, self.usertype))
        return list(sr.parse_exam_schedule(payload, school=self.school).exams)

    async def _open_work(self) -> list[dict[str, Any]]:
        rows = await self.client.fetch_json(asm.path_for(asm.OPEN_WORK_ROWS, self.usertype))
        return asm.parse_open_work(rows)

    async def _lunch(self, year: int, week: int) -> dict[dt.date, str]:
        monday, _ = sr.week_bounds(year, week)
        try:
            payload = await self.client.fetch_json(LUNCH_REST_PATH_TEMPLATE.format(week=week))
            parsed = parse_lunch_json(payload, school=self.school, requested_week=week)
        except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError) as err:
            _LOGGER.debug("Lunch REST failed (%s), falling back to JSP", err)
            html = await self.client.fetch_html(LUNCH_PATH, params={"requestid": str(week)})
            parsed = parse_lunch(html, school=self.school, requested_week=week)
        out: dict[dt.date, str] = {}
        for day in parsed.days:
            if day.day in WEEKDAYS and day.meal:
                out[monday + dt.timedelta(days=WEEKDAYS.index(day.day))] = day.meal
        return out

    async def _assessments(self) -> list[SubjectAssessment]:
        rows = await self.client.fetch_json(asm.path_for(asm.ASSESSMENT_ROWS, self.usertype))
        try:
            options = await self.client.fetch_json(
                asm.path_for(asm.ASSESSMENT_OPTIONS, self.usertype)
            )
        except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError):
            options = None
        return [SubjectAssessment(**r) for r in asm.parse_assessment_rows(rows, options)]

    async def _absence(self) -> UnreportedAbsenceList:
        html = await self._fetch_first(UNREPORTED_ABSENCE_PATHS)
        return parse_unreported_absence(html, school=self.school)

    async def _news(self) -> list[NewsItem]:
        html = await self._fetch_first(NEWS_PATHS)
        return list(parse_news(html, school=self.school).items)

    async def _fritids(self, child: Child) -> dict[str, Any]:
        html = await self.client.fetch_html(
            fr.FRITIDS_PATH, params={"requestid": str(child.student_id)}
        )
        return fr.parse_fritids(html, school=self.school)

    async def _fetch_first(
        self, paths: tuple[str, ...], params: dict[str, str] | None = None
    ) -> str:
        last: Exception | None = None
        for path in paths:
            try:
                return await self.client.fetch_html(path, params=params)
            except (SchoolSoftConnectionError, SchoolSoftAccessError, httpx.HTTPError) as err:
                last = err
                _LOGGER.debug("Path %s failed: %s", path, err)
        assert last is not None
        raise last
