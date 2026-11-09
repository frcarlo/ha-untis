"""Sensors per student: current/next lesson, school start/end, cancellations and changes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Lesson
from .coordinator import StudentPlan, UntisConfigEntry
from .entity import UntisEntity


def _lesson_attrs(l: Lesson | None) -> dict[str, Any]:
    if l is None:
        return {}
    return {
        "subject": l.subject,
        "subject_long": l.subject_long,
        "start": l.start.isoformat(),
        "end": l.end.isoformat(),
        "room": l.room,
        "room_changed": l.room_changed,
        "teacher": l.teacher,
        "teacher_changed": l.teacher_changed,
        "status": l.code,
        "text": l.text,
    }


def _bound(plan: StudentPlan, now: datetime, offset: int, index: int) -> datetime | None:
    bounds = plan.day_bounds(now.date() + timedelta(days=offset))
    return bounds[index] if bounds else None


def _with_code(code: str, offset: int) -> Callable[[StudentPlan, datetime], list[Lesson]]:
    def pick(plan: StudentPlan, now: datetime) -> list[Lesson]:
        return [l for l in plan.on(now.date() + timedelta(days=offset)) if l.code == code]

    return pick


@dataclass(frozen=True, kw_only=True)
class UntisSensorDescription(SensorEntityDescription):
    value_fn: Callable[[StudentPlan, datetime], Any]
    attrs_fn: Callable[[StudentPlan, datetime], dict[str, Any]] = lambda _p, _n: {}


def _lesson_sensor(key: str, icon: str, pick: Callable[[StudentPlan, datetime], Lesson | None]) -> UntisSensorDescription:
    return UntisSensorDescription(
        key=key,
        translation_key=key,
        icon=icon,
        value_fn=lambda p, n: (l.title if (l := pick(p, n)) else None),
        attrs_fn=lambda p, n: _lesson_attrs(pick(p, n)),
    )


def _list_sensor(key: str, icon: str, pick: Callable[[StudentPlan, datetime], list[Lesson]]) -> UntisSensorDescription:
    return UntisSensorDescription(
        key=key,
        translation_key=key,
        icon=icon,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p, n: len(pick(p, n)),
        attrs_fn=lambda p, n: {"lessons": [l.as_dict() for l in pick(p, n)]},
    )


SENSORS: tuple[UntisSensorDescription, ...] = (
    _lesson_sensor("current_lesson", "mdi:school", lambda p, n: p.current(n)),
    _lesson_sensor("next_lesson", "mdi:school-outline", lambda p, n: p.next(n)),
    UntisSensorDescription(
        key="next_lesson_start",
        translation_key="next_lesson_start",
        icon="mdi:clock-start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: (l.start if (l := p.next(n)) else None),
    ),
    UntisSensorDescription(
        key="wake_up",
        translation_key="wake_up",
        icon="mdi:alarm",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: (l.start if (l := p.wake_up(n)) else None),
        attrs_fn=lambda p, n: _lesson_attrs(p.wake_up(n)),
    ),
    UntisSensorDescription(
        key="today_start",
        translation_key="today_start",
        icon="mdi:bell-ring-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: _bound(p, n, 0, 0),
    ),
    UntisSensorDescription(
        key="today_end",
        translation_key="today_end",
        icon="mdi:home-import-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: _bound(p, n, 0, 1),
    ),
    UntisSensorDescription(
        key="tomorrow_start",
        translation_key="tomorrow_start",
        icon="mdi:bell-ring-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: _bound(p, n, 1, 0),
    ),
    UntisSensorDescription(
        key="tomorrow_end",
        translation_key="tomorrow_end",
        icon="mdi:home-import-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p, n: _bound(p, n, 1, 1),
    ),
    _list_sensor("cancelled_today", "mdi:cancel", _with_code("cancelled", 0)),
    _list_sensor("cancelled_tomorrow", "mdi:cancel", _with_code("cancelled", 1)),
    _list_sensor("changed_today", "mdi:swap-horizontal", _with_code("irregular", 0)),
    _list_sensor("changed_tomorrow", "mdi:swap-horizontal", _with_code("irregular", 1)),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: UntisConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        UntisSensor(coordinator, plan, desc) for plan in coordinator.data.values() for desc in SENSORS
    )


class UntisSensor(UntisEntity, SensorEntity):
    entity_description: UntisSensorDescription

    def __init__(self, coordinator, plan: StudentPlan, description: UntisSensorDescription) -> None:
        super().__init__(coordinator, plan.student, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        plan = self.plan
        return self.entity_description.value_fn(plan, dt_util.now()) if plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        plan = self.plan
        return self.entity_description.attrs_fn(plan, dt_util.now()) if plan else None
