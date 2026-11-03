"""Sensors for ЕИРЦ СПб meter readings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EircSpbCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create one sensor per meter register."""
    coordinator: EircSpbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for account_id, payload in coordinator.data.items():
        account = payload["account"]
        entities.append(EircCurrentBillSensor(coordinator, account_id, account))
        for meter in payload.get("meters", []):
            meter_ref = meter.get("id") or {}
            if not isinstance(meter_ref, dict) or not meter_ref.get("registration"):
                continue
            for indication in meter.get("indications") or []:
                if indication.get("meterScaleId") is None:
                    continue
                entities.append(
                    EircMeterSensor(coordinator, account_id, account, meter, indication)
                )
    async_add_entities(entities)


class EircMeterSensor(CoordinatorEntity[EircSpbCoordinator], SensorEntity):
    """One sensor for a single meter register."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: EircSpbCoordinator, account_id: str,
        account: dict[str, Any], meter: dict[str, Any], indication: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account_id
        self._registration = str(meter["id"]["registration"])
        self._scale_id = indication["meterScaleId"]
        self._account_register = str(
            account.get("tenancy", {}).get("register", account_id)
        )
        subservice_id = meter.get("subserviceId")
        utility = (
            (meter.get("subservice") or {}).get("utility") or meter.get("utility") or ""
        ).upper()
        if not utility:
            utility = {
                54179: "ELECTRICITY",
                4599: "WATER",
                4600: "WATER",
            }.get(subservice_id, "")
        meter_name = (
            meter.get("name") or (meter.get("subservice") or {}).get("name")
            or {
                54179: "Электроэнергия",
                4600: "Холодная вода",
                4599: "Горячая вода",
            }.get(subservice_id, {
                "WATER": "Водоснабжение", "ELECTRICITY": "Электроэнергия",
                "GAS": "Газ",
            }.get(utility, "Счётчик"))
        )
        scale_name = indication.get("scaleName")
        self._attr_name = (
            f"{meter_name} — {scale_name}"
            if scale_name and scale_name not in meter_name else meter_name
        )
        self._attr_unique_id = (
            f"{DOMAIN}_{account_id}_{self._registration}_{self._scale_id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id)},
            name=account.get("alias") or f"Лицевой счёт {self._account_register}",
            manufacturer="ЕИРЦ Санкт-Петербурга",
            model="Личный кабинет IKUS",
        )
        if utility == "ELECTRICITY":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif utility == "WATER":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_native_unit_of_measurement = indication.get("unit")

    @property
    def native_value(self) -> int | float | None:
        indication = self._current_indication()
        return indication.get("previousReading") if indication else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = self.coordinator.data.get(self._account_id, {})
        account = payload.get("account", {})
        meter = self._current_meter() or {}
        indication = self._current_indication() or {}
        period = payload.get("reading_period", {})
        acceptance = period.get("acceptanceParameters", {})
        meter_ref = meter.get("id") or {}
        return {
            "account_id": self._account_id,
            "account_register": self._account_register,
            "meter_registration": self._registration,
            "meter_serial": meter.get("serial"),
            "meter_status": meter.get("status"),
            "provider_id": meter_ref.get("provider") if isinstance(meter_ref, dict) else None,
            "scale_id": self._scale_id,
            "scale_name": indication.get("scaleName"),
            "last_reading_date": indication.get("previousReadingDate"),
            "reading_period": acceptance.get("name"),
            "reading_deadline": acceptance.get("deadLine"),
            "reading_forbidden": period.get("forbidden"),
            "account_alias": account.get("alias"),
        }

    def _current_meter(self) -> dict[str, Any] | None:
        payload = self.coordinator.data.get(self._account_id, {})
        for meter in payload.get("meters", []):
            meter_ref = meter.get("id") or {}
            if isinstance(meter_ref, dict) and str(meter_ref.get("registration")) == self._registration:
                return meter
        return None

    def _current_indication(self) -> dict[str, Any] | None:
        meter = self._current_meter()
        if meter is None:
            return None
        for indication in meter.get("indications") or []:
            if indication.get("meterScaleId") == self._scale_id:
                return indication
        return None

 
class EircCurrentBillSensor(CoordinatorEntity[EircSpbCoordinator], SensorEntity):
    """Current bill amount for one personal account."""

    _attr_has_entity_name = True
    _attr_name = "Сумма текущего счёта"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "RUB"

    def __init__(
        self, coordinator: EircSpbCoordinator, account_id: str, account: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account_id
        self._account_register = str(
            account.get("tenancy", {}).get("register", account_id)
        )
        self._attr_unique_id = f"{DOMAIN}_{account_id}_current_bill"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id)},
            name=account.get("alias") or f"Лицевой счёт {self._account_register}",
            manufacturer="ЕИРЦ Санкт-Петербурга",
            model="Личный кабинет IKUS",
        )

    @property
    def native_value(self) -> int | float | None:
        bill = self.coordinator.data.get(self._account_id, {}).get("bill", {})
        return bill.get("amount")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bill = self.coordinator.data.get(self._account_id, {}).get("bill", {})
        return {
            "account_id": self._account_id,
            "account_register": self._account_register,
            "bill_id": bill.get("id"),
            "bill_timestamp": bill.get("timestamp"),
        }
