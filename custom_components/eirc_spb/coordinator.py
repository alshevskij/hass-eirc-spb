"""Coordinator for IKUS account and meter data."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
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
        self.client = EircApiClient(
            hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD],
            session_cookie=entry.data.get(CONF_SESSION_COOKIE),
            token_auth=entry.data.get(CONF_TOKEN_AUTH),
            token_verify=entry.data.get(CONF_TOKEN_VERIFY),
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
