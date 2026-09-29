"""Shot recording and monitor auto-connect shared by all Bookoo config entries."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from aiobookoo.exceptions import BookooError

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .coordinator import BookooCoordinator
from .shot_detector import PRESSURE_START, ShotDetector

_LOGGER = logging.getLogger(__name__)

DATA_MANAGER = f"{DOMAIN}_shot_manager"
STORAGE_KEY = f"{DOMAIN}.shots"
STORAGE_VERSION = 1

SAMPLE_INTERVAL = timedelta(seconds=0.2)
SHOTS_KEPT = 10

MONITOR_CONNECT_ATTEMPTS = 5
MONITOR_CONNECT_RETRY = 15  # s
MONITOR_STOP_GRACE = 60  # s after the scale disconnected
MONITOR_IDLE_TIMEOUT = 600  # s without pressure while connected


async def async_get_manager(hass: HomeAssistant) -> ShotManager:
    """Return the shared shot manager, creating and loading it once."""
    if (manager := hass.data.get(DATA_MANAGER)) is None:
        manager = hass.data[DATA_MANAGER] = ShotManager(hass)
        manager.load_task = hass.async_create_task(manager.async_load())
    await manager.load_task
    return manager


class ShotManager:
    """Samples scale and monitor, detects shots and drives the monitor connection."""

    load_task: asyncio.Task

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.detector = ShotDetector()
        self.shots: list[dict[str, Any]] = []  # newest first
        self.auto_monitor = True
        self._store: Store[list[dict[str, Any]]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._scales: list[BookooCoordinator] = []
        self._monitors: list[BookooCoordinator] = []
        self._listeners: list[CALLBACK_TYPE] = []
        self._unsub_tick: CALLBACK_TYPE | None = None
        self._unsub_stop: CALLBACK_TYPE | None = None
        self._connect_task: asyncio.Task | None = None
        self._scale_connected = False
        self._monitor_connected = False
        self._monitor_active_at = 0.0

    async def async_load(self) -> None:
        """Load stored shots."""
        self.shots = await self._store.async_load() or []

    # -- registration ----------------------------------------------------------

    @callback
    def async_register(self, coordinator: BookooCoordinator) -> CALLBACK_TYPE:
        """Track a coordinator; returns the function to untrack it."""
        devices = self._scales if coordinator.scale is not None else self._monitors
        devices.append(coordinator)
        unsub_listener = coordinator.async_add_listener(self._async_device_update)

        @callback
        def unregister() -> None:
            unsub_listener()
            devices.remove(coordinator)
            self._async_device_update()

        return unregister

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Listen for shot updates (new shot, recording started/stopped)."""
        self._listeners.append(update_callback)
        return lambda: self._listeners.remove(update_callback)

    @callback
    def _async_notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    # -- device state ------------------------------------------------------------

    def _connected_scale(self) -> BookooCoordinator | None:
        return next((c for c in self._scales if c.device.connected), None)

    def _connected_monitor(self) -> BookooCoordinator | None:
        return next((c for c in self._monitors if c.device.connected), None)

    @callback
    def _async_device_update(self) -> None:
        """Handle connect/disconnect transitions of any device."""
        scale_connected = self._connected_scale() is not None
        monitor_connected = self._track_monitor_connection()

        if scale_connected != self._scale_connected:
            self._scale_connected = scale_connected
            if scale_connected:
                self._async_scale_connected()
            else:
                self._async_scale_disconnected()

        if scale_connected or monitor_connected:
            if self._unsub_tick is None:
                self._unsub_tick = async_track_time_interval(
                    self.hass, self._async_tick, SAMPLE_INTERVAL, name="bookoo shot sampling"
                )
        elif self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
            self._async_sample()  # lets a running shot finish with no data

    def _track_monitor_connection(self) -> bool:
        """Restart the idle clock whenever the monitor (re)connects."""
        monitor_connected = self._connected_monitor() is not None
        if monitor_connected and not self._monitor_connected:
            self._monitor_active_at = time.monotonic()
        self._monitor_connected = monitor_connected
        return monitor_connected

    # -- sampling ----------------------------------------------------------------

    @callback
    def _async_tick(self, _now: Any) -> None:
        self._async_sample()
        self._async_check_monitor_idle()

    @callback
    def _async_sample(self) -> None:
        scale = self._connected_scale()
        monitor = self._connected_monitor()
        weight = timer = pressure = None
        if scale is not None:
            weight = scale.device.weight
            timer = scale.device.timer
        if monitor is not None:
            pressure = monitor.device.pressure
            if pressure is not None and pressure >= PRESSURE_START:
                self._monitor_active_at = time.monotonic()

        was_recording = self.detector.recording
        shot = self.detector.feed(time.time(), weight, pressure, timer)
        if shot is not None:
            _LOGGER.debug(
                "Shot recorded: %.1f s, %s g, %s bar (%s)",
                shot["duration"], shot["yield_g"], shot["peak_bar"], shot["source"],
            )
            self.shots = [shot, *self.shots][:SHOTS_KEPT]
            self._store.async_delay_save(lambda: self.shots, 5)
        if shot is not None or was_recording != self.detector.recording:
            self._async_notify()

    # -- monitor auto connect ------------------------------------------------------

    @callback
    def _async_scale_connected(self) -> None:
        self._cancel_pending_stop()
        if not self.auto_monitor or not self._monitors:
            return
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = self.hass.async_create_background_task(
                self._async_connect_monitor(), "bookoo monitor auto connect"
            )

    async def _async_connect_monitor(self) -> None:
        for attempt in range(MONITOR_CONNECT_ATTEMPTS):
            if not self._scale_connected or not self.auto_monitor:
                return
            if self._connected_monitor() is not None or not self._monitors:
                return
            _LOGGER.debug("Auto connecting espresso monitor (attempt %s)", attempt + 1)
            try:
                await self._monitors[0].async_start_monitor()
            except (HomeAssistantError, BookooError, TimeoutError) as ex:
                _LOGGER.debug("Monitor auto connect attempt %s failed: %s", attempt + 1, ex)
            if self._connected_monitor() is not None:
                return
            await asyncio.sleep(MONITOR_CONNECT_RETRY)

    @callback
    def _async_scale_disconnected(self) -> None:
        if self.auto_monitor and self._connected_monitor() is not None:
            self._schedule_stop(MONITOR_STOP_GRACE)

    @callback
    def _schedule_stop(self, delay: float) -> None:
        self._cancel_pending_stop()
        self._unsub_stop = async_call_later(self.hass, delay, self._async_stop_after_grace)

    @callback
    def _cancel_pending_stop(self) -> None:
        if self._unsub_stop is not None:
            self._unsub_stop()
            self._unsub_stop = None

    @callback
    def _async_stop_after_grace(self, _now: Any) -> None:
        self._unsub_stop = None
        if self._scale_connected:
            return
        if self.detector.recording:
            self._schedule_stop(30)
            return
        self._async_stop_monitor("scale disconnected")

    @callback
    def _async_check_monitor_idle(self) -> None:
        # The monitor may have connected without its coordinator notifying yet.
        if not self._track_monitor_connection() or self.detector.recording:
            return
        if time.monotonic() - self._monitor_active_at > MONITOR_IDLE_TIMEOUT:
            self._monitor_active_at = time.monotonic()  # don't fire again every tick
            self._async_stop_monitor("idle timeout")

    @callback
    def _async_stop_monitor(self, reason: str) -> None:
        if (monitor := self._connected_monitor()) is None:
            return
        _LOGGER.debug("Stopping espresso monitor: %s", reason)

        async def stop() -> None:
            try:
                await monitor.async_stop_monitor()
            except (BookooError, TimeoutError) as ex:
                _LOGGER.debug("Stopping espresso monitor failed: %s", ex)

        self.hass.async_create_background_task(stop(), "bookoo monitor auto stop")

    def set_auto_monitor(self, enabled: bool) -> None:
        """Enable or disable connecting the monitor together with the scale."""
        self.auto_monitor = enabled
        if enabled and self._scale_connected:
            self._async_scale_connected()
        if not enabled:
            self._cancel_pending_stop()

