"""End-to-end: config flow, setup, sensors, calendar, change events, service."""

from __future__ import annotations


from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.untis.const import DOMAIN, EVENT_CHANGE

# Monday 2026-11-09, 09:00 Berlin: Deutsch is running, Englisch is cancelled
NOW = "2026-11-09 09:00:00+01:00"


def row(id_, date, start, end, su, ro="R1", te="Mü", code=None, ro_org=None, **extra):
    return {
        "id": id_, "date": date, "startTime": start, "endTime": end,
        "su": [{"id": 1, "name": su[:2].upper(), "longname": su}],
        "ro": [{"id": 1, "name": ro, **({"orgname": ro_org} if ro_org else {})}],
        "te": [{"id": 1, "name": te}],
        **({"code": code} if code else {}),
        **extra,
    }


def plan(fake):
    fake.timetable[11] = [
        row(1, 20261109, 800, 845, "Mathematik"),
        row(2, 20261109, 850, 935, "Deutsch", ro="B12", ro_org="A01", code="irregular"),
        row(3, 20261109, 950, 1035, "Englisch", code="cancelled", substText="Lehrer krank"),
        row(4, 20261109, 1040, 1125, "Biologie"),
        row(5, 20261110, 950, 1035, "Sport"),
        row(6, 20261110, 1040, 1125, "Kunst"),
    ]
    fake.timetable[12] = [row(7, 20261110, 800, 845, "Musik")]


async def _setup(hass: HomeAssistant, fake) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: fake.url, CONF_USERNAME: "eltern", CONF_PASSWORD: "secret", CONF_VERIFY_SSL: True},
        options={"students": ["11", "12"]},
        unique_id="x",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow(hass: HomeAssistant, untis_server) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    bad = {CONF_URL: "https://x.webuntis.com/WebUntis/", CONF_USERNAME: "eltern", CONF_PASSWORD: "secret"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"] == {CONF_URL: "invalid_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**bad, CONF_URL: untis_server.url, CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**bad, CONF_URL: untis_server.url}
    )
    assert result["step_id"] == "students"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"students": ["12"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Schule (Mia)"
    assert result["options"] == {"students": ["12"]}
    # Failed login, then one login + logout to list the students
    assert untis_server.calls[:3] == ["authenticate", "authenticate", "logout"]


async def test_entities(hass: HomeAssistant, untis_server, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    plan(untis_server)
    await _setup(hass, untis_server)

    assert hass.states.get("sensor.max_current_lesson").state == "Deutsch"
    assert hass.states.get("sensor.max_current_lesson").attributes["room_changed"] is True
    assert hass.states.get("sensor.max_next_lesson").state == "Biologie"
    assert hass.states.get("sensor.max_next_lesson_start").state == "2026-11-09T09:40:00+00:00"
    # Today has started → wake up for tomorrow's first lesson
    assert hass.states.get("sensor.max_next_lesson_to_wake_up").state == "2026-11-10T08:50:00+00:00"
    assert hass.states.get("sensor.max_school_start_today").state == "2026-11-09T07:00:00+00:00"
    assert hass.states.get("sensor.max_school_end_today").state == "2026-11-09T10:25:00+00:00"
    assert hass.states.get("sensor.max_cancelled_today").state == "1"
    assert hass.states.get("sensor.max_changes_today").state == "1"
    assert hass.states.get("sensor.max_cancelled_tomorrow").state == "0"
    assert hass.states.get("sensor.mia_current_lesson").state == "unknown"
    assert hass.states.get("sensor.mia_school_start_today").state == "unknown"
    assert hass.states.get("calendar.max_timetable").attributes["message"] == "⚠️ Deutsch"

    # The clock moves on without new data: the per-minute tick re-evaluates
    freezer.move_to("2026-11-09 10:45:00+01:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("sensor.max_current_lesson").state == "Biologie"
    assert hass.states.get("sensor.max_next_lesson").state == "Sport"

    # Calendar events incl. the cancellation
    resp = await hass.services.async_call(
        "calendar", "get_events",
        {"entity_id": "calendar.max_timetable", "start_date_time": "2026-11-09 00:00:00", "end_date_time": "2026-11-10 00:00:00"},
        blocking=True, return_response=True,
    )
    summaries = [e["summary"] for e in resp["calendar.max_timetable"]["events"]]
    assert summaries == ["Mathematik", "⚠️ Deutsch", "❌ Englisch (entfällt)", "Biologie"]


async def test_change_events(hass: HomeAssistant, untis_server, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    plan(untis_server)
    entry = await _setup(hass, untis_server)
    bus = async_capture_events(hass, EVENT_CHANGE)
    # First run only seeds what is already known
    assert hass.states.get("event.max_lesson_change").state == "unknown"

    # Tomorrow's Sport is cancelled now, plus a change far ahead (no event)
    untis_server.timetable[11][4] = row(5, 20261110, 950, 1035, "Sport", code="cancelled")
    untis_server.timetable[11].append(row(8, 20261113, 800, 845, "Chemie", code="cancelled"))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(bus) == 1
    assert bus[0].data["student"] == "Max"
    assert bus[0].data["day"] == "tomorrow"
    assert bus[0].data["message"] == "09:50 Sport entfällt"
    state = hass.states.get("event.max_lesson_change")
    assert state.attributes["event_type"] == "cancelled"
    assert state.attributes["message"] == "09:50 Sport entfällt"
    assert hass.states.get("event.mia_lesson_change").state == "unknown"

    # Same data again: nothing new
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(bus) == 1


async def test_service(hass: HomeAssistant, untis_server, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    plan(untis_server)
    entry = await _setup(hass, untis_server)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_11")})
    resp = await hass.services.async_call(
        DOMAIN, "get_timetable",
        {"device_id": device.id, "start": "2026-11-09", "end": "2026-11-10", "show_cancelled": False},
        blocking=True, return_response=True,
    )
    assert [l["subject_long"] for l in resp["lessons"]] == ["Mathematik", "Deutsch", "Biologie", "Sport", "Kunst"]


async def test_reauth_on_bad_password(hass: HomeAssistant, untis_server) -> None:
    untis_server.password = "changed"
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: untis_server.url, CONF_USERNAME: "eltern", CONF_PASSWORD: "secret"}, unique_id="x"
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"
