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
from .entity import BookooEntity

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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""

    coordinator = entry.runtime_data
    async_add_entities(
        BookooBinarySensor(coordinator, description) for description in BINARY_SENSORS
    )


class BookooBinarySensor(BookooEntity, BinarySensorEntity):
    """Representation of an Bookoo binary sensor."""

    entity_description: BookooBinarySensorEntityDescription

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        return self.entity_description.is_on_fn(self._scale)
