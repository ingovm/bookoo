"""Sensor platform for Bookoo."""

from collections.abc import Callable  # noqa: I001
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from aiobookoo.bookooscale import BookooDeviceState, BookooScale
from aiobookoo.bookoomonitor import BookooEspressoMonitor
from aiobookoo.const import UnitMass as BookooUnitOfMass
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfMass, UnitOfPressure, UnitOfVolumeFlowRate, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BookooConfigEntry
from .entity import BookooEntity, BookooShotEntity
from .shot import DATA_MANAGER

# Coordinator is used to centralize the data updates
PARALLEL_UPDATES = 0


@dataclass(kw_only=True, frozen=True)
class BookooSensorEntityDescription(SensorEntityDescription):
    """Description for Bookoo sensor entities."""

    value_fn: Callable[[BookooScale | BookooEspressoMonitor], int | float | None]


@dataclass(kw_only=True, frozen=True)
class BookooDynamicUnitSensorEntityDescription(BookooSensorEntityDescription):
    """Description for Bookoo sensor entities with dynamic units."""

    unit_fn: Callable[[BookooDeviceState], str] | None = None


SCALE_SENSORS: tuple[BookooSensorEntityDescription, ...] = (
    BookooDynamicUnitSensorEntityDescription(
        key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.GRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.weight,
    ),
    BookooDynamicUnitSensorEntityDescription(
        key="flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.MILLILITERS_PER_SECOND,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.flow_rate,
    ),
    BookooDynamicUnitSensorEntityDescription(
        key="timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.timer,
    ),
)

SCALE_RESTORE_SENSORS: tuple[BookooSensorEntityDescription, ...] = (
    BookooSensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: (
            device.device_state.battery_level if device.device_state else None
        ),
    ),
)

MONITOR_SENSORS: tuple[BookooSensorEntityDescription, ...] = (
    BookooSensorEntityDescription(
        key="pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=2,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.pressure,
    ),
)

MONITOR_RESTORE_SENSORS: tuple[BookooSensorEntityDescription, ...] = (
    BookooSensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.battery,
    ),
)

# Last, second-to-last and third-to-last shot.
SHOT_SENSORS: tuple[SensorEntityDescription, ...] = tuple(
    SensorEntityDescription(
        key=f"shot_{number}",
        translation_key=f"shot_{number}",
        device_class=SensorDeviceClass.TIMESTAMP,
    )
    for number in (1, 2, 3)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BookooConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""

    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    if coordinator.scale is not None:
        entities.extend(
            BookooSensor(coordinator, description) for description in SCALE_SENSORS
        )
        entities.extend(
            BookooRestoreSensor(coordinator, description)
            for description in SCALE_RESTORE_SENSORS
        )
        manager = hass.data[DATA_MANAGER]
        entities.extend(
            BookooShotSensor(coordinator, manager, description, index)
            for index, description in enumerate(SHOT_SENSORS)
        )
    else:
        entities.extend(
            BookooSensor(coordinator, description) for description in MONITOR_SENSORS
        )
        entities.extend(
            BookooRestoreSensor(coordinator, description)
            for description in MONITOR_RESTORE_SENSORS
        )

    async_add_entities(entities)


class BookooSensor(BookooEntity, SensorEntity):
    """Representation of a Bookoo sensor."""

    entity_description: BookooDynamicUnitSensorEntityDescription

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit of measurement of this entity."""
        if (
            isinstance(self._scale, BookooScale)
            and self._scale.device_state is not None
            and isinstance(self.entity_description, BookooDynamicUnitSensorEntityDescription)
            and self.entity_description.unit_fn is not None
        ):
            return self.entity_description.unit_fn(self._scale.device_state)
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> int | float | None:
        """Return the state of the entity."""
        return self.entity_description.value_fn(self._scale)


class BookooRestoreSensor(BookooEntity, RestoreSensor):
    """Representation of a Bookoo sensor with restore capabilities."""

    entity_description: BookooSensorEntityDescription
    _restored_data: SensorExtraStoredData | None = None

    async def async_added_to_hass(self) -> None:
        """Handle entity which will be added."""
        await super().async_added_to_hass()

        self._restored_data = await self.async_get_last_sensor_data()
        if self._restored_data is not None:
            self._attr_native_value = self._restored_data.native_value
            self._attr_native_unit_of_measurement = (
                self._restored_data.native_unit_of_measurement
            )

        value = self.entity_description.value_fn(self._scale)
        if value is not None:
            self._attr_native_value = value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        value = self.entity_description.value_fn(self._scale)
        if value is not None:
            self._attr_native_value = value
        self._async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return super().available or self._restored_data is not None


class BookooShotSensor(BookooShotEntity, SensorEntity):
    """A recorded shot: state is its start time, the profile is in the attributes."""

    # The profile is only needed by the dashboard; keep it out of the recorder.
    _unrecorded_attributes = frozenset({"samples"})

    def __init__(self, coordinator, manager, description, index: int) -> None:
        """Initialize the shot sensor."""
        super().__init__(coordinator, manager, description)
        self._index = index

    @property
    def _shot(self) -> dict[str, Any] | None:
        shots = self._manager.shots
        return shots[self._index] if self._index < len(shots) else None

    @property
    def native_value(self) -> datetime | None:
        """Return the start of the shot."""
        if (shot := self._shot) is None:
            return None
        return datetime.fromtimestamp(shot["start"], tz=UTC)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return shot key figures and the sampled profile."""
        if (shot := self._shot) is None:
            return None
        return {key: shot[key] for key in ("duration", "yield_g", "peak_bar", "source", "samples")}
