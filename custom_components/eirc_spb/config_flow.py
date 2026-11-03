"""Config flow for ЕИРЦ Санкт-Петербурга."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import config_validation as cv

from .api import EircApiClient, EircApiError, EircAuthError, TwoFactorAuthRequired
from .const import (
    CONF_PASSWORD,
    CONF_SELECTED_ACCOUNTS,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EircSpbConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure an IKUS account."""

    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._client: EircApiClient | None = None
        self._accounts: list[dict[str, Any]] = []
        self._transaction_id: str | None = None
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    def _user_schema(default_username: str = "") -> vol.Schema:
        return vol.Schema({
            vol.Required(CONF_USERNAME, default=default_username): str,
            vol.Required(CONF_PASSWORD): str,
        })

    def _show_auth_error(self, error: str) -> config_entries.ConfigFlowResult:
        if self._reauth_entry is not None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                errors={"base": error},
                description_placeholders={
                    "username": self._reauth_entry.data.get(CONF_USERNAME, "")
                },
            )
        return self.async_show_form(
            step_id="user",
            data_schema=self._user_schema(self._username),
            errors={"base": error},
        )

    async def _authenticate(self) -> config_entries.ConfigFlowResult:
        assert self._client is not None
        try:
            await self._client.async_authenticate()
        except TwoFactorAuthRequired as err:
            self._transaction_id = err.transaction_id
            if "EMAIL" not in err.methods:
                return self._show_auth_error("unsupported_2fa")
            try:
                await self._client.async_send_email_code(err.transaction_id)
            except EircApiError:
                _LOGGER.exception("Unable to request IKUS email verification")
                return self._show_auth_error("cannot_send_code")
            return await self.async_step_2fa()
        except EircAuthError:
            return self._show_auth_error("invalid_auth")
        except EircApiError:
            _LOGGER.exception("IKUS authentication failed")
            return self._show_auth_error("cannot_connect")
        return await self._after_authentication()

    async def _after_authentication(self) -> config_entries.ConfigFlowResult:
        assert self._client is not None
        try:
            self._accounts = await self._client.async_get_accounts()
        except EircApiError:
            _LOGGER.exception("Unable to retrieve IKUS accounts")
            return self._show_auth_error("cannot_fetch_accounts")

        if self._reauth_entry is not None:
            entry = self._reauth_entry
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    **self._client.token_data,
                },
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")
        return await self.async_step_account()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(self._username)
            self._abort_if_unique_id_configured()
            self._client = EircApiClient(self.hass, self._username, self._password)
            return await self._authenticate()
        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(self._username)
        )

    async def async_step_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            assert self._client is not None and self._transaction_id is not None
            try:
                await self._client.async_verify_email_code(
                    self._transaction_id, user_input["code"].strip()
                )
                return await self._after_authentication()
            except EircApiError:
                _LOGGER.warning("IKUS email verification failed")
                return self.async_show_form(
                    step_id="2fa", data_schema=vol.Schema({vol.Required("code"): str}),
                    errors={"base": "invalid_code"},
                )
        return self.async_show_form(
            step_id="2fa", data_schema=vol.Schema({vol.Required("code"): str})
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        options = {
            str(account["id"]): (
                f"{account.get('alias') or 'Лицевой счёт'} "
                f"(№ {account.get('tenancy', {}).get('register', account['id'])})"
            )
            for account in self._accounts
            if account.get("id") is not None and account.get("confirmed", True)
        }
        if not options:
            return self.async_abort(reason="no_accounts")
        if user_input is not None:
            assert self._client is not None
            return self.async_create_entry(
                title="ЕИРЦ Санкт-Петербурга",
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    **self._client.token_data,
                    CONF_SELECTED_ACCOUNTS: user_input[CONF_SELECTED_ACCOUNTS],
                },
            )
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema({
                vol.Required(CONF_SELECTED_ACCOUNTS, default=list(options)):
                    cv.multi_select(options)
            }),
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={
                "username": entry_data.get(CONF_USERNAME, "")
            },
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is None:
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            return await self.async_step_reauth(entry.data if entry else {})
        assert self._reauth_entry is not None
        self._username = self._reauth_entry.data[CONF_USERNAME]
        self._password = user_input[CONF_PASSWORD]
        self._client = EircApiClient(self.hass, self._username, self._password)
        return await self._authenticate()
