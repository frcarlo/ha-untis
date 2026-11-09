"""Timetable calendar per student (the fetched window: today + 14 days)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Lesson
from .coordinator import StudentPlan, UntisConfigEntry
from .entity import UntisEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: UntisConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UntisCalendar(coordinator, plan) for plan in coordinator.data.values())


def _event(l: Lesson) -> CalendarEvent:
    summary = l.title
    if l.cancelled:
        summary = f"❌ {summary} (entfällt)"
    elif l.changed:
        summary = f"⚠️ {summary}"
    room = f"{l.room} (geändert)" if l.room_changed and l.room else l.room
    teacher = f"{l.teacher} (Vertretung)" if l.teacher_changed and l.teacher else l.teacher
    description = "\n".join(x for x in (teacher and f"Lehrer: {teacher}", l.text) if x) or None
    return CalendarEvent(
        start=l.start,
        end=l.end,
        summary=summary,
        location=room,
        description=description,
        uid=f"untis-{l.day:%Y%m%d}-{l.id}",
    )


class UntisCalendar(UntisEntity, CalendarEntity):
    _attr_translation_key = "timetable"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, plan: StudentPlan) -> None:
        super().__init__(coordinator, plan.student, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        plan = self.plan
        if not plan:
            return None
        now = dt_util.now()
        lesson = plan.current(now) or plan.next(now)
        return _event(lesson) if lesson else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        plan = self.plan
        if not plan:
            return []
        return [_event(l) for l in plan.lessons if l.end > start_date and l.start < end_date]
