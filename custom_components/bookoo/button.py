"""Button entities for Bookoo devices."""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from aiobookoo.bookooscale import BookooScale
from aiobookoo.bookoomonitor import BookooEspressoMonitor

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

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self._scale)
