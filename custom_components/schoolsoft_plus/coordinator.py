"""Data update coordinator for SchoolSoft Plus."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ChildData,
    SchoolSoftAccessError,
    SchoolSoftAuthError,
    SchoolSoftConnectionError,
    SchoolSoftHub,
)
from .const import CONF_AI_TASK, DOMAIN, SLOW_INTERVAL, UPDATE_INTERVAL
from . import menu as menu_mod
from .menu import MenuDay, SchoolMenu
from .vendor.models import Child

_LOGGER = logging.getLogger(__name__)

type SchoolSoftPlusConfigEntry = ConfigEntry[SchoolSoftPlusCoordinator]


class SchoolSoftPlusCoordinator(DataUpdateCoordinator[dict[int, ChildData]]):
    """Polls SchoolSoft for every child on the account."""

    config_entry: SchoolSoftPlusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SchoolSoftPlusConfigEntry,
        hub: SchoolSoftHub,
        children: list[Child],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.hub = hub
        self.children = children
        self.menu: SchoolMenu | None = None
        # Other menus listed for this week or later (e.g. next week published early).
        self.extra_menus: list[SchoolMenu] = []
        self.menu_error: str | None = None
        self._menu_checked: dt.datetime | None = None
        self._menu_parsing = False
        # digest -> {"week": int | None, "days": {weekday index: [main, description, veg]}}
        self._menu_store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.menu"
        )
        self._menu_cache: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[int, ChildData]:
        now = dt_util.now()
        today = now.date()
        previous = self.data or {}
        result: dict[int, ChildData] = {}
        only_child = len(self.children) == 1
        failures = 0
        for child in self.children:
            prev = previous.get(child.student_id)
            slow = (
                prev is None
                or prev.slow_updated is None
                or dt_util.utcnow() - prev.slow_updated >= SLOW_INTERVAL
            )
            try:
                result[child.student_id] = await self.hub.async_fetch_child(
                    child, prev, only_child=only_child, slow=slow, today=today
                )
            except SchoolSoftAccessError as err:
                failures += 1
                _LOGGER.warning("SchoolSoft: could not select %s: %s", child.name, err)
                if prev is not None:
                    result[child.student_id] = prev
            except SchoolSoftAuthError as err:
                raise ConfigEntryAuthFailed(f"SchoolSoft login failed: {err}") from err
            except (SchoolSoftConnectionError, ValueError) as err:
                failures += 1
                _LOGGER.warning("SchoolSoft update for %s failed: %s", child.name, err)
                if prev is not None:
                    result[child.student_id] = prev
        if failures == len(self.children) and not result:
            raise UpdateFailed("SchoolSoft could not be reached for any child")

        if self._menu_checked is None or dt_util.utcnow() - self._menu_checked >= SLOW_INTERVAL:
            self._menu_checked = dt_util.utcnow()
            try:
                await self._async_refresh_menu()
                self.menu_error = None
            except SchoolSoftAuthError as err:
                self.menu_error = str(err)
                _LOGGER.warning("SchoolSoft menu: %s", err)
            except (SchoolSoftConnectionError, HomeAssistantError, ValueError, OSError, Exception) as err:  # noqa: BLE001
                self.menu_error = str(err)
                _LOGGER.warning("SchoolSoft menu could not be fetched: %s", err)
        self._merge_menu(result)
        return result

    # -- school lunch menu ---------------------------------------------------------

    def menu_days(self) -> dict[dt.date, MenuDay]:
        """Days from all loaded menus; the main menu wins where weeks overlap."""
        days: dict[dt.date, MenuDay] = {}
        for menu in [*self.extra_menus, self.menu]:
            if menu is not None:
                days.update(menu.days)
        return days

    def _merge_menu(self, result: dict[int, ChildData]) -> None:
        days = self.menu_days()
        if not days:
            return
        menu_lunch = {day: item.as_lunch() for day, item in days.items()}
        for data in result.values():
            data.lunch = {**data.lunch, **menu_lunch}

    def menu_day(self, day: dt.date) -> MenuDay | None:
        return self.menu_days().get(day)

    async def _async_refresh_menu(self) -> None:
        async with self.hub.lock:
            html = await self.hub.client.fetch_html(menu_mod.SCHOOL_INFO_PATH)
        today = dt_util.now().date()
        links = menu_mod.find_menu_links(html)
        found = menu_mod.choose_menu_link(links, today)
        if found is None:
            raise ValueError("No menu link found under 'Matsedel' on Skolinformation")
        await self._async_refresh_extra_menus(menu_mod.upcoming_menu_links(links, today))
        link, label = found
        new = await menu_mod.async_download(get_async_client(self.hass), link, label)
        if self.menu is not None and self.menu.digest == new.digest:
            self.menu.link, self.menu.label = new.link, new.label or self.menu.label
            if not self.menu.days and self.menu.is_image:
                self._schedule_ai_parse()
            return
        self.menu = new
        await self._async_parse_menu(new)

    async def _async_refresh_extra_menus(self, links: list[tuple[str, str]]) -> None:
        known = {menu.link: menu for menu in self.extra_menus}
        extra: list[SchoolMenu] = []
        for link, label in links:
            try:
                new = await menu_mod.async_download(get_async_client(self.hass), link, label)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("SchoolSoft menu %s could not be fetched: %s", label, err)
                if link in known:
                    extra.append(known[link])
                continue
            old = known.get(link)
            if old is not None and old.digest == new.digest:
                extra.append(old)
                continue
            # Extra weeks are read from PDF text or OCR; AI Task is used for the main menu only.
            await self._async_parse_menu(new, allow_ai=False)
            extra.append(new)
        self.extra_menus = extra

    async def _async_cache(self) -> dict[str, Any]:
        if self._menu_cache is None:
            self._menu_cache = await self._menu_store.async_load() or {}
        return self._menu_cache

    def _apply_parsed(self, menu: SchoolMenu, week: int | None, days: dict[int, MenuDay], status: str) -> None:
        week = week or menu_mod.week_of(menu)
        menu.week = week
        menu.days = menu_mod.dated_days(week, days, dt_util.now().date())
        menu.parse_status = status if menu.days else f"{status}, men inga rätter hittades"
        if days and week is None:
            menu.parse_status = "veckonummer saknas"

    async def _async_parse_menu(self, menu: SchoolMenu, *, allow_ai: bool = True) -> None:
        cache = await self._async_cache()
        hit = cache.get(menu.digest)
        # Re-parse PDF/OCR results from an older parser; AI results are kept.
        stale = (
            hit
            and hit.get("parser", 1) < menu_mod.PARSER_VERSION
            and hit.get("status") in ("tolkad från PDF", "tolkad med OCR")
        )
        if hit and not stale:
            days = {int(k): MenuDay(*v) for k, v in hit["days"].items()}
            self._apply_parsed(menu, hit.get("week"), days, hit.get("status", "tolkad"))
            return
        if menu.is_pdf:
            week, days = await menu_mod.async_parse_pdf(menu)
            await self._async_store(menu, week, days, "tolkad från PDF")
            self._apply_parsed(menu, week, days, "tolkad från PDF")
        elif menu.is_image and allow_ai and self.config_entry.options.get(CONF_AI_TASK):
            menu.parse_status = "väntar på AI-tolkning"
            self._schedule_ai_parse()
        elif menu.is_image:
            tessdata = await menu_mod.async_ensure_tessdata(
                get_async_client(self.hass), self.hass.config.path(".storage", f"{DOMAIN}_tessdata")
            )
            text, week, days = await menu_mod.async_ocr(menu, tessdata)
            menu.ocr_text = text
            _LOGGER.info("SchoolSoft menu OCR text:\n%s", text)
            await self._async_store(menu, week, days, "tolkad med OCR")
            self._apply_parsed(menu, week, days, "tolkad med OCR")
        else:
            menu.parse_status = f"okänt filformat ({menu.content_type})"

    async def _async_store(self, menu: SchoolMenu, week: int | None, days: dict[int, MenuDay], status: str) -> None:
        cache = await self._async_cache()
        # Keep only a handful of recent menus.
        while len(cache) >= 8:
            cache.pop(next(iter(cache)))
        cache[menu.digest] = {
            "week": week,
            "status": status,
            "parser": menu_mod.PARSER_VERSION,
            "days": {str(k): [d.main, d.description, d.vegetarian] for k, d in days.items()},
        }
        await self._menu_store.async_save(cache)

    def _schedule_ai_parse(self) -> None:
        if self._menu_parsing:
            return
        entity_id = self.config_entry.options.get(CONF_AI_TASK)
        if not entity_id:
            if self.menu:
                self.menu.parse_status = "bild – välj en AI-uppgift i integrationens alternativ för att tolka den"
            return
        self._menu_parsing = True
        self.config_entry.async_create_background_task(
            self.hass, self._async_ai_parse(entity_id), f"{DOMAIN}_menu_ai"
        )

    async def _async_ai_parse(self, ai_entity: str) -> None:
        try:
            menu = self.menu
            if menu is None:
                return
            registry = er.async_get(self.hass)
            image_entity = None
            for _ in range(30):
                image_entity = registry.async_get_entity_id(
                    "image", DOMAIN, f"{self.config_entry.entry_id}_menu_image"
                )
                if image_entity and self.hass.states.get(image_entity):
                    break
                await asyncio.sleep(10)
            if not image_entity:
                menu.parse_status = "bildentiteten saknas"
                return
            try:
                response = await self.hass.services.async_call(
                    "ai_task",
                    "generate_data",
                    {
                        "task_name": "SchoolSoft matsedel",
                        "entity_id": ai_entity,
                        "instructions": menu_mod.AI_INSTRUCTIONS,
                        "structure": menu_mod.ai_structure(),
                        "attachments": [
                            {
                                "media_content_id": f"media-source://image/{image_entity}",
                                "media_content_type": menu.content_type,
                            }
                        ],
                    },
                    blocking=True,
                    return_response=True,
                )
            except Exception as err:  # noqa: BLE001
                menu.parse_status = f"AI-tolkning misslyckades: {err}"
                _LOGGER.warning("SchoolSoft menu AI parse failed: %s", err)
                return
            data = (response or {}).get("data") or {}
            if not isinstance(data, dict):
                menu.parse_status = "AI-svaret kunde inte läsas"
                return
            week, days = menu_mod.parse_ai_result(data)
            if menu is not self.menu:
                return
            status = f"tolkad med {ai_entity}"
            await self._async_store(menu, week, days, status)
            self._apply_parsed(menu, week, days, status)
            if self.data:
                self._merge_menu(self.data)
        finally:
            self._menu_parsing = False
            self.async_update_listeners()
