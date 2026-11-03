"""Buttons for submitting IKUS meter readings."""
from __future__ import annotations

import html
import math
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EircSpbCoordinator
from .helpers import meter_label


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create one submit button for each meter register."""
    coordinator: EircSpbCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for account_id, payload in coordinator.data.items():
        account = payload["account"]
        if payload.get("bill", {}).get("id") not in (None, ""):
            entities.append(
                EircOpenCurrentBillButton(coordinator, account_id, account)
            )
        for meter in payload.get("meters", []):
            meter_ref = meter.get("id") or {}
            if not isinstance(meter_ref, dict) or not meter_ref.get("registration"):
                continue
            for indication in meter.get("indications") or []:
                if indication.get("meterScaleId") is None:
                    continue
                entities.append(
                    EircSubmitReadingButton(
                        coordinator, account_id, account, meter, indication
                    )
                )
    async_add_entities(entities)


class EircSubmitReadingButton(CoordinatorEntity[EircSpbCoordinator], ButtonEntity):
    """Submit a reading and verify it with a fresh API read."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:send"

    def __init__(
        self,
        coordinator: EircSpbCoordinator,
        account_id: str,
        account: dict[str, Any],
        meter: dict[str, Any],
        indication: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account_id
        self._registration = str(meter["id"]["registration"])
        self._scale_id = indication["meterScaleId"]
        self._key = (account_id, self._registration, self._scale_id)
        self._attr_name = f"{meter_label(meter, indication)}: передать показания"
        self._attr_unique_id = (
            f"{DOMAIN}_{account_id}_{self._registration}_{self._scale_id}_submit_reading"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id)},
            name=account.get("alias") or f"Лицевой счёт {account_id}",
            manufacturer="ЕИРЦ Санкт-Петербурга",
            model="Личный кабинет IKUS",
        )

    async def async_press(self) -> None:
        """Submit the pending value once, then verify by reading the meter again."""
        value = self.coordinator.pending_readings.get(self._key)
        if value is None:
            raise HomeAssistantError("Сначала укажите новые показания счётчика")

        current = self._current_reading()
        if current is None:
            raise HomeAssistantError("Не удалось определить текущие показания счётчика")
        if float(value) < float(current):
            raise HomeAssistantError(
                "Новые показания не могут быть меньше текущих. Проверьте введённое значение."
            )

        await self.coordinator.client.async_submit_reading(
            self._account_id, self._registration, self._scale_id, float(value)
        )
        # IKUS may return false even when it has accepted the reading.
        # Never repeat this POST automatically; verify with a fresh GET instead.
        await self.coordinator.async_request_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                "Запрос отправлен, но не удалось проверить результат. "
                "Не нажимайте кнопку повторно до обновления данных."
            )
        accepted = self._current_reading()
        if accepted is None or not math.isclose(
            float(accepted), float(value), rel_tol=0, abs_tol=1e-6
        ):
            raise HomeAssistantError(
                "IKUS пока не подтвердил новые показания при повторном чтении. "
                "Проверьте сенсор перед повторной отправкой."
            )

    def _current_reading(self) -> int | float | None:
        """Return the current reading from coordinator data."""
        payload = self.coordinator.data.get(self._account_id, {})
        for meter in payload.get("meters", []):
            meter_ref = meter.get("id") or {}
            if (
                isinstance(meter_ref, dict)
                and str(meter_ref.get("registration")) == self._registration
            ):
                for indication in meter.get("indications") or []:
                    if indication.get("meterScaleId") == self._scale_id:
                        return indication.get("previousReading")
        return None

 
class EircOpenCurrentBillButton(CoordinatorEntity[EircSpbCoordinator], ButtonEntity):
    """Create a notification with a link to the current bill PDF."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:file-pdf-box"

    def __init__(
        self,
        coordinator: EircSpbCoordinator,
        account_id: str,
        account: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._account_id = account_id
        self._attr_name = "Квитанция: открыть PDF"
        self._attr_unique_id = f"{DOMAIN}_{account_id}_open_current_bill"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account_id)},
            name=account.get("alias") or f"Лицевой счёт {account_id}",
            manufacturer="ЕИРЦ Санкт-Петербурга",
            model="Личный кабинет IKUS",
        )

    async def async_press(self) -> None:
        """Create a persistent notification for the current bill."""
        payload = self.coordinator.data.get(self._account_id, {})
        bill = payload.get("bill", {})
        bill_id = bill.get("id")
        if bill_id in (None, ""):
            raise HomeAssistantError("Для этого лицевого счёта сейчас нет квитанции")

        account = payload.get("account", {})
        tenancy = account.get("tenancy")
        register = (
            tenancy.get("register") if isinstance(tenancy, dict) else None
        ) or account.get("alias") or "выбранного лицевого счёта"
        url = self.coordinator.async_bill_url(self._account_id, str(bill_id))
        details = []
        period = bill.get("period") or bill.get("name") or bill.get("timestamp")
        amount = bill.get("amount")
        if period is not None:
            details.append(f"за {period}")
        if amount is not None:
            details.append(f"сумма: {amount} ₽")
        description = ", ".join(details) or "текущий платёжный документ"
        persistent_notification.async_create(
            self.hass,
            (
                f"Квитанция для {html.escape(str(register))} ({html.escape(description)}). "
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                "Открыть PDF</a>"
            ),
            title="ЕИРЦ СПб: текущая квитанция",
            notification_id=(
                f"eirc_spb_current_bill_{self.coordinator.entry.entry_id}_"
                f"{self._account_id}"
            ),
        )
