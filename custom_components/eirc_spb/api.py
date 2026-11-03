"""Async API client for the IKUS / ЕИРЦ СПб service."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .const import (
    ACCOUNTS_URL,
    AUTH_URL,
    COOKIE_URL,
    CURRENT_BILL_URL,
    METERS_INFO_URL,
    READING_PERIOD_URL,
    SUBMIT_READING_URL,
)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


class EircApiError(HomeAssistantError):
    """General API error."""


class EircAuthError(EircApiError):
    """Authentication has expired or is invalid."""


class TwoFactorAuthRequired(EircApiError):
    """IKUS requires an additional authentication factor."""

    def __init__(self, transaction_id: str, methods: list[str]) -> None:
        super().__init__("IKUS requires two-factor authentication")
        self.transaction_id = transaction_id
        self.methods = methods


class EircApiClient:
    """Async client for the endpoints used by this integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        *,
        session_cookie: str | None = None,
        token_auth: str | None = None,
        token_verify: str | None = None,
    ) -> None:
        self._session = async_get_clientsession(hass)
        self._username = username
        self._password = password
        self.session_cookie = session_cookie
        self.token_auth = token_auth
        self.token_verify = token_verify

    @property
    def token_data(self) -> dict[str, str | None]:
        """Authentication state to persist in the config entry."""
        return {
            "session_cookie": self.session_cookie,
            "token_auth": self.token_auth,
            "token_verify": self.token_verify,
        }

    def _headers(self, authenticated: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
            "Customer": "ikus-spb",
        }
        if self.session_cookie:
            headers["Cookie"] = f"session-cookie={self.session_cookie}"
        if authenticated and self.token_auth:
            headers["Authorization"] = f"Bearer {self.token_auth}"
        if authenticated and self.token_verify:
            headers["Auth-Verification"] = self.token_verify
        return headers

    async def _decode(self, response: aiohttp.ClientResponse) -> Any:
        if response.status == 204:
            return None
        try:
            payload = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            payload = await response.text()
        if response.status in (401, 403):
            raise EircAuthError("Сессия IKUS истекла или требует повторной авторизации")
        if not 200 <= response.status < 300:
            message = payload.get("message") if isinstance(payload, dict) else payload
            raise EircApiError(
                f"Ошибка IKUS API (HTTP {response.status}): {message or 'нет описания'}"
            )
        return payload

    async def _request(
        self, method: str, url: str, *, json: Any = None, authenticated: bool = True
    ) -> Any:
        try:
            async with self._session.request(
                method, url, headers=self._headers(authenticated), json=json, timeout=_TIMEOUT
            ) as response:
                return await self._decode(response)
        except EircApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircApiError(f"Не удалось связаться с IKUS: {err}") from err

    async def async_fetch_session_cookie(self) -> None:
        """Obtain the session cookie required by IKUS."""
        try:
            async with self._session.get(
                COOKIE_URL, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT
            ) as response:
                await self._decode(response)
                cookie = response.cookies.get("session-cookie")
                if cookie is None:
                    cookie = self._session.cookie_jar.filter_cookies(
                        URL("https://ikus.pesc.ru")
                    ).get("session-cookie")
                if cookie is None:
                    raise EircApiError("IKUS не вернул обязательную session-cookie")
                self.session_cookie = cookie.value
        except EircApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircApiError(f"Не удалось получить сессию IKUS: {err}") from err

    async def async_authenticate(self) -> None:
        """Authenticate using phone and password."""
        if not self.session_cookie:
            await self.async_fetch_session_cookie()
        payload = {"type": "PHONE", "login": self._username, "password": self._password}
        try:
            async with self._session.post(
                AUTH_URL,
                headers=self._headers(authenticated=False),
                json=payload,
                timeout=_TIMEOUT,
            ) as response:
                data = await self._decode_auth_response(response)
        except EircApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EircApiError(f"Ошибка авторизации в IKUS: {err}") from err
        self.token_auth = data.get("auth")
        self.token_verify = data.get("verified")
        if not self.token_auth:
            raise EircApiError("IKUS не вернул токен авторизации")

    async def _decode_auth_response(self, response: aiohttp.ClientResponse) -> dict[str, Any]:
        if response.status == 424:
            data = await response.json(content_type=None)
            transaction_id = data.get("transactionId")
            if not transaction_id:
                raise EircApiError("IKUS запросил 2FA, но не вернул transactionId")
            raise TwoFactorAuthRequired(str(transaction_id), list(data.get("types", [])))
        data = await self._decode(response)
        if not isinstance(data, dict):
            raise EircApiError("Неожиданный ответ авторизации IKUS")
        return data

    async def async_send_email_code(self, transaction_id: str) -> None:
        """Request an email verification code."""
        url = (
            "https://ikus.pesc.ru/api/v7/users/"
            f"{quote(transaction_id, safe='')}/email/check/confirmation/send"
        )
        await self._request("POST", url, authenticated=False)

    async def async_verify_email_code(self, transaction_id: str, code: str) -> None:
        """Verify an email confirmation code."""
        url = (
            "https://ikus.pesc.ru/api/v7/users/"
            f"{quote(transaction_id, safe='')}/email/check/verification"
        )
        data = await self._request(
            "POST", url, json={"code": code}, authenticated=False
        )
        if not isinstance(data, dict) or not data.get("auth") or not data.get("verified"):
            raise EircApiError("IKUS вернул неполный ответ подтверждения")
        self.token_auth = data["auth"]
        self.token_verify = data["verified"]

    async def async_get_accounts(self) -> list[dict[str, Any]]:
        """Fetch available personal accounts."""
        data = await self._request("GET", ACCOUNTS_URL)
        if not isinstance(data, list):
            raise EircApiError("IKUS вернул неожиданный список лицевых счетов")
        return data

    async def async_get_meters(self, account_id: int | str) -> list[dict[str, Any]]:
        """Fetch meter readings for an account."""
        url = METERS_INFO_URL.format(account_id=quote(str(account_id), safe=""))
        data = await self._request("GET", url)
        if isinstance(data, dict):
            data = data.get("meters", [])
        if not isinstance(data, list):
            raise EircApiError("IKUS вернул неожиданный формат данных счётчиков")
        return data

    async def async_get_reading_period(self, account_id: int | str) -> dict[str, Any]:
        """Fetch the current meter-reading acceptance period."""
        url = READING_PERIOD_URL.format(account_id=quote(str(account_id), safe=""))
        data = await self._request("GET", url)
        return data if isinstance(data, dict) else {}

    async def async_get_current_bill(self, account_id: int | str) -> dict[str, Any]:
        """Return the current bill amount and identifier, when available."""
        url = CURRENT_BILL_URL.format(account_id=quote(str(account_id), safe=""))
        data = await self._request("GET", url)
        return data if isinstance(data, dict) else {}

    async def async_submit_reading(
        self,
        account_id: int | str,
        registration: str,
        scale_id: int,
        value: float,
    ) -> Any:
        """Submit one meter reading. The caller must verify it with a fresh GET."""
        url = SUBMIT_READING_URL.format(
            account_id=quote(str(account_id), safe=""),
            registration=quote(str(registration), safe=""),
        )
        return await self._request(
            "POST",
            url,
            json=[{"scaleId": scale_id, "value": value}],
        )
