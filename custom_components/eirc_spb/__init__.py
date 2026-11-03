"""The ЕИРЦ СПб integration."""
from __future__ import annotations

import logging
import re

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv

from .api import EircApiError, EircAuthError
from .const import DOMAIN
from .coordinator import EircSpbCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
]


class EircBillView(HomeAssistantView):
    """Serve the current bill PDF through a signed Home Assistant URL."""

    url = "/api/eirc_spb/bill/{entry_id}/{account_id}/{bill_id}"
    name = "api:eirc_spb:bill"
    requires_auth = True

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        account_id: str,
        bill_id: str,
    ) -> web.Response:
        hass = request.app[KEY_HASS]
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise web.HTTPNotFound()

        coordinator: EircSpbCoordinator = hass.data[DOMAIN][entry_id]
        payload = coordinator.data.get(account_id)
        if payload is None:
            raise web.HTTPNotFound()
        current_bill = payload.get("bill", {})
        if str(current_bill.get("id", "")) != bill_id:
            # IKUS exposes only the current bill. Do not let a stale URL
            # accidentally return a different document.
            raise web.HTTPNotFound()

        try:
            content = await coordinator.client.async_get_bill_pdf(account_id, bill_id)
        except EircAuthError as err:
            _LOGGER.error("Авторизация IKUS не удалась при получении PDF квитанции")
            raise web.HTTPBadGateway() from err
        except EircApiError as err:
            _LOGGER.error("Не удалось получить PDF квитанции ЕИРЦ: %s", err)
            raise web.HTTPBadGateway() from err

        filename = re.sub(r"[^A-Za-z0-9._-]", "_", f"eirc-spb-{bill_id}.pdf")[:120]
        return web.Response(
            body=content,
            content_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the PDF endpoint once for the integration domain."""
    hass.http.register_view(EircBillView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ЕИРЦ СПб from a config entry."""
    if entry.title != "ЕИРЦ СПб: Личный кабинет":
        hass.config_entries.async_update_entry(
            entry, title="ЕИРЦ СПб: Личный кабинет"
        )
    coordinator = EircSpbCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    hass.data[DOMAIN].pop(entry.entry_id)
    return True
