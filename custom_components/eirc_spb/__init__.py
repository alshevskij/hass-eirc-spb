"""The ЕИРЦ СПб integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EircSpbCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BUTTON]


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
