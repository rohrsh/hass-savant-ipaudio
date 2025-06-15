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
        update_interval: timedelta = timedelta(seconds=20),
        name: str = "savant_ipaudio",
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=update_interval,
        )
        self.host = host
        self.auth = auth
        self.session = async_get_clientsession(hass)
        self.constants = None  # Will hold model/chassis info
        # Initialize data structure
        self.data = {
            "status": {},
            "av": {"outputs": []},
            "constants": {},
        }
        _LOGGER.debug("Initialized SavantDataUpdateCoordinator for host %s", host)

    async def async_config_entry_first_refresh(self):
        # Fetch constants once at startup
        base = f"http://{self.host}"
        self.constants = await self._fetch_constants(base)
        self.data["constants"] = self.constants
        await super().async_config_entry_first_refresh()

    async def _async_update_data(self) -> dict:
        """Fetch all data from the Savant device in one call (status and AV only)."""
        try:
            _LOGGER.debug("Fetching data from Savant device at %s", self.host)
            base = f"http://{self.host}"
            # Fetch status and audio ports concurrently
            async with asyncio.TaskGroup() as tg:
                status_task = tg.create_task(self._fetch_status(base))
                av_task = tg.create_task(self._fetch_audio_ports(base))
            # Combine the data, reusing constants
            data = {
                "status": status_task.result(),
                "av": av_task.result(),
                "constants": self.constants or {},
            }
            _LOGGER.debug("Successfully fetched all data: %s", data)
            return data
        except Exception as err:
            _LOGGER.error("Error communicating with Savant device: %s", err, exc_info=True)
            raise UpdateFailed(f"Error communicating with Savant device: {err}")

    async def _fetch_status(self, base: str) -> dict:
        """Fetch status from the device."""
        try:
            url = f"{base}/cgi-bin/status?outputType=application/json"
            _LOGGER.debug("Fetching status from %s", url)
            async with self.session.get(url, auth=self.auth) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _LOGGER.debug("Status response: %s", data)
                    return data
                raise Exception(f"Status request failed: {resp.status}")
        except Exception as err:
            _LOGGER.warning("Failed to fetch status: %s", err)
            return {}

    async def _fetch_audio_ports(self, base: str) -> dict:
        """Fetch audio ports data from the device."""
        try:
            url = f"{base}/cgi-bin/avswitch?action=showAllAudioPortsInJson"
            _LOGGER.debug("Fetching audio ports from %s", url)
            async with self.session.get(url, auth=self.auth) as resp:
                resp.raise_for_status()
                data = await resp.json()
                _LOGGER.debug("Audio ports response (raw): %s", data)
                return data
        except Exception as err:
            _LOGGER.error("Failed to fetch audio ports: %s", err)
            raise

    async def _fetch_constants(self, base: str) -> dict:
        """Fetch constants (model/chassis) from the device."""
        try:
            url = f"{base}/cgi-bin/constants"
            _LOGGER.debug("Fetching constants from %s", url)
            async with self.session.get(url, auth=self.auth) as resp:
                resp.raise_for_status()
                data = await resp.json()
                _LOGGER.debug("Constants response: %s", data)
                return data
        except Exception as err:
            _LOGGER.warning("Failed to fetch constants: %s", err)
            return {}

    async def async_set_volume(self, port: int, volume: float) -> None:
        """Set volume for a zone with optimistic update and quick refresh."""
        try:
            level_db = int((volume * 60) - 60)
            _LOGGER.debug("Setting volume for port %s to %s (level %s)", port, volume, level_db)
            # Optimistically update the data
            if self.data and "av" in self.data and "outputs" in self.data["av"]:
                for output in self.data["av"]["outputs"]:
                    if output["port"] == port:
                        output["volume"] = level_db
                        _LOGGER.debug("Optimistically updated volume in local data")
                        break
            self.async_update_listeners()
            # Make the actual API call
            url = f"http://{self.host}/cgi-bin/avswitch?action=setAudio"
            data = {f"output{port}.volume": str(level_db)}
            _LOGGER.debug("Sending volume update to %s with data %s", url, data)
            async with self.session.post(url, data=data, auth=self.auth) as resp:
                resp.raise_for_status()
                _LOGGER.debug("Successfully set volume via API")
            # Schedule a refresh 1 second later
            asyncio.create_task(self._delayed_refresh())
        except Exception as err:
            _LOGGER.error("Failed to set volume for port %s: %s", port, err, exc_info=True)
            raise

    async def async_set_mute(self, port: int, mute: bool) -> None:
        """Set mute state for a zone with optimistic update and quick refresh."""
        try:
            _LOGGER.debug("Setting mute for port %s to %s", port, mute)
            # Optimistically update the data
            if self.data and "av" in self.data and "outputs" in self.data["av"]:
                for output in self.data["av"]["outputs"]:
                    if output["port"] == port:
                        output["mute"] = mute
                        _LOGGER.debug("Optimistically updated mute in local data")
                        break
            self.async_update_listeners()
            # Make the actual API call using setAudio and outputX.mute
            url = f"http://{self.host}/cgi-bin/avswitch?action=setAudio"
            mute_val = "muted" if mute else "not-muted"
            data = {f"output{port}.mute": mute_val}
            _LOGGER.debug("Sending mute update to %s with data %s", url, data)
            async with self.session.post(url, data=data, auth=self.auth) as resp:
                resp.raise_for_status()
                _LOGGER.debug("Successfully set mute via API")
            # Schedule a refresh 1 second later
            asyncio.create_task(self._delayed_refresh())
        except Exception as err:
            _LOGGER.error("Failed to set mute for port %s: %s", port, err, exc_info=True)
            raise

    async def async_set_source(self, port, source):
        """Set the input source for a zone with optimistic update and quick refresh."""
        _LOGGER.debug("Setting source for port %s to %s", port, source)
        try:
            # Optimistically update local data
            if "av" in self.data and "outputs" in self.data["av"]:
                for output in self.data["av"]["outputs"]:
                    if output["port"] == port:
                        output["inputsrc"] = source
                        break
            self.async_update_listeners()
            # Make API call using the correct endpoint format
            url = f"http://{self.host}/cgi-bin/avswitch"
            data = {
                "action": "setAudio",
                f"output{port}.inputsrc": str(source)
            }
            _LOGGER.debug("Sending source update to %s with data %s", url, data)
            async with self.session.post(url, data=data, auth=self.auth) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to set source: %s", await response.text())
                    raise UpdateFailed(f"Failed to set source: {response.status}")
                _LOGGER.debug("Successfully set source for port %s to %s", port, source)
            # Schedule a refresh 1 second later
            asyncio.create_task(self._delayed_refresh())
        except Exception as e:
            _LOGGER.error("Error setting source: %s", str(e), exc_info=True)
            raise UpdateFailed(f"Error setting source: {str(e)}")

    async def _delayed_refresh(self):
        await asyncio.sleep(1)
        await self.async_request_refresh()

    async def async_set_source(self, port, source):
        """Set the input source for a zone."""
        _LOGGER.debug("Setting source for port %s to %s", port, source)
        try:
            # Optimistically update local data
            if "av" in self.data and "outputs" in self.data["av"]:
                for output in self.data["av"]["outputs"]:
                    if output["port"] == port:
                        output["inputsrc"] = source
                        break

            # Make API call using the correct endpoint format
            url = f"http://{self.host}/cgi-bin/avswitch"
            data = {
                "action": "setAudio",
                f"output{port}.inputsrc": str(source)
            }
            _LOGGER.debug("Sending source update to %s with data %s", url, data)
            
            async with self.session.post(url, data=data, auth=self.auth) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to set source: %s", await response.text())
                    raise UpdateFailed(f"Failed to set source: {response.status}")
                
                # Request a refresh to confirm the change
                await self.async_request_refresh()
                
                _LOGGER.debug("Successfully set source for port %s to %s", port, source)
        except Exception as e:
            _LOGGER.error("Error setting source: %s", str(e), exc_info=True)
            raise UpdateFailed(f"Error setting source: {str(e)}") 