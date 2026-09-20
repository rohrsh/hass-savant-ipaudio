"""Savant IP Audio integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import SavantConfigEntry, SavantDataUpdateCoordinator

PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: SavantConfigEntry) -> bool:
    """Set up Savant IP Audio from a config entry."""
    coordinator = SavantDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Entries created before the savantID was stored get it now, so entity
    # unique IDs no longer depend on the status request succeeding at startup.
    if (
        entry.unique_id is None
        and (savant_id := coordinator.status.get("savantID"))
        and not hass.config_entries.async_entry_for_domain_unique_id(
            entry.domain, savant_id
        )
    ):
        hass.config_entries.async_update_entry(entry, unique_id=savant_id)

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SavantConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
