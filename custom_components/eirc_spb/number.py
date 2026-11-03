"""Number entities for entering IKUS meter readings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EircSpbCoordinator
from .helpers import meter_label, meter_utility


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create a reading input for each meter register."""
    coordinator: EircSpbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    for account_id, payload in coordinator.data.items():
        account = payload["account"]
        for meter in payload.get("meters", []):
            meter_ref = meter.get("id") or {}
            if not isinstance(meter_ref, dict) or not meter_ref.get("registration"):
                continue
            for indication in meter.get("indications") or []:
                scale_id = indication.get("meterScaleId")
                if scale_id is None:
                    continue
                key = (account_id, str(meter_ref["registration"]), scale_id)
                current = indication.get("previousReading")
                coordinator.pending_readings.setdefault(key, current)
                entities.append(
                    EircReadingNumber(
                        coordinator, account_id, account, meter, indication
                    )
                )
    async_add_entities(entities)


class EircReadingNumber(CoordinatorEntity[EircSpbCoordinator], NumberEntity):
    """Input field holding the next reading to submit."""

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 999999999
    _attr_native_step = 0.001

    def __init__(
        self,
        coordinator: EircSpbCoordinator,
        account_id: str,
        account: dict[str, Any],
        meter: dict[str, Any],
        indication: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        registration = str(meter["id"]["registration"])
        scale_id = indication["meterScaleId"]
        self._key = (account_id, registration, scale_id)
        self._attr_name = f"{meter_label(meter, indication)}: ввод показаний"
        self._attr_unique_id = f"{DOMAIN}_{account_id}_{registration}_{scale_id}_reading_input"
        self._attr_native_value = indication.get("previousReading") or 0
        self._attr_native_unit_of_measurement = indication.get("unit")
        utility = meter_utility(meter)
        if utility == "ELECTRICITY":
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_native_step = 0.01
        elif utility == "WATER":
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
            self._attr_native_step = 0.001
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id)},
            name=account.get("alias") or f"Лицевой счёт {account_id}",
            manufacturer="ЕИРЦ Санкт-Петербурга",
            model="Личный кабинет IKUS",
        )

    async def async_set_native_value(self, value: float) -> None:
        """Store the value that the submit button will send."""
        self.coordinator.pending_readings[self._key] = value
        self._attr_native_value = value
        self.async_write_ha_state()
