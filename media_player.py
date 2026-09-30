"""Media player platform for Savant IP Audio."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    TURN_ON_LAST,
    UNRELIABLE_OUTPUT_KEYS,
    zone_source_option,
)
from .coordinator import (
    SavantConfigEntry,
    SavantDataUpdateCoordinator,
    port_name,
    resolve_input_names,
)

# Requests are serialized by the client, so no limit is needed here.
PARALLEL_UPDATES = 0

# Volume step size: 0.05 = 5% = 3 dB
VOLUME_STEP = 0.05

# Output fields exposed through standard media player properties.
_STANDARD_OUTPUT_KEYS = {"volume", "mute", "inputsrc", "port", "id"}

_ROUTING_FEATURES = (
    MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
)
_VOLUME_FEATURES = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
)

# Output 6 is the TOSLINK digital out. Savant's own specification calls it a
# "digital optical preamp output ... fixed volume", and the hardware agrees:
# it answers volume and mute commands with HTTP 200 and then ignores them
# (verified on a PAV-SIPA125, firmware 9.4:706, 2026-09-20 - two different
# volume values and a mute both had no effect). Advertising volume control
# would give Home Assistant a slider and a mute button that silently do
# nothing. Output 5, the analogue line out, *does* apply volume, so it keeps
# the full feature set and only this one port is special-cased.
#
# Deliberately a fixed port number rather than something inferred from the
# device payload, and applied only to the one chassis the behaviour has been
# checked on: guessing wrong on another model would strip away working
# controls. When the model couldn't be read at startup, assume the tested one.
_FIXED_VOLUME_PORT = 6
_FIXED_VOLUME_MODEL = "SIPA125"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SavantConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Savant IP Audio media player entities."""
    coordinator = entry.runtime_data
    input_names = resolve_input_names(coordinator.data, entry.options)

    async_add_entities(
        SavantZone(
            coordinator,
            port,
            input_names,
            entry.options.get(zone_source_option(port), TURN_ON_LAST),
        )
        for port in coordinator.data.outputs
    )


def _as_int[T](value: Any, default: T) -> int | T:
    """Coerce a device value to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _model(coordinator: SavantDataUpdateCoordinator) -> str | None:
    """Return the chassis model reported by the device, if any."""
    return coordinator.constants.get("chassis") or coordinator.status.get("chassis")


def _has_fixed_volume_port(coordinator: SavantDataUpdateCoordinator) -> bool:
    """Return whether output 6 is the known fixed-volume optical out."""
    if not (model := _model(coordinator)):
        return True
    return _FIXED_VOLUME_MODEL in re.sub(r"[^A-Z0-9]", "", str(model).upper())


def _device_info(coordinator: SavantDataUpdateCoordinator) -> DeviceInfo:
    """Build the device registry entry shared by all zones."""
    device_id = coordinator.device_id
    info = DeviceInfo(
        identifiers={(DOMAIN, device_id)},
        name="Savant IP Audio",
        manufacturer="Savant",
        configuration_url=f"http://{coordinator.client.host}/",
    )
    if model := _model(coordinator):
        info["model"] = model
    if firmware := coordinator.status.get("firmwareVersion"):
        info["sw_version"] = firmware
    # The savantID starts with the device's MAC address
    raw = device_id[:12]
    if len(raw) == 12 and all(c in "0123456789abcdefABCDEF" for c in raw):
        info["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(raw))}
    return info


@dataclass
class SavantZoneExtraStoredData(ExtraStoredData):
    """Zone state to keep across restarts."""

    last_source: int | None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return {"last_source": self.last_source}


class SavantZone(
    CoordinatorEntity[SavantDataUpdateCoordinator], MediaPlayerEntity, RestoreEntity
):
    """A single Savant output zone."""

    _attr_has_entity_name = True
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_volume_step = VOLUME_STEP
    _attr_supported_features = _ROUTING_FEATURES | _VOLUME_FEATURES

    def __init__(
        self,
        coordinator: SavantDataUpdateCoordinator,
        port: int,
        input_names: dict[int, str],
        turn_on_source: str,
    ) -> None:
        """Initialize the Savant zone."""
        super().__init__(coordinator)
        self._port = port
        self._input_names = input_names
        self._turn_on_source = turn_on_source
        self._last_source: int | None = self._source_id or None

        if port == _FIXED_VOLUME_PORT and _has_fixed_volume_port(coordinator):
            self._attr_supported_features = _ROUTING_FEATURES

        self._attr_unique_id = f"{coordinator.device_id}_zone_{port}"
        self._attr_name = port_name(self._output, f"Output {port}")
        self._attr_source_list = list(input_names.values())
        self._attr_device_info = _device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        """Restore the last used source of a zone that is currently off."""
        await super().async_added_to_hass()
        if self._last_source is None and (
            extra_data := await self.async_get_last_extra_data()
        ):
            self._last_source = (
                _as_int(extra_data.as_dict().get("last_source"), 0) or None
            )

    @property
    def extra_restore_state_data(self) -> SavantZoneExtraStoredData:
        """Return zone state to keep across restarts."""
        return SavantZoneExtraStoredData(self._last_source)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the source in use, so turning on can return to it."""
        if source_id := self._source_id:
            self._last_source = source_id
        super()._handle_coordinator_update()

    @property
    def _output(self) -> dict[str, Any]:
        """Return the device data for this zone."""
        return self.coordinator.data.outputs.get(self._port, {})

    @property
    def _source_id(self) -> int:
        """Return the input port this zone listens to, 0 when off."""
        return _as_int(self._output.get("inputsrc"), 0)

    @property
    def available(self) -> bool:
        """Return if the zone is reported by the device."""
        return super().available and self._port in self.coordinator.data.outputs

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the zone based on input source."""
        return MediaPlayerState.ON if self._source_id else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return volume as 0.0-1.0 from the device's dB range."""
        if (vol_db := _as_int(self._output.get("volume"), None)) is None:
            return None
        level = (vol_db - MIN_VOLUME_DB) / (MAX_VOLUME_DB - MIN_VOLUME_DB)
        return max(0.0, min(1.0, level))

    @property
    def is_volume_muted(self) -> bool | None:
        """Return mute state."""
        if (mute := self._output.get("mute")) is None:
            return None
        if isinstance(mute, str):
            return mute == "muted"
        return bool(mute)

    @property
    def source(self) -> str | None:
        """Return the current input source name, or None if off."""
        if not (source_id := self._source_id):
            return None
        return self._input_names.get(source_id, f"Source {source_id}")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose DSP/delay attributes that don't map to standard HA features."""
        return {
            key: value
            for key, value in self._output.items()
            if key not in _STANDARD_OUTPUT_KEYS and key not in UNRELIABLE_OUTPUT_KEYS
        }

    # ── Commands ──────────────────────────────────────────────────────

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0..1."""
        await self.coordinator.async_set_volume(self._port, volume)

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the volume."""
        await self.coordinator.async_set_mute(self._port, mute)

    async def async_select_source(self, source: str) -> None:
        """Select input source by name."""
        source_id = next(
            (port for port, name in self._input_names.items() if name == source), None
        )
        if source_id is None:
            raise ServiceValidationError(
                f"Source '{source}' is not available for {self.entity_id}; "
                f"choose one of: {', '.join(self._input_names.values())}"
            )
        await self.coordinator.async_set_source(self._port, source_id)

    async def async_turn_on(self) -> None:
        """Turn on with the configured source, else the last used one.

        A zone that is already on keeps playing what it is playing.
        """
        if self._source_id:
            return

        configured = _as_int(self._turn_on_source, 0)
        for candidate in (configured, self._last_source, *sorted(self._input_names)):
            if candidate in self._input_names:
                await self.coordinator.async_set_source(self._port, candidate)
                return
        raise HomeAssistantError(f"No input available to turn on {self.entity_id}")

    async def async_turn_off(self) -> None:
        """Turn off by setting input source to 0."""
        await self.coordinator.async_set_source(self._port, 0)
