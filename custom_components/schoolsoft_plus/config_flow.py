"""Config flow for SchoolSoft Plus."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import build_hub
from .api import SchoolSoftAccessError, SchoolSoftAuthError, SchoolSoftConnectionError
from .const import CONF_AI_TASK, CONF_SCHOOL, DEFAULT_SCHOOL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _schema(school: str = DEFAULT_SCHOOL, username: str = "") -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SCHOOL, default=school): str,
            vol.Required(CONF_USERNAME, default=username): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


class SchoolSoftPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SchoolSoft Plus."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SchoolSoftPlusOptionsFlow()

    async def _validate(self, data: dict[str, Any]) -> tuple[str | None, list[str]]:
        """Log in and list children. Returns (error key, child names)."""
        hub = build_hub(self.hass, data[CONF_SCHOOL], data[CONF_USERNAME], data[CONF_PASSWORD])
        try:
            await hub.async_login()
            children = await hub.async_children()
        except SchoolSoftAccessError:
            return "cannot_connect", []
        except SchoolSoftAuthError:
            return "invalid_auth", []
        except SchoolSoftConnectionError:
            return "cannot_connect", []
        except Exception:
            _LOGGER.exception("Unexpected error validating SchoolSoft login")
            return "unknown", []
        finally:
            await hub.async_close()
        if not children:
            return "no_children", []
        return None, [c.name for c in children]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_SCHOOL] = user_input[CONF_SCHOOL].strip().strip("/")
            user_input[CONF_USERNAME] = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(
                f"{user_input[CONF_SCHOOL].lower()}_{user_input[CONF_USERNAME].lower()}"
            )
            self._abort_if_unique_id_configured()
            error, names = await self._validate(user_input)
            if error is None:
                title = f"SchoolSoft ({', '.join(names)})"
                return self.async_create_entry(title=title, data=user_input)
            errors["base"] = error
            schema = _schema(user_input[CONF_SCHOOL], user_input[CONF_USERNAME])
        else:
            schema = _schema()
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            error, _ = await self._validate(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )


class SchoolSoftPlusOptionsFlow(OptionsFlow):
    """Choose an AI Task entity for reading image menus."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_AI_TASK)
        key = (
            vol.Optional(CONF_AI_TASK, description={"suggested_value": current})
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {key: EntitySelector(EntitySelectorConfig(domain="ai_task"))}
            ),
        )
