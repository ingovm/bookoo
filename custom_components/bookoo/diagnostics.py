"""Diagnostics support for Bookoo."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import BookooConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    if coordinator.scale is not None:
        scale = coordinator.scale
        return {
            "model": scale.model,
            "device_state": (
                asdict(scale.device_state) if scale.device_state is not None else ""
            ),
            "mac": scale.mac,
            "last_disconnect_time": scale.last_disconnect_time,
            "timer": scale.timer,
            "weight": scale.weight,
        }

    monitor = coordinator.monitor
    return {
        "model": monitor.model,
        "mac": monitor.mac,
        "last_disconnect_time": monitor.last_disconnect_time,
        "pressure": monitor.pressure,
        "battery": monitor.battery,
    }
