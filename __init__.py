from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import logging

DOMAIN = "savant_ipaudio"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Savant IP Audio component."""
    _LOGGER.debug("SAVANT SETUP CALLED")
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Savant IP Audio from a config entry."""
    _LOGGER.debug("SAVANT SETUP ENTRY CALLED with data: %s", entry.data)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, ["media_player"])
        _LOGGER.debug("SAVANT SETUP ENTRY SUCCESS")
        return True
    except Exception as e:
        _LOGGER.error("Failed to setup Savant IP Audio: %s", str(e), exc_info=True)
        return False

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("SAVANT UNLOAD ENTRY CALLED")
    return await hass.config_entries.async_forward_entry_unload(entry, "media_player")
