"""WebUntis timetable integration for Home Assistant."""

from __future__ import annotations

from datetime import datetime

import aiohttp

from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .api import UntisClient
from .const import DOMAIN
from .coordinator import UntisConfigEntry, UntisCoordinator
from .services import async_setup_services

PLATFORMS = [Platform.CALENDAR, Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: UntisConfigEntry) -> bool:
    # Own cookie jar-less session: the WebUntis session cookie is set per request
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    client = UntisClient(
        session,
        entry.data[CONF_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    coordinator = UntisCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # "Current/next lesson" depends on the clock, not only on new data: re-evaluate every minute
    @callback
    def _tick(_now: datetime) -> None:
        coordinator.async_update_listeners()

    entry.async_on_unload(async_track_time_change(hass, _tick, second=0))
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: UntisConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: UntisConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
