"""Switch platform for Bookoo espresso monitors."""

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import BookooConfigEntry
from .entity import BookooShotEntity
from .shot import DATA_MANAGER

PARALLEL_UPDATES = 0

AUTO_MONITOR_SWITCH = SwitchEntityDescription(
    key="auto_monitor",
    translation_key="auto_monitor",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switches."""
    coordinator = entry.runtime_data
    if coordinator.monitor is not None:
        async_add_entities(
            [BookooAutoMonitorSwitch(coordinator, hass.data[DATA_MANAGER], AUTO_MONITOR_SWITCH)]
        )


class BookooAutoMonitorSwitch(BookooShotEntity, SwitchEntity, RestoreEntity):
    """Connect the monitor whenever the scale connects, disconnect it afterwards."""

    async def async_added_to_hass(self) -> None:
        """Restore the last state (default on)."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self._manager.set_auto_monitor(last_state is None or last_state.state != STATE_OFF)

    @property
    def is_on(self) -> bool:
        """Return true if auto connect is enabled."""
        return self._manager.auto_monitor

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto connect."""
        self._manager.set_auto_monitor(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto connect."""
        self._manager.set_auto_monitor(False)
        self.async_write_ha_state()
