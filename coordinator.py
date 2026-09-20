"""Data update coordinator for Savant IP Audio integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import timedelta
import logging
import re
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SavantAuthError, SavantClient, SavantConnectionError, SavantError
from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    input_name_option,
)

_LOGGER = logging.getLogger(__name__)

type SavantConfigEntry = ConfigEntry[SavantDataUpdateCoordinator]

# After a command, poll at this rate for a short burst so changes made outside
# Home Assistant (keypads, the Savant app) show up quickly while someone is
# actively using the system.
_ACTIVE_POLL_INTERVAL = timedelta(seconds=3)
_ACTIVE_POLL_DURATION = 30  # seconds before reverting to normal interval

# Commands request a refresh; wait for activity (e.g. a volume slider drag) to
# settle before polling the device.
_REFRESH_COOLDOWN = 1.5

# A poll that started before a command had settled may carry the old value.
# Keep the optimistic state for that output rather than bouncing the UI.
_COMMAND_SETTLE_TIME = 0.5

# Ride out this many consecutive failed polls before marking zones unavailable,
# so a single dropped request doesn't make entities flicker.
_TOLERATED_POLL_FAILURES = 2


# Unless named by a Savant host, ports identify themselves as "input1", "output3"
_DEFAULT_PORT_ID = re.compile(r"(input|output)\s*\d+", re.IGNORECASE)


@dataclass(frozen=True)
class SavantData:
    """Dynamic state of the audio switch, keyed by port number."""

    inputs: dict[int, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[int, dict[str, Any]] = field(default_factory=dict)


def _by_port(items: Any) -> dict[int, dict[str, Any]]:
    """Index a list of port dicts from the device by port number."""
    result: dict[int, dict[str, Any]] = {}
    for item in items if isinstance(items, list) else []:
        try:
            result[int(item["port"])] = item
        except (KeyError, TypeError, ValueError):
            _LOGGER.debug("Ignoring port entry without a usable port: %s", item)
    return result


def port_name(item: Mapping[str, Any], fallback: str) -> str:
    """Return the name a port was given on the device, else the fallback."""
    name = str(item.get("id") or "").strip()
    return fallback if not name or _DEFAULT_PORT_ID.fullmatch(name) else name


def resolve_input_names(data: SavantData, options: Mapping[str, Any]) -> dict[int, str]:
    """Return unique display names for the selectable inputs.

    User overrides from the options flow win over device names. Port 0 means
    "off" on the device and is never offered as a source.
    """
    names: dict[int, str] = {}
    for port, inp in sorted(data.inputs.items()):
        if port == 0:
            continue
        name = options.get(input_name_option(port)) or port_name(inp, f"Input {port}")
        names[port] = str(name)

    # Sources are selected by name, so names must be unique.
    seen: dict[str, int] = {}
    for name in names.values():
        seen[name] = seen.get(name, 0) + 1
    return {
        port: f"{name} ({port})" if seen[name] > 1 else name
        for port, name in names.items()
    }


class SavantDataUpdateCoordinator(DataUpdateCoordinator[SavantData]):
    """Coordinator for Savant IP Audio data."""

    config_entry: SavantConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SavantConfigEntry) -> None:
        """Initialize the coordinator."""
        self._normal_update_interval = timedelta(
            seconds=int(
                entry.options.get(
                    CONF_UPDATE_INTERVAL,
                    entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                )
            )
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            update_interval=self._normal_update_interval,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=_REFRESH_COOLDOWN, immediate=False
            ),
            always_update=False,
        )
        self.client = SavantClient(
            async_get_clientsession(hass),
            entry.data[CONF_HOST],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        # Static device metadata, fetched once in _async_setup.
        self.status: dict[str, Any] = {}
        self.constants: dict[str, Any] = {}
        self._failed_polls = 0
        self._last_command: dict[int, float] = {}
        self._burst_cancel: CALLBACK_TYPE | None = None

    @property
    def device_id(self) -> str:
        """Return the stable identifier used for the device and unique IDs."""
        return (
            self.config_entry.unique_id
            or self.status.get("savantID")
            or self.client.host
        )

    async def _async_setup(self) -> None:
        """Fetch static device metadata (model, firmware, savantID) once."""
        try:
            self.status = await self.client.async_get_status()
        except SavantAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SavantConnectionError as err:
            # Without a stored unique ID the savantID decides the entity unique
            # IDs, so don't set up with a guess - Home Assistant will retry.
            if self.config_entry.unique_id is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.debug("Could not fetch device status: %s", err)

        try:
            self.constants = await self.client.async_get_constants()
        except SavantAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SavantConnectionError as err:
            _LOGGER.debug("Could not fetch device constants: %s", err)

    async def _async_update_data(self) -> SavantData:
        """Fetch dynamic zone data from the Savant device."""
        started = monotonic()
        try:
            av_data = await self.client.async_get_audio_ports()
        except SavantAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SavantConnectionError as err:
            self._failed_polls += 1
            if self.data is not None and self._failed_polls <= _TOLERATED_POLL_FAILURES:
                _LOGGER.debug(
                    "Poll failed (%s of %s tolerated): %s",
                    self._failed_polls,
                    _TOLERATED_POLL_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        self._failed_polls = 0
        outputs = _by_port(av_data.get("outputs"))
        if self.data is not None:
            for port, commanded_at in self._last_command.items():
                if (
                    started < commanded_at + _COMMAND_SETTLE_TIME
                    and port in outputs
                    and port in self.data.outputs
                ):
                    outputs[port] = self.data.outputs[port]
        return SavantData(inputs=_by_port(av_data.get("inputs")), outputs=outputs)

    @callback
    def _async_update_output(self, port: int, fields: dict[str, Any]) -> None:
        """Apply new field values to an output and notify listeners.

        Builds new objects instead of mutating, so data handed out earlier
        (including to the change detection of the coordinator) stays intact.
        """
        if (output := self.data.outputs.get(port)) is None:
            return
        outputs = {**self.data.outputs, port: {**output, **fields}}
        self.data = replace(self.data, outputs=outputs)
        self.async_update_listeners()

    async def _async_send_command(
        self, port: int, param: str, value: str, optimistic: dict[str, Any]
    ) -> None:
        """Set a parameter of an output, updating state optimistically."""
        if (previous := self.data.outputs.get(port)) is None:
            raise HomeAssistantError(f"Output {port} is not known to the device")

        self._async_update_output(port, optimistic)
        try:
            await self.client.async_set_audio({f"output{port}.{param}": value})
        except SavantError as err:
            self._async_update_output(
                port, {key: previous[key] for key in optimistic if key in previous}
            )
            if isinstance(err, SavantAuthError):
                self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"Failed to set {param} of output {port}: {err}"
            ) from err
        finally:
            self._last_command[port] = monotonic()

        self._activate_fast_polling()
        await self.async_request_refresh()

    async def async_set_volume(self, port: int, volume: float) -> None:
        """Set volume for a zone from Home Assistant's 0.0-1.0 scale."""
        span = MAX_VOLUME_DB - MIN_VOLUME_DB
        level_db = round(max(0.0, min(1.0, volume)) * span) + MIN_VOLUME_DB
        await self._async_send_command(
            port, "volume", str(level_db), {"volume": level_db}
        )

    async def async_set_mute(self, port: int, mute: bool) -> None:
        """Set mute state for a zone."""
        await self._async_send_command(
            port, "mute", "muted" if mute else "not-muted", {"mute": mute}
        )

    async def async_set_source(self, port: int, source: int) -> None:
        """Set the input source for a zone (0 turns the zone off)."""
        await self._async_send_command(
            port, "inputsrc", str(source), {"inputsrc": source}
        )

    @callback
    def _activate_fast_polling(self) -> None:
        """Switch to a faster poll interval, resetting the cooldown timer."""
        self.update_interval = _ACTIVE_POLL_INTERVAL
        if self._burst_cancel is not None:
            self._burst_cancel()
        self._burst_cancel = async_call_later(
            self.hass, _ACTIVE_POLL_DURATION, self._end_fast_polling
        )

    @callback
    def _end_fast_polling(self, _now: Any = None) -> None:
        """Restore normal polling interval after the active window expires."""
        self._burst_cancel = None
        self.update_interval = self._normal_update_interval

    async def async_shutdown(self) -> None:
        """Cancel pending timers when the config entry is unloaded."""
        if self._burst_cancel is not None:
            self._burst_cancel()
            self._burst_cancel = None
        await super().async_shutdown()
