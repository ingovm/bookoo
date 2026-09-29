"""Binary sensor platform for Bookoo scales."""

from collections.abc import Callable
from dataclasses import dataclass

from aiobookoo.bookooscale import BookooScale
from aiobookoo.bookoomonitor import BookooEspressoMonitor

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BookooConfigEntry
from .entity import BookooEntity, BookooShotEntity
from .shot import DATA_MANAGER

# Coordinator is used to centralize the data updates
PARALLEL_UPDATES = 0


@dataclass(kw_only=True, frozen=True)
class BookooBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Description for Bookoo binary sensor entities."""

    is_on_fn: Callable[[BookooScale | BookooEspressoMonitor], bool]


BINARY_SENSORS: tuple[BookooBinarySensorEntityDescription, ...] = (
    BookooBinarySensorEntityDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=lambda scale: scale.connected,
    ),
)

SHOT_RUNNING_SENSOR = BinarySensorEntityDescription(
    key="shot_running",
    translation_key="shot_running",
    device_class=BinarySensorDeviceClass.RUNNING,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""

    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        BookooBinarySensor(coordinator, description) for description in BINARY_SENSORS
    ]
    if coordinator.scale is not None:
        entities.append(
            BookooShotRunningBinarySensor(
                coordinator, hass.data[DATA_MANAGER], SHOT_RUNNING_SENSOR
            )
        )
    async_add_entities(entities)


class BookooBinarySensor(BookooEntity, BinarySensorEntity):
    """Representation of an Bookoo binary sensor."""

    entity_description: BookooBinarySensorEntityDescription

    @property
    def available(self) -> bool:
        """Stay available while disconnected so the state reads off, not unavailable."""
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self.entity_description.is_on_fn(self._scale)


class BookooShotRunningBinarySensor(BookooShotEntity, BinarySensorEntity):
    """On while a shot is being recorded."""

    @property
    def is_on(self) -> bool:
        """Return true while a shot is recorded."""
        return self._manager.detector.recording
