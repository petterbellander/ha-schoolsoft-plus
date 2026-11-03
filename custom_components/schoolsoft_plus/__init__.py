"""SchoolSoft Plus: schedule, homework, tests, lunch and more from SchoolSoft."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
import httpx

from homeassistant.util.ssl import get_default_context

from .api import (
    SchoolSoftAccessError,
    SchoolSoftAuthError,
    SchoolSoftConnectionError,
    SchoolSoftHub,
)
from .const import BASE_URL, CONF_SCHOOL, USERTYPE_PARENT
from .coordinator import SchoolSoftPlusConfigEntry, SchoolSoftPlusCoordinator
from .vendor.client import SchoolSoftClient

PLATFORMS = [Platform.CALENDAR, Platform.IMAGE, Platform.SENSOR]


def build_hub(hass: HomeAssistant, school: str, username: str, password: str) -> SchoolSoftHub:
    """Create a hub with its own cookie jar (one SchoolSoft session)."""
    # Own client (own cookie jar) that we may close; HA's shared httpx
    # clients must not be closed by integrations.
    http_client = httpx.AsyncClient(verify=get_default_context())
    client = SchoolSoftClient(
        school,
        username,
        password,
        usertype=USERTYPE_PARENT,
        base_url=BASE_URL,
        http_client=http_client,
    )
    return SchoolSoftHub(client, school, USERTYPE_PARENT)


async def async_setup_entry(hass: HomeAssistant, entry: SchoolSoftPlusConfigEntry) -> bool:
    """Set up SchoolSoft Plus from a config entry."""
    hub = build_hub(
        hass, entry.data[CONF_SCHOOL], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )
    try:
        await hub.async_login()
        children = await hub.async_children()
    except SchoolSoftAccessError as err:
        await hub.async_close()
        raise ConfigEntryNotReady(str(err)) from err
    except SchoolSoftAuthError as err:
        await hub.async_close()
        raise ConfigEntryAuthFailed(str(err)) from err
    except (SchoolSoftConnectionError, Exception) as err:
        await hub.async_close()
        raise ConfigEntryNotReady(f"SchoolSoft not reachable: {err}") from err

    if not children:
        await hub.async_close()
        raise ConfigEntryNotReady("SchoolSoft returned no children for this account")

    coordinator = SchoolSoftPlusCoordinator(hass, entry, hub, children)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await hub.async_close()
        raise
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SchoolSoftPlusConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.hub.async_close()
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: SchoolSoftPlusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
