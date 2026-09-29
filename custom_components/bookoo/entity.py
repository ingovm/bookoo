"""Base class for Bookoo entities."""

from dataclasses import dataclass

from homeassistant.helpers.device_registry import (
    CONNECTION_BLUETOOTH,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BookooCoordinator
from .shot import ShotManager


def bookoo_device_info(coordinator: BookooCoordinator) -> DeviceInfo:
    """Return the device info shared by all entities of a device."""
    device = coordinator.device
    return DeviceInfo(
        identifiers={(DOMAIN, format_mac(device.mac))},
        manufacturer="Bookoo",
        model=device.model,
        suggested_area="Kitchen",
        connections={(CONNECTION_BLUETOOTH, device.mac)},
    )


@dataclass
class BookooEntity(CoordinatorEntity[BookooCoordinator]):
    """Common elements for all entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BookooCoordinator,
        entity_description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._scale = coordinator.device
        self._attr_unique_id = f"{format_mac(self._scale.mac)}_{entity_description.key}"
        self._attr_device_info = bookoo_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Returns whether entity is available."""
        return super().available and self._scale.connected


class BookooShotEntity(Entity):
    """Entity fed by the shared shot manager instead of the coordinator.

    Deliberately not a CoordinatorEntity: the scale notifies ~10x per second and
    these entities should only write state when a shot starts, stops or is stored.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: BookooCoordinator,
        manager: ShotManager,
        entity_description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        self.entity_description = entity_description
        self._manager = manager
        self._attr_unique_id = f"{format_mac(coordinator.device.mac)}_{entity_description.key}"
        self._attr_device_info = bookoo_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        """Subscribe to shot updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._manager.async_add_listener(self.async_write_ha_state))
