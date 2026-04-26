"""Sensor platform for Vandebron Energie."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UsageData
from .const import DOMAIN
from .coordinator import VandebronEnergieCoordinator

CURRENCY_EUR = "EUR"


@dataclass(frozen=True, kw_only=True)
class VandebronSensorEntityDescription(SensorEntityDescription):
    """Vandebron sensor description with a value extractor and optional flag."""

    value_fn: Any = None
    requires_gas: bool = False


SENSORS: tuple[VandebronSensorEntityDescription, ...] = (
    # ------------------------------------------------------------------
    # Daily consumption (most recent day with real meter data)
    # ------------------------------------------------------------------
    VandebronSensorEntityDescription(
        key="electricity_today_peak",
        translation_key="electricity_today_peak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda d: round(d.electricity_peak_kwh, 3),
    ),
    VandebronSensorEntityDescription(
        key="electricity_today_off_peak",
        translation_key="electricity_today_off_peak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda d: round(d.electricity_off_peak_kwh, 3),
    ),
    VandebronSensorEntityDescription(
        key="electricity_today_total",
        translation_key="electricity_today_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=3,
        value_fn=lambda d: round(d.electricity_peak_kwh + d.electricity_off_peak_kwh, 3),
    ),
    # ------------------------------------------------------------------
    # Monthly consumption (month-to-date, real meter data)
    # ------------------------------------------------------------------
    VandebronSensorEntityDescription(
        key="electricity_month_peak",
        translation_key="electricity_month_peak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: round(d.electricity_month_peak_kwh, 1),
    ),
    VandebronSensorEntityDescription(
        key="electricity_month_off_peak",
        translation_key="electricity_month_off_peak",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: round(d.electricity_month_off_peak_kwh, 1),
    ),
    VandebronSensorEntityDescription(
        key="electricity_month_total",
        translation_key="electricity_month_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: round(
            d.electricity_month_peak_kwh + d.electricity_month_off_peak_kwh, 1
        ),
    ),
    # ------------------------------------------------------------------
    # Expected monthly consumption (SJV annual profile ÷ 12)
    # ------------------------------------------------------------------
    VandebronSensorEntityDescription(
        key="electricity_month_expected",
        translation_key="electricity_month_expected",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=0,
        value_fn=lambda d: d.electricity_month_expected_kwh,
    ),
    # ------------------------------------------------------------------
    # Costs (variable + proportional fixed, incl. 9% VAT)
    # ------------------------------------------------------------------
    VandebronSensorEntityDescription(
        key="electricity_today_cost",
        translation_key="electricity_today_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.electricity_today_cost_eur,
    ),
    VandebronSensorEntityDescription(
        key="electricity_month_cost",
        translation_key="electricity_month_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.electricity_month_cost_eur,
    ),
    VandebronSensorEntityDescription(
        key="electricity_month_expected_cost",
        translation_key="electricity_month_expected_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=CURRENCY_EUR,
        suggested_display_precision=2,
        value_fn=lambda d: d.electricity_month_expected_cost_eur,
    ),
    # ------------------------------------------------------------------
    # Gas (only created when the account has a gas contract)
    # ------------------------------------------------------------------
    VandebronSensorEntityDescription(
        key="gas_today",
        translation_key="gas_today",
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=3,
        requires_gas=True,
        value_fn=lambda d: round(d.gas_m3, 3) if d.gas_m3 is not None else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Vandebron Energie sensors from a config entry."""
    coordinator: VandebronEnergieCoordinator = hass.data[DOMAIN][entry.entry_id]

    has_gas = coordinator.data is not None and coordinator.data.has_gas

    async_add_entities(
        VandebronSensor(coordinator, description)
        for description in SENSORS
        if not description.requires_gas or has_gas
    )


class VandebronSensor(
    CoordinatorEntity[VandebronEnergieCoordinator], SensorEntity
):
    """Representation of a Vandebron Energie sensor."""

    _attr_has_entity_name = True
    entity_description: VandebronSensorEntityDescription

    def __init__(
        self,
        coordinator: VandebronEnergieCoordinator,
        description: VandebronSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="Vandebron",
            manufacturer="Vandebron",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the date the reading is from (may be yesterday due to API lag)."""
        if self.coordinator.data is None or self.coordinator.data.data_date is None:
            return {}
        return {"data_date": self.coordinator.data.data_date.isoformat()}
