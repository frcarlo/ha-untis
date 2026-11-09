"""untis.get_timetable: a student's lessons for any date range (response-only service)."""

from __future__ import annotations

from datetime import date

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.util import dt as dt_util

from .api import UntisError
from .const import DOMAIN

MAX_DAYS = 62

SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("start"): cv.date,
        vol.Required("end"): cv.date,
        vol.Optional("show_cancelled", default=True): cv.boolean,
    }
)


async def _get_timetable(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    device = dr.async_get(hass).async_get(call.data["device_id"])
    ident = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None) if device else None
    if not ident:
        raise ServiceValidationError("Not an Untis student device")
    entry_id, student_id = ident.rsplit("_", 1)
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("Untis integration is not loaded")

    start: date = call.data["start"]
    end: date = call.data["end"]
    days = (end - start).days + 1
    if days < 1 or days > MAX_DAYS:
        raise ServiceValidationError(f"The range must be 1–{MAX_DAYS} days")

    try:
        lessons = await entry.runtime_data.client.timetable(int(student_id), start, days, dt_util.get_default_time_zone())
    except UntisError as err:
        raise HomeAssistantError(str(err)) from err
    if not call.data["show_cancelled"]:
        lessons = [l for l in lessons if not l.cancelled]
    return {"lessons": [l.as_dict() for l in lessons]}


def async_setup_services(hass: HomeAssistant) -> None:
    hass.services.async_register(
        DOMAIN, "get_timetable", _get_timetable, schema=SCHEMA, supports_response=SupportsResponse.ONLY
    )
