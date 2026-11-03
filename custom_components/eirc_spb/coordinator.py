"""Coordinator for IKUS account and meter data."""
from __future__ import annotations

import html
import logging
from datetime import timedelta
from urllib.parse import quote

from homeassistant.components import persistent_notification
from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EircApiClient, EircApiError, EircAuthError
from .const import (
    CONF_PASSWORD,
    CONF_SELECTED_ACCOUNTS,
    CONF_SESSION_COOKIE,
    CONF_TOKEN_AUTH,
    CONF_TOKEN_VERIFY,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EircSpbCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Fetch selected accounts and their meter readings."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.entry = entry
        self.pending_readings: dict[tuple[str, str, int], float | int | None] = {}
        self._bill_store = Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_seen_bills")
        self._seen_bill_ids: dict[str, str] | None = None
        self.client = EircApiClient(
            hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD],
            session_cookie=entry.data.get(CONF_SESSION_COOKIE),
            token_auth=entry.data.get(CONF_TOKEN_AUTH),
            token_verify=entry.data.get(CONF_TOKEN_VERIFY),
        )


    async def _async_process_current_bills(
        self, accounts: dict[str, dict]
    ) -> None:
        """Notify once when the current bill ID changes for an account."""
        current_ids = {
            account_id: str(payload["bill"]["id"])
            for account_id, payload in accounts.items()
            if payload.get("bill", {}).get("id") not in (None, "")
        }

        if self._seen_bill_ids is None:
            try:
                stored = await self._bill_store.async_load()
            except Exception:
                _LOGGER.exception(
                    "Не удалось прочитать историю квитанций ЕИРЦ; "
                    "текущие счета будут приняты за исходную историю"
                )
                stored = None

            if (
                isinstance(stored, dict)
                and stored.get("initialized") is True
                and isinstance(stored.get("bill_ids"), dict)
            ):
                self._seen_bill_ids = {
                    str(account_id): str(bill_id)
                    for account_id, bill_id in stored["bill_ids"].items()
                    if bill_id not in (None, "")
                }
            else:
                # First setup: establish a silent baseline, never notify about
                # the bill that was already present when the integration loaded.
                self._seen_bill_ids = current_ids.copy()
                await self._async_save_bill_history()
                return

        changed = False
        for account_id, bill_id in current_ids.items():
            previous_id = self._seen_bill_ids.get(account_id)
            if previous_id is None:
                self._seen_bill_ids[account_id] = bill_id
                changed = True
                continue
            if previous_id == bill_id:
                continue

            bill = accounts[account_id].get("bill", {})
            account = accounts[account_id].get("account", {})
            tenancy = account.get("tenancy")
            register = (
                tenancy.get("register") if isinstance(tenancy, dict) else None
            ) or account.get("alias") or "лицевому счёту"
            period = bill.get("period") or bill.get("name") or bill.get("timestamp")
            amount = bill.get("amount")
            details = []
            if period is not None:
                details.append(f"за {html.escape(str(period))}")
            if amount is not None:
                details.append(f"сумма: {html.escape(str(amount))} ₽")
            description = ", ".join(details) or "доступен новый платёжный документ"
            url = self.async_bill_url(account_id, bill_id)
            try:
                persistent_notification.async_create(
                    self.hass,
                    (
                        f"Для {html.escape(str(register))} появился новый документ "
                        f"({description}). "
                        f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                        "Открыть PDF</a>"
                    ),
                    title="ЕИРЦ СПб: новая квитанция",
                    notification_id=(
                        f"eirc_spb_bill_{self.entry.entry_id}_{account_id}_{bill_id}"
                    ),
                )
                self.hass.bus.async_fire(
                    "eirc_spb_new_bill",
                    {
                        "account_id": account_id,
                        "bill_id": bill_id,
                        "period": period,
                        "amount": amount,
                        "url": url,
                    },
                )
            except Exception:
                _LOGGER.exception(
                    "Не удалось создать уведомление о квитанции по одному из счетов"
                )
                continue

            self._seen_bill_ids[account_id] = bill_id
            changed = True

        if changed:
            await self._async_save_bill_history()

    async def _async_save_bill_history(self) -> None:
        """Persist the last processed bill ID for every selected account."""
        try:
            await self._bill_store.async_save(
                {"initialized": True, "bill_ids": self._seen_bill_ids or {}}
            )
        except Exception:
            _LOGGER.exception(
                "Не удалось сохранить историю квитанций ЕИРЦ; "
                "уведомление может повториться после перезапуска"
            )

    def async_bill_url(self, account_id: str, bill_id: str) -> str:
        """Return a 24-hour signed URL for the current bill PDF."""
        path = (
            f"/api/eirc_spb/bill/{self.entry.entry_id}/"
            f"{quote(str(account_id), safe='')}/{quote(str(bill_id), safe='')}"
        )
        return async_sign_path(
            self.hass, path, timedelta(hours=24), use_content_user=True
        )

    async def _async_update_data(self) -> dict[str, dict]:
        selected = {str(value) for value in self.entry.data[CONF_SELECTED_ACCOUNTS]}
        try:
            accounts = await self.client.async_get_accounts()
            result: dict[str, dict] = {}
            for account in accounts:
                account_id = str(account.get("id", ""))
                if account_id not in selected:
                    continue
                meters = await self.client.async_get_meters(account_id)
                try:
                    bill = await self.client.async_get_current_bill(account_id)
                except EircAuthError:
                    raise
                except EircApiError as err:
                    _LOGGER.debug("Current bill unavailable for %s: %s", account_id, err)
                    bill = {}
                try:
                    period = await self.client.async_get_reading_period(account_id)
                except EircAuthError:
                    raise
                except EircApiError as err:
                    _LOGGER.debug("Reading period unavailable for %s: %s", account_id, err)
                    period = {}
                result[account_id] = {
                    "account": account, "meters": meters, "reading_period": period,
                    "bill": bill
                }
            if not result:
                raise UpdateFailed("Не удалось найти выбранные лицевые счета в IKUS")
            await self._async_process_current_bills(result)
            token_data = self.client.token_data
            if any(self.entry.data.get(key) != value for key, value in token_data.items()):
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, **token_data}
                )
            return result
        except EircAuthError as err:
            raise ConfigEntryAuthFailed(
                "Требуется повторная авторизация в личном кабинете ЕИРЦ"
            ) from err
        except EircApiError as err:
            raise UpdateFailed(f"Ошибка получения данных IKUS: {err}") from err
