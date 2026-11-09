"""Base entity: one device per student."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Student
from .const import DOMAIN
from .coordinator import StudentPlan, UntisCoordinator


class UntisEntity(CoordinatorEntity[UntisCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: UntisCoordinator, student: Student, key: str) -> None:
        super().__init__(coordinator)
        self._student_id = student.id
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{student.id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{student.id}")},
            name=student.name,
            manufacturer="Untis",
            model="WebUntis",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{coordinator.client.origin}/WebUntis/?school={coordinator.client.school}",
        )

    @property
    def plan(self) -> StudentPlan | None:
        return (self.coordinator.data or {}).get(self._student_id)

    @property
    def available(self) -> bool:
        return super().available and self.plan is not None
