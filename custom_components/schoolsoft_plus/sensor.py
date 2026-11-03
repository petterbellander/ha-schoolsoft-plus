"""Sensors for SchoolSoft Plus."""

from __future__ import annotations

import datetime as dt
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import ChildData
from .coordinator import SchoolSoftPlusConfigEntry
from .entity import SchoolSoftChildEntity, SchoolSoftSchoolEntity, due_date, is_exam, lesson_times
from .menu import WEEKDAY_LABELS

MAX_LIST = 15


def _short(text: str, limit: int = 250) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolSoftPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [MenuSensor(coordinator)]
    for child in coordinator.children:
        entities += [
            SchoolDaySensor(coordinator, child, "skolstart_idag", 0, "start"),
            SchoolDaySensor(coordinator, child, "skolslut_idag", 0, "end"),
            SchoolDaySensor(coordinator, child, "skolstart_imorgon", 1, "start"),
            NextExamSensor(coordinator, child, "nasta_prov"),
            OpenWorkSensor(coordinator, child, "oppna_uppgifter"),
            AbsenceSensor(coordinator, child, "ej_anmald_franvaro"),
            WarningSensor(coordinator, child, "amnen_med_varning"),
            LunchTodaySensor(coordinator, child, "lunch_idag"),
            NewsSensor(coordinator, child, "senaste_nyhet"),
        ]
        data = (coordinator.data or {}).get(child.student_id)
        if data and data.fritids and data.fritids.get("has_fritids"):
            entities.append(FritidsSensor(coordinator, child, "fritids_hamtning_idag"))
    async_add_entities(entities)


class _ChildSensor(SchoolSoftChildEntity, SensorEntity):
    _unrecorded_attributes = frozenset({"items", "upcoming", "aviserade", "menu", "body"})

    @property
    def native_value(self) -> Any:
        data = self.child_data
        return self._value(data) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.child_data
        return self._attrs(data) if data else None

    def _value(self, data: ChildData) -> Any:
        raise NotImplementedError

    def _attrs(self, data: ChildData) -> dict[str, Any] | None:
        return None


class SchoolDaySensor(_ChildSensor):
    """First lesson start or last lesson end, today or tomorrow."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, child, key: str, offset: int, which: str) -> None:
        super().__init__(coordinator, child, key)
        self._offset = offset
        self._which = which
        when = "idag" if offset == 0 else "imorgon"
        self._attr_name = f"Skol{'start' if which == 'start' else 'slut'} {when}"
        self._attr_icon = "mdi:school" if which == "start" else "mdi:home-import-outline"

    def _lessons(self, data: ChildData) -> list[tuple[dt.datetime, dt.datetime, str]]:
        day = dt_util.now().date() + dt.timedelta(days=self._offset)
        out = []
        for item in data.lessons:
            if item.date != day or item.lesson.is_break:
                continue
            times = lesson_times(item)
            if times:
                out.append((times[0], times[1], item.lesson.subject))
        return sorted(out)

    def _value(self, data: ChildData) -> dt.datetime | None:
        lessons = self._lessons(data)
        if not lessons:
            return None
        return lessons[0][0] if self._which == "start" else max(e for _, e, _ in lessons)

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        lessons = self._lessons(data)
        if self._which == "start":
            return {
                "forsta_lektion": lessons[0][2] if lessons else None,
                "lektioner": [f"{s:%H:%M}-{e:%H:%M} {n}" for s, e, n in lessons],
            }
        return {"sista_lektion": lessons[-1][2] if lessons else None}


class NextExamSensor(_ChildSensor):
    _attr_name = "Nästa prov"
    _attr_icon = "mdi:file-document-edit"
    _attr_device_class = SensorDeviceClass.DATE

    def _upcoming(self, data: ChildData):
        today = dt_util.now().date()
        items = [a for a in data.assignments if is_exam(a) and (due_date(a) or dt.date.min) >= today]
        return sorted(items, key=lambda a: due_date(a) or dt.date.max)

    def _value(self, data: ChildData) -> dt.date | None:
        items = self._upcoming(data)
        return due_date(items[0]) if items else None

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        items = self._upcoming(data)
        nxt = items[0] if items else None
        return {
            "amne": nxt.subject if nxt else None,
            "titel": nxt.title if nxt else None,
            "typ": nxt.kind if nxt else None,
            "larare": nxt.teacher if nxt else None,
            "dagar_kvar": (due_date(nxt) - dt_util.now().date()).days if nxt else None,
            "antal_kommande": len(items),
            "upcoming": [
                {"datum": str(due_date(a)), "amne": a.subject, "titel": a.title, "typ": a.kind}
                for a in items[:MAX_LIST]
            ],
            # Announced exams: the dates are when the announcement is shown,
            # not when the exam is written.
            "aviserade": [
                {"titel": e.title, "typ": e.kind, "visas_fran": e.start, "visas_till": e.end}
                for e in data.exams
                if (e.end or e.start or "")[:10] >= str(dt_util.now().date())
            ][:MAX_LIST],
        }


class OpenWorkSensor(_ChildSensor):
    _attr_name = "Öppna uppgifter"
    _attr_icon = "mdi:clipboard-text-clock"
    _attr_native_unit_of_measurement = "st"

    def _items(self, data: ChildData) -> list[dict[str, Any]]:
        return [
            r
            for r in data.open_work
            if r.get("entity_type") == "ASSIGNMENT" and r.get("status") == "ONGOING"
        ]

    def _value(self, data: ChildData) -> int:
        return len(self._items(data))

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        items = self._items(data)
        return {
            "nasta_deadline": items[0].get("end_date") if items else None,
            "items": [
                {
                    "amne": r.get("subject"),
                    "titel": r.get("title"),
                    "typ": r.get("kind"),
                    "deadline": r.get("end_date"),
                    "inlamning": r.get("submission_status"),
                }
                for r in items[:MAX_LIST]
            ],
        }


class AbsenceSensor(_ChildSensor):
    _attr_name = "Ej anmäld frånvaro"
    _attr_icon = "mdi:account-question"
    _attr_native_unit_of_measurement = "st"

    def _value(self, data: ChildData) -> int | None:
        return len(data.absence.events) if data.absence else None

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        events = data.absence.events if data.absence else []
        return {
            "items": [
                {"vecka": e.week, "dag": e.day, "lektion": e.lesson, "meddelande": _short(e.message)}
                for e in events[:MAX_LIST]
            ]
        }


class WarningSensor(_ChildSensor):
    _attr_name = "Ämnen med varning"
    _attr_icon = "mdi:alert-decagram"
    _attr_native_unit_of_measurement = "st"

    def _value(self, data: ChildData) -> int:
        return sum(1 for s in data.assessments if s.subject_warning)

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        published = [s for s in data.assessments if s.published]
        return {
            "amnen": [s.subject for s in data.assessments if s.subject_warning],
            "olasta_bedomningar": sum(1 for s in published if not s.read),
            "publicerade_bedomningar": len(published),
        }


class LunchTodaySensor(_ChildSensor):
    """Today's main dish; vegetarian option and description as attributes."""

    _attr_name = "Lunch idag"
    _attr_icon = "mdi:silverware-fork-knife"

    def _day(self, data: ChildData, day: dt.date) -> dict[str, str] | None:
        item = self.coordinator.menu_day(day)
        if item is not None:
            return {"ratt": item.main, "vegetariskt": item.vegetarian, "beskrivning": item.description}
        meal = data.lunch.get(day)
        if not meal:
            return None
        main, _, veg = meal.partition(" | Veg: ")
        return {"ratt": main.strip(), "vegetariskt": veg.strip(), "beskrivning": ""}

    def _value(self, data: ChildData) -> str | None:
        day = self._day(data, dt_util.now().date())
        return _short(day["ratt"], 255) if day else None

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        today = dt_util.now().date()
        day = self._day(data, today) or {}
        tomorrow = self._day(data, today + dt.timedelta(days=1))
        return {
            "vegetariskt": day.get("vegetariskt") or None,
            "beskrivning": day.get("beskrivning") or None,
            "imorgon": tomorrow["ratt"] if tomorrow else None,
            "imorgon_vegetariskt": (tomorrow or {}).get("vegetariskt") or None,
            "menu": {str(d): m for d, m in sorted(data.lunch.items()) if d >= today},
        }


class NewsSensor(_ChildSensor):
    _attr_name = "Senaste nyhet"
    _attr_icon = "mdi:newspaper-variant-outline"

    def _value(self, data: ChildData) -> str | None:
        return _short(data.news[0].title, 255) if data.news else None

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        first = data.news[0] if data.news else None
        return {
            "datum": first.date if first else None,
            "forfattare": first.author if first else None,
            "body": _short(first.body, 1000) if first else None,
            "antal": len(data.news),
            "items": [
                {"datum": n.date, "titel": n.title, "forfattare": n.author}
                for n in data.news[:MAX_LIST]
            ],
        }


class FritidsSensor(_ChildSensor):
    _attr_name = "Fritids hämtning idag"
    _attr_icon = "mdi:human-male-child"

    def _today(self, data: ChildData) -> dict[str, Any] | None:
        if not data.fritids:
            return None
        today = str(dt_util.now().date())
        for day in data.fritids.get("days", []):
            if day.get("date") == today:
                return day
        return None

    def _value(self, data: ChildData) -> str | None:
        day = self._today(data)
        return (day.get("pick_up") or None) if day else None

    def _attrs(self, data: ChildData) -> dict[str, Any]:
        day = self._today(data)
        return {"lamning": day.get("drop_off") if day else None}


class MenuSensor(SchoolSoftSchoolEntity, SensorEntity):
    """The school's weekly menu, as parsed from the linked file."""

    _attr_name = "Veckans matsedel"
    _attr_icon = "mdi:food"
    _unrecorded_attributes = frozenset({"dagar", "lank", "ocr_text"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "menu")

    @property
    def native_value(self) -> str | None:
        menu = self.coordinator.menu
        if menu is None:
            return None
        if menu.week:
            return f"v. {menu.week}"
        return menu.label or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        menu = self.coordinator.menu
        if menu is None:
            return {"status": self.coordinator.menu_error or "ej hämtad"}
        return {
            "status": menu.parse_status,
            "fel": self.coordinator.menu_error,
            "vecka": menu.week,
            "fil": menu.filename,
            "filtyp": menu.content_type,
            "lank": menu.link,
            "hamtad": menu.fetched.isoformat(),
            "ocr_text": menu.ocr_text or None,
            "dagar": [
                {
                    "datum": str(day),
                    "dag": WEEKDAY_LABELS[day.weekday()] if day.weekday() < 5 else str(day),
                    "ratt": item.main,
                    "beskrivning": item.description,
                    "vegetariskt": item.vegetarian,
                }
                for day, item in sorted(menu.days.items())
            ],
        }
