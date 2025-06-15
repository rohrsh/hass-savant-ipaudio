from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import aiohttp
import logging
from datetime import timedelta
import asyncio

_LOGGER = logging.getLogger(__name__)

class SavantDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for Savant IP Audio data."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        auth: aiohttp.BasicAuth,
        update_interval: timedelta = timedelta(seconds=5)
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Savant IP Audio",
            update_interval=update_interval,
        )
        self.host = host
        self.auth = auth
        self.session = async_get_clientsession(hass)
        self._data = None
        self._volume_refresh_task = None

    async def _async_update_data(self):
        """Fetch data from the Savant device."""
        try:
            # Fetch both status and audio ports data
            async with self.session.get(
                f"http://{self.host}/cgi-bin/avswitch?action=showAllAudioPortsInJson",
                auth=self.auth
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._data = data
                return data
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Savant device: {err}")

    async def _debounced_volume_refresh(self):
        """Debounced refresh for volume changes."""
        if self._volume_refresh_task:
            self._volume_refresh_task.cancel()
        self._volume_refresh_task = asyncio.create_task(self._delayed_volume_refresh())

    async def _delayed_volume_refresh(self):
        """Delayed refresh for volume changes."""
        await asyncio.sleep(0.3)  # 300ms debounce for volume changes
        await self.async_request_refresh()

    async def async_set_volume(self, port: int, volume: float) -> None:
        """Set volume for a zone with optimistic update."""
        try:
            level = int((volume * 80) - 80)
            payload = {f"output{port}.volume": str(level)}
            
            # Optimistically update the data
            if self._data and "outputs" in self._data:
                for output in self._data["outputs"]:
                    if output["port"] == port:
                        output["volume"] = level
                        break
            
            # Make the actual API call
            async with self.session.post(
                f"http://{self.host}/cgi-bin/avswitch?action=setAudio",
                data=payload,
                auth=self.auth
            ) as resp:
                resp.raise_for_status()
            
            # Request a refresh with custom debounce for volume
            await self._debounced_volume_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set volume: %s", err)
            raise

    async def async_set_mute(self, port: int, mute: bool) -> None:
        """Set mute state for a zone with optimistic update."""
        try:
            val = "muted" if mute else "not-muted"
            payload = {f"output{port}.mute": val}
            
            # Optimistically update the data
            if self._data and "outputs" in self._data:
                for output in self._data["outputs"]:
                    if output["port"] == port:
                        output["mute"] = mute
                        break
            
            # Make the actual API call
            async with self.session.post(
                f"http://{self.host}/cgi-bin/avswitch?action=setAudio",
                data=payload,
                auth=self.auth
            ) as resp:
                resp.raise_for_status()
            
            # Request a refresh to confirm the change
            await self.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set mute: %s", err)
            raise

    async def async_set_source(self, port: int, source_id: int) -> None:
        """Set source for a zone with optimistic update."""
        try:
            payload = {f"output{port}.inputsrc": str(source_id)}
            
            # Optimistically update the data
            if self._data and "outputs" in self._data:
                for output in self._data["outputs"]:
                    if output["port"] == port:
                        output["inputsrc"] = source_id
                        break
            
            # Make the actual API call
            async with self.session.post(
                f"http://{self.host}/cgi-bin/avswitch?action=setAudio",
                data=payload,
                auth=self.auth
            ) as resp:
                resp.raise_for_status()
            
            # Request a refresh to confirm the change
            await self.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to set source: %s", err)
            raise 