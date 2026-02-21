"""Component providing HA binary sensor support for Ring cameras."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic

from ring_doorbell import RingCapability, RingEvent
from ring_doorbell.const import KIND_MOTION, KIND_MOTION_HUMAN, KIND_MOTION_VEHICLE, KIND_MOTION_OTHER

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_at

from . import RingConfigEntry
from .coordinator import RingListenCoordinator
from .entity import RingBaseEntity, RingDeviceT

PARALLEL_UPDATES = 0
MOTION_CLEAR_TIMEOUT = 30


@dataclass(frozen=True, kw_only=True)
class RingMotionSensorEntityDescription(
    BinarySensorEntityDescription, Generic[RingDeviceT]
):
    """Describes Ring motion binary sensor entity."""

    motion_state: str


BINARY_SENSOR_TYPES: tuple[RingMotionSensorEntityDescription, ...] = (
    RingMotionSensorEntityDescription(
        key=KIND_MOTION_HUMAN,
        translation_key="person_detected",
        device_class=BinarySensorDeviceClass.MOTION,
        motion_state=KIND_MOTION_HUMAN,
    ),
    RingMotionSensorEntityDescription(
        key=KIND_MOTION_VEHICLE,
        translation_key="vehicle_detected",
        device_class=BinarySensorDeviceClass.MOTION,
        motion_state=KIND_MOTION_VEHICLE,
    ),
    RingMotionSensorEntityDescription(
        key=KIND_MOTION_OTHER,
        translation_key="motion_detected",
        device_class=BinarySensorDeviceClass.MOTION,
        motion_state=KIND_MOTION_OTHER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Ring binary sensors from a config entry."""
    ring_data = entry.runtime_data
    listen_coordinator = ring_data.listen_coordinator

    async_add_entities(
        RingMotionBinarySensor(device, listen_coordinator, description)
        for description in BINARY_SENSOR_TYPES
        for device in ring_data.devices.all_devices
        if device.has_capability(RingCapability.MOTION_DETECTION)
    )


class RingMotionBinarySensor(
    RingBaseEntity[RingListenCoordinator, RingDeviceT], BinarySensorEntity
):
    """A binary sensor for Ring motion detection."""

    entity_description: RingMotionSensorEntityDescription[RingDeviceT]

    def __init__(
        self,
        device: RingDeviceT,
        coordinator: RingListenCoordinator,
        description: RingMotionSensorEntityDescription[RingDeviceT],
    ) -> None:
        """Initialize a binary sensor for Ring device."""
        super().__init__(device, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{device.id}-{description.key}"
        self._attr_is_on = False
        self._active_alert: RingEvent | None = None
        self._cancel_callback: CALLBACK_TYPE | None = None

    @callback
    def _async_handle_event(self, alert: RingEvent) -> None:
        """Handle the event."""
        self._attr_is_on = True
        self._active_alert = alert
        loop = self.hass.loop
        when = loop.time() + MOTION_CLEAR_TIMEOUT
        if self._cancel_callback:
            self._cancel_callback()
        self._cancel_callback = async_call_at(self.hass, self._async_cancel_event, when)

    @callback
    def _async_cancel_event(self, _now: Any) -> None:
        """Clear the event."""
        self._cancel_callback = None
        self._attr_is_on = False
        self._active_alert = None
        self.async_write_ha_state()

    def _get_coordinator_alert(self) -> RingEvent | None:
        alert = self.coordinator.alerts.get(
            (self._device.device_api_id, KIND_MOTION)
        )
        if alert and alert.state == self.entity_description.motion_state:
            return alert
        return None

    @callback
    def _handle_coordinator_update(self) -> None:
        if alert := self._get_coordinator_alert():
            self._async_handle_event(alert)
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.event_listener.started

    async def async_update(self) -> None:
        """All updates are passive."""

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return the state attributes."""
        attrs = dict(super().extra_state_attributes or {})

        if self._active_alert is None:
            return attrs

        attrs["state"] = self._active_alert.state
        now = self._active_alert.now
        attrs["expires_at"] = datetime.fromtimestamp(
            now + MOTION_CLEAR_TIMEOUT
        ).isoformat()

        return attrs
