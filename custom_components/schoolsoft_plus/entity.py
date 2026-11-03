"""Shared entity helpers for SchoolSoft Plus."""

from __future__ import annotations

import datetime as dt

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Assignment, ChildData, DatedLesson
from .const import DOMAIN, EXAM_RE
from .coordinator import SchoolSoftPlusCoordinator
from .vendor.models import Child


def first_name(child: Child) -> str:
    return child.name.split()[0] if child.name else str(child.student_id)


def is_exam(item: Assignment) -> bool:
    return bool(EXAM_RE.search(item.kind) or EXAM_RE.search(item.title))


def due_date(item: Assignment) -> dt.date | None:
    return item.end or item.start


def lesson_times(item: DatedLesson) -> tuple[dt.datetime, dt.datetime] | None:
    """Timezone-aware start and end of a lesson."""
    tz = dt_util.get_default_time_zone()
    try:
        start = dt.time.fromisoformat(item.lesson.start)
        end = dt.time.fromisoformat(item.lesson.end)
    except ValueError:
        return None
    return (
        dt.datetime.combine(item.date, start, tz),
        dt.datetime.combine(item.date, end, tz),
    )


class SchoolSoftChildEntity(CoordinatorEntity[SchoolSoftPlusCoordinator]):
    """An entity belonging to one child."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SchoolSoftPlusCoordinator, child: Child, key: str
    ) -> None:
        super().__init__(coordinator)
        self.child = child
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{child.student_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{child.student_id}")},
            name=f"SchoolSoft {first_name(child)}",
            manufacturer="SchoolSoft",
            model=" ".join(p for p in (child.school, child.grade) if p) or None,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def child_data(self) -> ChildData | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.child.student_id)

    @property
    def available(self) -> bool:
        return super().available and self.child_data is not None


class SchoolSoftSchoolEntity(CoordinatorEntity[SchoolSoftPlusCoordinator]):
    """An entity for the school itself (shared by all children)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SchoolSoftPlusCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        school = coordinator.hub.school
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_school")},
            name=f"SchoolSoft {school}",
            manufacturer="SchoolSoft",
            model="Skola",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://sms.schoolsoft.se/{school}/",
        )
