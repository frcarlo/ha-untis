"""Data coordinator: one login per update for all students of an account."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Lesson, Student, UntisAuthError, UntisClient, UntisError
from .const import CONF_STUDENTS, DOMAIN, EVENT_CHANGE, EVENT_DAYS, FETCH_DAYS, STORAGE_VERSION, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type UntisConfigEntry = ConfigEntry[UntisCoordinator]


@dataclass
class StudentPlan:
    student: Student
    lessons: list[Lesson]

    # ── Views used by the entities (all relative to "now") ──

    def held(self) -> list[Lesson]:
        return [l for l in self.lessons if not l.cancelled]

    def on(self, day: date) -> list[Lesson]:
        return [l for l in self.lessons if l.day == day]

    def current(self, now: datetime) -> Lesson | None:
        return next((l for l in self.held() if l.start <= now < l.end), None)

    def next(self, now: datetime) -> Lesson | None:
        return next((l for l in self.held() if l.start > now), None)

    def day_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        held = [l for l in self.held() if l.day == day]
        if not held:
            return None
        return min(l.start for l in held), max(l.end for l in held)

    def wake_up(self, now: datetime) -> Lesson | None:
        """First lesson of the next day on which school hasn't started yet."""
        started: set[date] = set()
        for lesson in self.held():
            if lesson.start <= now:
                started.add(lesson.day)
            elif lesson.day not in started:
                return lesson
        return None


class UntisCoordinator(DataUpdateCoordinator[dict[int, StudentPlan]]):
    config_entry: UntisConfigEntry

    def __init__(self, hass: HomeAssistant, entry: UntisConfigEntry, client: UntisClient) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.client = client
        # student id → lesson keys already reported (or seen on the first run)
        self._store: Store[dict[str, list[str]]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.seen")
        self._seen: dict[str, list[str]] | None = None
        # Event entities subscribe here: (student id, lesson, message)
        self.change_listeners: list[Callable[[int, Lesson, str], None]] = []

    def _selected(self) -> set[int] | None:
        ids = self.config_entry.options.get(CONF_STUDENTS)
        return {int(i) for i in ids} if ids else None

    async def _async_update_data(self) -> dict[int, StudentPlan]:
        tz = dt_util.get_default_time_zone()
        today = dt_util.now().date()
        try:
            plans = await self.client.timetables(today, FETCH_DAYS, tz, self._selected())
        except UntisAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UntisError as err:
            raise UpdateFailed(str(err)) from err
        data = {st.id: StudentPlan(st, lessons) for st, lessons in plans.items()}
        await self._report_changes(data)
        return data

    async def _report_changes(self, data: dict[int, StudentPlan]) -> None:
        """Fire an event per new cancellation/change today or tomorrow (port of checkUntis)."""
        if self._seen is None:
            self._seen = await self._store.async_load() or {}
        now = dt_util.now()
        last_day = now.date() + timedelta(days=EVENT_DAYS - 1)
        today_key = f"{now.date():%Y%m%d}"
        for sid, plan in data.items():
            skey = str(sid)
            first_run = skey not in self._seen
            known = {k for k in self._seen.get(skey, []) if k.split(":")[0] >= today_key}
            for lesson in plan.lessons:
                if not lesson.code or lesson.day > last_day or lesson.key in known:
                    continue
                known.add(lesson.key)
                # Lessons already over don't need an event
                if first_run or lesson.end <= now:
                    continue
                message = lesson_line(lesson)
                self.hass.bus.async_fire(
                    EVENT_CHANGE,
                    {
                        "config_entry_id": self.config_entry.entry_id,
                        "student": plan.student.name,
                        "student_id": plan.student.id,
                        "day": "today" if lesson.day == now.date() else "tomorrow",
                        "message": message,
                        **lesson.as_dict(),
                    },
                )
                for listener in self.change_listeners:
                    listener(sid, lesson, message)
            self._seen[skey] = sorted(known)
        self._store.async_delay_save(lambda: self._seen or {}, 5)


def lesson_line(l: Lesson) -> str:
    what = "entfällt" if l.cancelled else "geändert"
    extra = ", ".join(
        x
        for x in (
            f"Raum {l.room}" if l.room_changed and l.room else "",
            f"Lehrer {l.teacher}" if l.teacher_changed and l.teacher else "",
            "" if l.cancelled else (l.text or ""),
        )
        if x
    )
    return f"{l.start:%H:%M} {l.title} {what}{f' ({extra})' if extra else ''}"
