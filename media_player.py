from __future__ import annotations
import logging
from homeassistant.components.media_player import MediaPlayerEntity, MediaPlayerEntityFeature
from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers.entity import DeviceInfo
from datetime import timedelta
from .const import DOMAIN
from .coordinator import SavantDataUpdateCoordinator
import aiohttp

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Savant IP Audio integration."""
    host = config_entry.data["host"]
    username = config_entry.data["username"]
    password = config_entry.data["password"]
    options = config_entry.options

    # Read update_interval from config entry data, default to 30 seconds
    update_interval = timedelta(seconds=int(config_entry.data.get("update_interval", 30)))
    # Create coordinator
    coordinator = SavantDataUpdateCoordinator(
        hass,
        host,
        aiohttp.BasicAuth(username, password),
        update_interval=update_interval,
        name=f"{DOMAIN}-{config_entry.entry_id}",
    )

    # Store coordinator in hass.data
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN][config_entry.entry_id] = coordinator

    # Initial data fetch
    await coordinator.async_config_entry_first_refresh()
    data = coordinator.data

    if not data or "av" not in data:
        _LOGGER.error("Failed to fetch initial data")
        return False

    # Build input_names and output_names from device
    input_names = {inp["port"]: inp.get("id", f"Input {inp['port']}") for inp in data["av"].get("inputs", [])}
    output_names = {out["port"]: out.get("id", f"Output {out['port']}") for out in data["av"].get("outputs", [])}
    
    # Apply user overrides if present
    for i in range(1, 6):
        key = f"input_{i}"
        if key in options:
            input_names[i] = options[key]
    
    # Ensure input 0 is always 'Off'
    if 0 not in input_names:
        input_names[0] = "Off"

    # Create entities
    entities = [
        SavantZone(
            output["port"], coordinator, input_names, output_names,
            model=data["status"].get("chassis", "Unknown"),
            unique_id=data["status"].get("savantID", host),
            firmware=data["status"].get("firmwareVersion"),
            ip_address=data["status"].get("ipAddress", host)
        ) for output in data["av"]["outputs"]
    ]
    async_add_entities(entities)

async def async_unload_entry(hass, config_entry):
    """Unload the Savant IP Audio integration."""
    if DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]:
        coordinator = hass.data[DOMAIN][config_entry.entry_id]
        await coordinator.async_shutdown()
        del hass.data[DOMAIN][config_entry.entry_id]
    return True

class SavantZone(MediaPlayerEntity):
    """A single Savant zone, backed by the shared coordinator."""

    _attr_should_poll = False
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET |
        MediaPlayerEntityFeature.VOLUME_STEP |
        MediaPlayerEntityFeature.VOLUME_MUTE |
        MediaPlayerEntityFeature.SELECT_SOURCE |
        MediaPlayerEntityFeature.TURN_ON |
        MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, port, coordinator, input_names, output_names, model, unique_id, firmware, ip_address=None):
        """Initialize the Savant zone."""
        self._port = port
        self._coordinator = coordinator
        self._input_names = input_names
        self._output_names = output_names
        # Use model from constants if available
        self._model = None  # Will be property
        self._unique_id = unique_id
        self._firmware = firmware
        self._ip_address = ip_address
        self._remove = None
        _LOGGER.debug("Initialized SavantZone with port %s", self._port)

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        _LOGGER.debug("Adding entity %s to hass", self.name)
        self._remove = self._coordinator.async_add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        """When entity will be removed from hass."""
        if self._remove:
            self._remove()

    @property
    def _output(self):
        return next((o for o in self._coordinator.data["av"].get("outputs", []) if o["port"] == self._port), {})

    @property
    def name(self):
        # Use MAC ID (first 12 chars of unique_id) for uniqueness
        mac_id = self._unique_id[:12] if self._unique_id else "unknown"
        return f"Savant {mac_id} Output {self._port}"

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
        state = STATE_OFF if self._output.get("inputsrc", 0) == 0 else STATE_ON
        _LOGGER.debug("Entity %s state: %s (inputsrc: %s)", self.name, state, self._output.get("inputsrc", 0))
        return state

    @property
    def volume_level(self):
        vol_db = self._output.get("volume", -60)
        volume = max(0.0, min(1.0, (vol_db + 60) / 60))
        _LOGGER.debug("Entity %s volume: %s (raw: %s)", self.name, volume, vol_db)
        return volume

    @property
    def is_volume_muted(self):
        muted = self._output.get("mute", False)
        _LOGGER.debug("Entity %s mute state: %s", self.name, muted)
        return muted

    @property
    def source(self):
        source = self._input_names.get(self._output.get("inputsrc", 0), f"Source {self._output.get('inputsrc', 0)}")
        _LOGGER.debug("Entity %s source: %s (inputsrc: %s)", self.name, source, self._output.get("inputsrc", 0))
        return source

    @property
    def source_list(self):
        return list(self._input_names.values())

    @property
    def device_class(self):
        return "receiver"

    @property
    def unique_id(self):
        return f"{self._unique_id}_zone_{self._port}"

    @property
    def model(self):
        # Prefer model from constants, then status, then fallback
        return (
            self._coordinator.data.get("constants", {}).get("chassis") or
            self._coordinator.data.get("status", {}).get("chassis") or
            "Unknown"
        )

    @property
    def device_info(self) -> DeviceInfo:
        info = {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": "Savant IP Audio",
            "manufacturer": "Savant",
            "model": self.model,
            "sw_version": self._firmware,
            "configuration_url": f"http://{self._coordinator.host}/",
        }
        if self._unique_id and len(self._unique_id) == 16:
            mac = ':'.join(self._unique_id[i:i+2] for i in range(0, 12, 2))
            info["connections"] = {("mac", mac)}
        return info

    @property
    def extra_state_attributes(self):
        exclude_keys = {"volume", "mute", "inputsrc", "port", "id"}
        return {k: v for k, v in self._output.items() if k not in exclude_keys}

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
        # Always use input 0 to turn off
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
