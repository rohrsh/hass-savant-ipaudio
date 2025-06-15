from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from datetime import timedelta
from .const import DOMAIN
import aiohttp
import logging
import re

SCAN_INTERVAL = timedelta(seconds=5)
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
        resp = await session.get(f"http://{host}/cgi-bin/avswitch?action=showAllAudioPortsInJson", auth=auth)
        resp.raise_for_status()
        data = await resp.json()
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
                output, session, host, auth, input_names, output_names,
                model=model, unique_id=unique_id, firmware=firmware, ip_address=ip_address
            ) for output in data["outputs"]
        ]
        async_add_entities(entities)
    except Exception as e:
        _LOGGER.error("Failed to setup Savant IP Audio: %s", str(e))
        return False

class SavantZone(MediaPlayerEntity):
    _attr_should_poll = True
    _attr_available = True

    def __init__(self, data, session, host, auth, input_names, output_names, model, unique_id, firmware, ip_address=None):
        self._data = data
        self._session = session
        self._host = host
        self._auth = auth
        self._input_names = input_names  # shared dict for all zones
        self._output_names = output_names
        self._name = output_names.get(data['port'], f"Savant Zone {data['port']}")
        self._port = data["port"]
        self._model = model
        self._unique_id = unique_id or host  # fallback to host if no unique_id
        self._firmware = firmware
        self._ip_address = ip_address or host

    @property
    def name(self):
        return self._name

    @property
    def state(self):
        if not self._attr_available:
            return STATE_UNAVAILABLE
        return STATE_OFF if self._data["inputsrc"] == 0 else STATE_ON

    @property
    def volume_level(self):
        vol_db = self._data["volume"]
        return max(0.0, min(1.0, (vol_db + 80) / 80))

    @property
    def is_volume_muted(self):
        return self._data.get("mute", False)

    @property
    def source(self):
        return self._input_names.get(self._data["inputsrc"], f"Source {self._data['inputsrc']}")

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
            "configuration_url": f"http://{self._host}/",
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

    async def async_update(self):
        try:
            resp = await self._session.get(f"http://{self._host}/cgi-bin/avswitch?action=showAllAudioPortsInJson", auth=self._auth)
            resp.raise_for_status()
            data = await resp.json()
            self._data = data["outputs"][self._port - 1]
            self._attr_available = True
        except Exception as e:
            _LOGGER.error("Failed to update Savant Zone %s: %s", self._port, str(e))
            self._attr_available = False

    async def async_set_volume_level(self, volume):
        try:
            level = int((volume * 80) - 80)
            payload = {f"output{self._port}.volume": str(level)}
            resp = await self._session.post(f"http://{self._host}/cgi-bin/avswitch?action=setAudio", data=payload, auth=self._auth)
            resp.raise_for_status()
            await self.async_update()
        except Exception as e:
            _LOGGER.error("Failed to set volume for Savant Zone %s: %s", self._port, str(e))

    async def async_mute_volume(self, mute):
        try:
            val = "muted" if mute else "not-muted"
            payload = {f"output{self._port}.mute": val}
            resp = await self._session.post(f"http://{self._host}/cgi-bin/avswitch?action=setAudio", data=payload, auth=self._auth)
            resp.raise_for_status()
            await self.async_update()
        except Exception as e:
            _LOGGER.error("Failed to set mute for Savant Zone %s: %s", self._port, str(e))

    async def async_select_source(self, source):
        try:
            src_id = next((k for k, v in self._input_names.items() if v == source), None)
            if src_id is not None:
                payload = {f"output{self._port}.inputsrc": str(src_id)}
                resp = await self._session.post(f"http://{self._host}/cgi-bin/avswitch?action=setAudio", data=payload, auth=self._auth)
                resp.raise_for_status()
                await self.async_update()
        except Exception as e:
            _LOGGER.error("Failed to select source for Savant Zone %s: %s", self._port, str(e))

    async def async_turn_on(self):
        # Always select the first available input (lowest non-zero, non-off)
        src_id = next((k for k in sorted(self._input_names) if k != 0 and self._input_names[k].lower() != "off"), None)
        if src_id is not None:
            await self.async_select_source(self._input_names[src_id])
        else:
            _LOGGER.warning("No valid input found to turn on zone %s.", self._port)

    async def async_turn_off(self):
        # Try to find the input number for 'Off', fallback to 0
        off_id = next((k for k, v in self._input_names.items() if v.lower() == "off"), None)
        if off_id is not None:
            await self.async_select_source(self._input_names[off_id])
        elif 0 in self._input_names:
            await self.async_select_source(self._input_names[0])
        else:
            _LOGGER.warning("No 'Off' input found for zone %s; cannot turn off.", self._port)

    async def async_volume_up(self):
        # Increase volume by a step (e.g., 0.05)
        new_level = min(1.0, (self.volume_level or 0) + 0.05)
        await self.async_set_volume_level(new_level)

    async def async_volume_down(self):
        # Decrease volume by a step (e.g., 0.05)
        new_level = max(0.0, (self.volume_level or 0) - 0.05)
        await self.async_set_volume_level(new_level)
