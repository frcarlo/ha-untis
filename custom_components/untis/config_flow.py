"""Config flow: browser URL + parent/student login, then pick the students."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import Student, UntisAuthError, UntisClient, UntisError, UntisUrlError, parse_url
from .const import CONF_STUDENTS, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


async def _fetch_students(data: Mapping[str, Any]) -> list[Student]:
    """Log in once and list the account's students. Raises UntisError subclasses."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        client = UntisClient(session, data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD], data.get(CONF_VERIFY_SSL, True))
        return await client.students()


async def _validate(data: Mapping[str, Any]) -> tuple[list[Student], dict[str, str]]:
    try:
        students = await _fetch_students(data)
    except UntisUrlError:
        return [], {CONF_URL: "invalid_url"}
    except UntisAuthError:
        return [], {"base": "invalid_auth"}
    except UntisError as err:
        _LOGGER.debug("WebUntis connection failed: %s", err)
        return [], {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error")
        return [], {"base": "unknown"}
    if not students:
        return [], {"base": "no_students"}
    return students, {}


def _student_selector(students: list[Student]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[SelectOptionDict(value=str(s.id), label=s.name) for s in students],
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class UntisConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._students: list[Student] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                origin, school = parse_url(user_input[CONF_URL])
            except UntisUrlError:
                errors[CONF_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(f"{origin}|{school}|{user_input[CONF_USERNAME]}".lower())
                self._abort_if_unique_id_configured()
                self._students, errors = await _validate(user_input)
                if not errors:
                    self._data = {**user_input, "school": school}
                    if len(self._students) > 1:
                        return await self.async_step_students()
                    return self._create()
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input), errors=errors
        )

    async def async_step_students(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_STUDENTS):
                return self._create(user_input[CONF_STUDENTS])
            errors["base"] = "no_selection"
        return self.async_show_form(
            step_id="students",
            data_schema=vol.Schema(
                {vol.Required(CONF_STUDENTS, default=[str(s.id) for s in self._students]): _student_selector(self._students)}
            ),
            errors=errors,
        )

    def _create(self, selected: list[str] | None = None) -> ConfigFlowResult:
        names = [s.name for s in self._students if selected is None or str(s.id) in selected]
        title = f"{self._data['school']} ({', '.join(names)})"
        return self.async_create_entry(
            title=title,
            data={k: v for k, v in self._data.items() if k != "school"},
            options={CONF_STUDENTS: selected} if selected else {},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            _, errors = await _validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return UntisOptionsFlow()


class UntisOptionsFlow(OptionsFlow):
    """Change which students get a device."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_STUDENTS):
                return self.async_create_entry(data={CONF_STUDENTS: user_input[CONF_STUDENTS]})
            errors["base"] = "no_selection"
        students, load_errors = await _validate(self.config_entry.data)
        if load_errors:
            return self.async_abort(reason=load_errors.get("base", "cannot_connect"))
        current = self.config_entry.options.get(CONF_STUDENTS) or [str(s.id) for s in students]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required(CONF_STUDENTS, default=current): _student_selector(students)}),
            errors=errors,
        )
