"""The school's weekly menu file as an image entity."""

from __future__ import annotations

import datetime as dt

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SchoolSoftPlusConfigEntry, SchoolSoftPlusCoordinator
from .entity import SchoolSoftSchoolEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolSoftPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([MenuImage(entry.runtime_data)])


class MenuImage(SchoolSoftSchoolEntity, ImageEntity):
    _attr_name = "Matsedel"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: SchoolSoftPlusCoordinator) -> None:
        SchoolSoftSchoolEntity.__init__(self, coordinator, "menu_image")
        ImageEntity.__init__(self, coordinator.hass)

    @property
    def available(self) -> bool:
        menu = self.coordinator.menu
        return menu is not None and menu.is_image

    @property
    def content_type(self) -> str:  # type: ignore[override]
        menu = self.coordinator.menu
        return menu.content_type if menu and menu.is_image else "image/png"

    @property
    def image_last_updated(self) -> dt.datetime | None:  # type: ignore[override]
        menu = self.coordinator.menu
        return menu.fetched if menu else None

    async def async_image(self) -> bytes | None:
        menu = self.coordinator.menu
        return menu.content if menu and menu.is_image else None
