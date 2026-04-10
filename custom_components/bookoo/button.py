"""Button entities for Bookoo devices."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from aiobookoo.bookooscale import BookooScale
from aiobookoo.bookoomonitor import BookooEspressoMonitor

from homeassistant.components import bluetooth
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BookooConfigEntry
from .entity import BookooEntity

PARALLEL_UPDATES = 0


@dataclass(kw_only=True, frozen=True)
class BookooButtonEntityDescription(ButtonEntityDescription):
    """Description for Bookoo button entities."""

    press_fn: Callable[[BookooScale | BookooEspressoMonitor], Coroutine[Any, Any, None]]


SCALE_BUTTONS: tuple[BookooButtonEntityDescription, ...] = (
    BookooButtonEntityDescription(
        key="tare",
        translation_key="tare",
        press_fn=lambda device: device.tare(),
    ),
    BookooButtonEntityDescription(
        key="reset_timer",
        translation_key="reset_timer",
        press_fn=lambda device: device.reset_timer(),
    ),
    BookooButtonEntityDescription(
        key="start",
        translation_key="start",
        press_fn=lambda device: device.start_timer(),
    ),
    BookooButtonEntityDescription(
        key="stop",
        translation_key="stop",
        press_fn=lambda device: device.stop_timer(),
    ),
    BookooButtonEntityDescription(
        key="tare_and_start",
        translation_key="tare_and_start",
        press_fn=lambda device: device.tare_and_start_timer(),
    ),
)

MONITOR_BUTTONS: tuple[BookooButtonEntityDescription, ...] = (
    BookooButtonEntityDescription(
        key="start_extraction",
        translation_key="start_extraction",
        press_fn=lambda device: device.start_extraction(),
    ),
    BookooButtonEntityDescription(
        key="stop_extraction",
        translation_key="stop_extraction",
        press_fn=lambda device: device.stop_extraction(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities and services."""

    coordinator = entry.runtime_data
    buttons = SCALE_BUTTONS if coordinator.scale is not None else MONITOR_BUTTONS
    async_add_entities(BookooButton(coordinator, description) for description in buttons)


class BookooButton(BookooEntity, ButtonEntity):
    """Representation of a Bookoo button."""

    entity_description: BookooButtonEntityDescription

    @property
    def available(self) -> bool:
        """Return True if entity is available.

        Monitor buttons are always available so the user can start/stop extraction
        regardless of connection state. Scale buttons require an active connection.
        """
        if isinstance(self._scale, BookooEspressoMonitor):
            return True
        return super().available

    async def async_press(self) -> None:
        """Handle the button press."""
        # For the espresso monitor, refresh BLEDevice before connecting so that
        # bleak_retry_connector is used and the connection is reliable.
        if isinstance(self._scale, BookooEspressoMonitor) and not self._scale.connected:
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._scale.mac, connectable=True
            ) or bluetooth.async_ble_device_from_address(
                self.hass, self._scale.mac, connectable=False
            )
            if ble_device:
                self._scale.address_or_ble_device = ble_device

        await self.entity_description.press_fn(self._scale)
