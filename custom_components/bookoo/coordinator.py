"""Coordinator for Bookoo integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from aiobookoo.bookooscale import BookooScale
from aiobookoo.bookoomonitor import BookooEspressoMonitor
from aiobookoo.exceptions import BookooDeviceNotFound, BookooError

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_DEVICE_TYPE, CONF_IS_VALID_SCALE, DEVICE_TYPE_MONITOR

SCAN_INTERVAL = timedelta(seconds=5)

_LOGGER = logging.getLogger(__name__)

type BookooConfigEntry = ConfigEntry[BookooCoordinator]


class BookooCoordinator(DataUpdateCoordinator[None]):
    """Class to handle fetching data from a Bookoo device."""

    config_entry: BookooConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BookooConfigEntry) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="bookoo coordinator",
            update_interval=SCAN_INTERVAL,
            config_entry=entry,
        )

        device_type = entry.data.get(CONF_DEVICE_TYPE)

        if device_type == DEVICE_TYPE_MONITOR:
            self._device: BookooScale | BookooEspressoMonitor = BookooEspressoMonitor(
                address_or_ble_device=entry.data[CONF_ADDRESS],
                name=entry.title,
                notify_callback=self.async_update_listeners,
            )
        else:
            # Default to scale; also handles legacy config entries without CONF_DEVICE_TYPE
            self._device = BookooScale(
                address_or_ble_device=entry.data[CONF_ADDRESS],
                name=entry.title,
                is_valid_scale=entry.data.get(CONF_IS_VALID_SCALE, True),
                notify_callback=self.async_update_listeners,
            )

    @property
    def device(self) -> BookooScale | BookooEspressoMonitor:
        """Return the underlying device object."""
        return self._device

    @property
    def scale(self) -> BookooScale | None:
        """Return the scale object, or None if the device is not a scale."""
        return self._device if isinstance(self._device, BookooScale) else None

    @property
    def monitor(self) -> BookooEspressoMonitor | None:
        """Return the monitor object, or None if the device is not a monitor."""
        return self._device if isinstance(self._device, BookooEspressoMonitor) else None

    async def _async_update_data(self) -> None:
        """Fetch data."""

        # device is already connected, return
        if self._device.connected:
            return

        # For the espresso monitor, don't auto-reconnect — connection is managed
        # manually via the Start/Stop extraction buttons to preserve battery life.
        if self.monitor is not None:
            return

        # device is not connected, try to connect
        # Refresh BLEDevice from HA's scanner cache so bleak_retry_connector can be used.
        # Falls back to MAC string if the device hasn't been seen by the scanner yet.
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self._device.mac, connectable=True
        ) or bluetooth.async_ble_device_from_address(
            self.hass, self._device.mac, connectable=False
        )
        if ble_device:
            self._device.address_or_ble_device = ble_device

        try:
            await self._device.connect(setup_tasks=False)
        except (BookooDeviceNotFound, BookooError, TimeoutError) as ex:
            _LOGGER.debug(
                "Could not connect to device: %s, Error: %s",
                self.config_entry.data[CONF_ADDRESS],
                ex,
            )
            self._device.device_disconnected_handler(notify=False)
            return

        # connected, set up background tasks

        if not self._device.process_queue_task or self._device.process_queue_task.done():
            self._device.process_queue_task = (
                self.config_entry.async_create_background_task(
                    hass=self.hass,
                    target=self._device.process_queue(),
                    name="bookoo_process_queue_task",
                )
            )
