"""Event entity per student: fires on new cancellations/changes for today and tomorrow."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Lesson
from .coordinator import StudentPlan, UntisConfigEntry
from .entity import UntisEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: UntisConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UntisChangeEvent(coordinator, plan) for plan in coordinator.data.values())


class UntisChangeEvent(UntisEntity, EventEntity):
    _attr_translation_key = "lesson_change"
    _attr_icon = "mdi:calendar-alert"
    _attr_event_types = ["cancelled", "changed"]

    def __init__(self, coordinator, plan: StudentPlan) -> None:
        super().__init__(coordinator, plan.student, "lesson_change")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.change_listeners.append(self._on_change)
        self.async_on_remove(lambda: self.coordinator.change_listeners.remove(self._on_change))

    @callback
    def _on_change(self, student_id: int, lesson: Lesson, message: str) -> None:
        if student_id != self._student_id:
            return
        self._trigger_event(
            "cancelled" if lesson.cancelled else "changed",
            {
                "message": message,
                "day": "today" if lesson.day == dt_util.now().date() else "tomorrow",
                **lesson.as_dict(),
            },
        )
        self.async_write_ha_state()
