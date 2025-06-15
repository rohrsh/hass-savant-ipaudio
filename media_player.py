from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from datetime import timedelta
from .const import DOMAIN
from .coordinator import SavantDataUpdateCoordinator
import aiohttp
import logging
import re

_LOGGER = logging.getLogger(__name__)

DEFAULT_INPUT_NAMES = {
    0: "Off",
    1: "Doorbell",
    2: "Optical 2",
    3: "RCA 1",
    4: "RCA 2",
    5: "Media Streamer"
}

async def async_setup_entry(hass, config_entry, async_add_entities):
    host = config_entry.data["host"]
    username = config_entry.data["username"]
    password = config_entry.data["password"]
    options = config_entry.options

    input_names = DEFAULT_INPUT_NAMES.copy()
    for i in range(1, 6):
        key = f"input_{i}"
        if key in options:
            input_names[i] = options[key]

    session = async_get_clientsession(hass)
    auth = aiohttp.BasicAuth(username, password)

    # Create coordinator
    coordinator = SavantDataUpdateCoordinator(
        hass,
        host,
        auth,
        update_interval=timedelta(seconds=5)
    )

    # Fetch device info
    model = None
    unique_id = None
    firmware = None
    ip_address = None
    try:
        # Model from /cgi-bin/constants
        resp = await session.get(f"http://{host}/cgi-bin/constants", auth=auth)
        resp.raise_for_status()
        constants = await resp.json()
        model = constants.get("chassis", "Unknown")
    except Exception as e:
        _LOGGER.warning("Could not fetch model: %s", str(e))
        model = "Unknown"
    try:
        # Try to get JSON from /cgi-bin/status
        resp = await session.get(f"http://{host}/cgi-bin/status?outputType=application/json", auth=auth)
        if resp.status == 200:
            status = await resp.json()
            unique_id = status.get("savantID")
            firmware = status.get("firmwareVersion")
            ip_address = status.get("ipAddress")
        else:
            raise Exception("Not JSON")
    except Exception:
        # Fallback: parse HTML
        try:
            resp = await session.get(f"http://{host}/cgi-bin/status", auth=auth)
            resp.raise_for_status()
            html = await resp.text()
            # Parse Savant ID
            m = re.search(r'<th class="attribute-name">Savant ID</th><td class="attribute-value">([A-Fa-f0-9]+)</td>', html)
            if m:
                unique_id = m.group(1)
            # Parse Firmware Version
            m = re.search(r'<th class="attribute-name">Firmware Version</th><td class="attribute-value">([^<]+)</td>', html)
            if m:
                firmware = m.group(1)
            # Parse IP Address
            m = re.search(r'<th class="attribute-name">IP Address</th><td class="attribute-value">([^<]+)</td>', html)
            if m:
                ip_address = m.group(1)
        except Exception as e:
            _LOGGER.warning("Could not fetch or parse status: %s", str(e))
            unique_id = host
            firmware = None
            ip_address = host

    try:
        # Initial data fetch
        await coordinator.async_config_entry_first_refresh()
        data = coordinator.data
        
        # Build input_names and output_names from device
        input_names = {inp["port"]: inp.get("name", f"Input {inp['port']}") for inp in data.get("inputs", [])}
        output_names = {out["port"]: out.get("name", f"Output {out['port']}") for out in data.get("outputs", [])}
        # Apply user overrides if present
        for i in range(1, 6):
            key = f"input_{i}"
            if key in options:
                input_names[i] = options[key]
        # Ensure input 0 is always 'Off'
        if 0 not in input_names:
            input_names[0] = "Off"
        entities = [
            SavantZone(
                output, coordinator, input_names, output_names,
                model=model, unique_id=unique_id, firmware=firmware, ip_address=ip_address
            ) for output in data["outputs"]
        ]
        async_add_entities(entities)
    except Exception as e:
        _LOGGER.error("Failed to setup Savant IP Audio: %s", str(e))
        return False

class SavantZone(MediaPlayerEntity):
    _attr_should_poll = False
    _attr_available = True

    def __init__(self, data, coordinator, input_names, output_names, model, unique_id, firmware, ip_address=None):
        self._data = data
        self._coordinator = coordinator
        self._input_names = input_names
        self._output_names = output_names
        self._name = output_names.get(data['port'], f"Savant Zone {data['port']}")
        self._port = data["port"]
        self._model = model
        self._unique_id = unique_id or coordinator.host
        self._firmware = firmware
        self._ip_address = ip_address or coordinator.host

    @property
    def name(self):
        return self._name

    @property
    def available(self):
        """Return if entity is available."""
        available = self._coordinator.last_update_success
        _LOGGER.debug("Entity %s availability: %s", self.name, available)
        return available

    @property
    def state(self):
        if not self.available:
            _LOGGER.debug("Entity %s is unavailable", self.name)
            return STATE_UNAVAILABLE
        state = STATE_OFF if self._data["inputsrc"] == 0 else STATE_ON
        _LOGGER.debug("Entity %s state: %s (inputsrc: %s)", self.name, state, self._data["inputsrc"])
        return state

    @property
    def volume_level(self):
        vol_db = self._data["volume"]
        volume = max(0.0, min(1.0, (vol_db + 80) / 80))
        _LOGGER.debug("Entity %s volume: %s (raw: %s)", self.name, volume, vol_db)
        return volume

    @property
    def is_volume_muted(self):
        muted = self._data.get("mute", False)
        _LOGGER.debug("Entity %s mute state: %s", self.name, muted)
        return muted

    @property
    def source(self):
        source = self._input_names.get(self._data["inputsrc"], f"Source {self._data['inputsrc']}")
        _LOGGER.debug("Entity %s source: %s (inputsrc: %s)", self.name, source, self._data["inputsrc"])
        return source

    @property
    def source_list(self):
        return list(self._input_names.values())

    @property
    def device_class(self):
        return "receiver"

    @property
    def supported_features(self):
        return (
            MediaPlayerEntityFeature.VOLUME_SET |
            MediaPlayerEntityFeature.VOLUME_MUTE |
            MediaPlayerEntityFeature.SELECT_SOURCE |
            MediaPlayerEntityFeature.TURN_ON |
            MediaPlayerEntityFeature.TURN_OFF |
            MediaPlayerEntityFeature.VOLUME_STEP
        )

    @property
    def unique_id(self):
        return f"{self._unique_id}_zone_{self._port}"

    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": "Savant IP Audio",
            "manufacturer": "Savant",
            "model": self._model or "Unknown",
            "sw_version": self._firmware,
            "configuration_url": f"http://{self._coordinator.host}/",
        }
        # Add savant_id as a MAC address connection if it looks like a MAC
        if self._unique_id and len(self._unique_id) == 16:
            mac = ':'.join(self._unique_id[i:i+2] for i in range(0, 12, 2))
            info["connections"] = {("mac", mac)}
        return info

    @property
    def extra_state_attributes(self):
        exclude_keys = {"volume", "mute", "inputsrc", "port", "id"}
        return {k: v for k, v in self._data.items() if k not in exclude_keys}

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        _LOGGER.debug("Adding entity %s to hass", self.name)
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self):
        """Return if entity is available."""
        available = self._coordinator.last_update_success
        _LOGGER.debug("Entity %s availability: %s", self.name, available)
        return available

    @property
    def state(self):
        if not self.available:
            _LOGGER.debug("Entity %s is unavailable", self.name)
            return STATE_UNAVAILABLE
        state = STATE_OFF if self._data["inputsrc"] == 0 else STATE_ON
        _LOGGER.debug("Entity %s state: %s (inputsrc: %s)", self.name, state, self._data["inputsrc"])
        return state

    async def async_set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        _LOGGER.debug("Setting volume for %s to %s", self.name, volume)
        await self._coordinator.async_set_volume(self._port, volume)

    async def async_mute_volume(self, mute):
        """Mute the volume."""
        _LOGGER.debug("Setting mute for %s to %s", self.name, mute)
        await self._coordinator.async_set_mute(self._port, mute)

    async def async_select_source(self, source):
        """Select input source."""
        _LOGGER.debug("Selecting source for %s: %s", self.name, source)
        src_id = next((k for k, v in self._input_names.items() if v == source), None)
        if src_id is not None:
            await self._coordinator.async_set_source(self._port, src_id)
        else:
            _LOGGER.warning("Source %s not found for %s", source, self.name)

    async def async_turn_on(self):
        """Turn the media player on."""
        _LOGGER.debug("Turning on %s", self.name)
        # Always select the first available input (lowest non-zero, non-off)
        src_id = next((k for k in sorted(self._input_names) if k != 0 and self._input_names[k].lower() != "off"), None)
        if src_id is not None:
            await self.async_select_source(self._input_names[src_id])
        else:
            _LOGGER.warning("No valid input found to turn on %s", self.name)

    async def async_turn_off(self):
        """Turn the media player off."""
        _LOGGER.debug("Turning off %s", self.name)
        # Try to find the input number for 'Off', fallback to 0
        off_id = next((k for k, v in self._input_names.items() if v.lower() == "off"), None)
        if off_id is not None:
            await self._coordinator.async_set_source(self._port, off_id)
        else:
            await self._coordinator.async_set_source(self._port, 0)

    async def async_volume_up(self):
        """Volume up the media player."""
        current_volume = self.volume_level
        new_volume = min(1.0, current_volume + 0.05)
        _LOGGER.debug("Volume up for %s: %s -> %s", self.name, current_volume, new_volume)
        await self.async_set_volume_level(new_volume)

    async def async_volume_down(self):
        """Volume down the media player."""
        current_volume = self.volume_level
        new_volume = max(0.0, current_volume - 0.05)
        _LOGGER.debug("Volume down for %s: %s -> %s", self.name, current_volume, new_volume)
        await self.async_set_volume_level(new_volume)
