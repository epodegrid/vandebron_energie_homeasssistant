"""Binary sensor platform for Vandebron Energie.

Provides a peak/off-peak hour indicator that flips precisely at
07:00 and 23:00 Amsterdam time on weekdays.

Dutch tariff schedule:
  Peak  (Piek): Mon–Fri 07:00–23:00 CET/CEST
  Off-peak (Dal): Mon–Fri 00:00–07:00 & 23:00–24:00 + all weekends
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change

from .api import is_nl_peak_hour
from .const import DOMAIN
from .coordinator import VandebronEnergieCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the peak-hour binary sensor."""
    coordinator: VandebronEnergieCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VandebronPeakHourSensor(coordinator)])


class VandebronPeakHourSensor(BinarySensorEntity):
    """ON during peak tariff hours (Mon–Fri 07:00–23:00 Amsterdam time)."""

    _attr_has_entity_name = True
    _attr_translation_key = "is_peak_hour"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: VandebronEnergieCoordinator) -> None:
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_is_peak_hour"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="Vandebron",
            manufacturer="Vandebron",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_is_on = is_nl_peak_hour()
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Register listeners that fire exactly at the tariff transition times."""

        @callback
        def _on_transition(_now) -> None:
            self._attr_is_on = is_nl_peak_hour()
            self.async_write_ha_state()

        # Fire at 07:00 (off-peak → peak on weekdays) and
        # 23:00 (peak → off-peak on weekdays).
        # On weekends the callback still fires but is_nl_peak_hour() returns
        # False both before and after, so the state stays OFF correctly.
        self.async_on_remove(
            async_track_time_change(
                self.hass, _on_transition, hour=[7, 23], minute=0, second=0
            )
        )

    @property
    def is_on(self) -> bool:
        """Return current peak state, always recalculated from the clock."""
        return is_nl_peak_hour()

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the tariff window as an attribute for clarity."""
        return {"tariff_window": "Mon–Fri 07:00–23:00 Amsterdam"}
