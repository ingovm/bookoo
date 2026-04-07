"""Config flow for Bookoo integration."""

import logging
from typing import Any

from aiobookoo.exceptions import BookooDeviceNotFound, BookooError, BookooUnknownDevice
from aiobookoo.helpers import is_bookoo_monitor, is_bookoo_scale
import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_DEVICE_TYPE, CONF_IS_VALID_SCALE, DEVICE_TYPE_MONITOR, DEVICE_TYPE_SCALE, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def _detect_device_type(address: str) -> str:
    """Return DEVICE_TYPE_SCALE or DEVICE_TYPE_MONITOR, or raise BookooUnknownDevice."""
    try:
        await is_bookoo_scale(address)
        return DEVICE_TYPE_SCALE
    except BookooUnknownDevice:
        pass

    await is_bookoo_monitor(address)  # raises BookooUnknownDevice if not a monitor either
    return DEVICE_TYPE_MONITOR


class BookooConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for bookoo."""

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered: dict[str, Any] = {}
        self._discovered_devices: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""

        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input[CONF_ADDRESS]
            try:
                device_type = await _detect_device_type(mac)
            except BookooDeviceNotFound:
                errors["base"] = "device_not_found"
            except BookooError:
                _LOGGER.exception("Error occurred while connecting to the device")
                errors["base"] = "unknown"
            except BookooUnknownDevice:
                return self.async_abort(reason="unsupported_device")
            else:
                await self.async_set_unique_id(format_mac(mac))
                self._abort_if_unique_id_configured()

            if not errors:
                data: dict[str, Any] = {
                    CONF_ADDRESS: mac,
                    CONF_DEVICE_TYPE: device_type,
                }
                if device_type == DEVICE_TYPE_SCALE:
                    data[CONF_IS_VALID_SCALE] = True
                return self.async_create_entry(
                    title=self._discovered_devices[mac],
                    data=data,
                )

        for device in async_discovered_service_info(self.hass):
            self._discovered_devices[device.address] = device.name

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        options = [
            SelectOptionDict(
                value=device_mac,
                label=f"{device_name} ({device_mac})",
            )
            for device_mac, device_name in self._discovered_devices.items()
        ]

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovered Bluetooth device."""

        self._discovered[CONF_ADDRESS] = discovery_info.address
        self._discovered[CONF_NAME] = discovery_info.name

        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()

        try:
            self._discovered[CONF_DEVICE_TYPE] = await _detect_device_type(
                discovery_info.address
            )
        except BookooDeviceNotFound:
            _LOGGER.debug("Device not found during discovery")
            return self.async_abort(reason="device_not_found")
        except BookooError:
            _LOGGER.debug(
                "Error occurred while connecting to the device during discovery",
                exc_info=True,
            )
            return self.async_abort(reason="unknown")
        except BookooUnknownDevice:
            _LOGGER.debug("Unsupported device during discovery")
            return self.async_abort(reason="unsupported_device")

        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle confirmation of Bluetooth discovery."""

        if user_input is not None:
            device_type = self._discovered[CONF_DEVICE_TYPE]
            data: dict[str, Any] = {
                CONF_ADDRESS: self._discovered[CONF_ADDRESS],
                CONF_DEVICE_TYPE: device_type,
            }
            if device_type == DEVICE_TYPE_SCALE:
                data[CONF_IS_VALID_SCALE] = True
            return self.async_create_entry(
                title=self._discovered[CONF_NAME],
                data=data,
            )

        self.context["title_placeholders"] = placeholders = {
            CONF_NAME: self._discovered[CONF_NAME]
        }

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=placeholders,
        )
