"""Data update coordinator for Savant IP Audio integration."""

from __future__ import annotations

import asyncio
from collections import defaultdict
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

# Beyond that, stretch the poll interval (doubling each time) so an offline
# device isn't asked for its state every few seconds indefinitely.
_MAX_BACKOFF_INTERVAL = timedelta(minutes=5)


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


def _normalise(param: str, value: Any) -> Any:
    """Return a device or commanded value in a comparable form."""
    if param == "mute":
        return value == "muted" if isinstance(value, str) else bool(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


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
        # Commands, keyed by (port, param). The generation identifies the most
        # recent command for a param; the lock allows one request per param in
        # flight, so a slider drag sends at most the value in flight and the
        # latest one rather than every intermediate step.
        self._generation: dict[tuple[int, str], int] = {}
        self._param_locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        # Last values known to be on the device, from polls and accepted
        # commands. A failed command rolls back to these.
        self._confirmed: dict[int, dict[str, Any]] = {}
        # Accepted commands awaiting read-back: value and when it was sent.
        self._expected: dict[tuple[int, str], tuple[Any, float]] = {}

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
            self._async_set_interval()
            if self.data is not None and self._failed_polls <= _TOLERATED_POLL_FAILURES:
                _LOGGER.debug(
                    "Poll failed (%s of %s tolerated): %s",
                    self._failed_polls,
                    _TOLERATED_POLL_FAILURES,
                    err,
                )
                return self.data
            raise UpdateFailed(str(err)) from err

        if self._failed_polls:
            self._failed_polls = 0
            self._async_set_interval()
        polled = _by_port(av_data.get("outputs"))
        outputs = dict(polled)
        for port, output in polled.items():
            commanded_at = self._last_command.get(port)
            if (
                commanded_at is not None
                and started < commanded_at + _COMMAND_SETTLE_TIME
                and self.data is not None
                and port in self.data.outputs
            ):
                outputs[port] = self.data.outputs[port]
            else:
                self._confirmed[port] = output
        self._check_expected(started, polled)
        return SavantData(inputs=_by_port(av_data.get("inputs")), outputs=outputs)

    def _check_expected(
        self, started: float, polled: dict[int, dict[str, Any]]
    ) -> None:
        """Warn about accepted commands the device did not apply.

        The device answers HTTP 200 to commands it then ignores (seen on the
        fixed-volume optical output). The poll that follows a command already
        replaces the optimistic state with what the device reports; this makes
        the discrepancy visible in the log instead of just a slider jumping back.
        """
        for (port, param), (value, sent_at) in list(self._expected.items()):
            if started < sent_at + _COMMAND_SETTLE_TIME:
                continue
            del self._expected[(port, param)]
            if param not in (output := polled.get(port, {})):
                continue
            if _normalise(param, output[param]) != _normalise(param, value):
                _LOGGER.warning(
                    "Output %s accepted %s=%s but reports %s; the device may "
                    "have ignored the command, or it was changed elsewhere",
                    port,
                    param,
                    value,
                    output[param],
                )

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
        self, port: int, param: str, device_value: str, value: Any
    ) -> None:
        """Set a parameter of an output, updating state optimistically.

        Commands for the same param are coalesced: one that is still waiting
        when a newer one arrives is dropped, since the newer value is where
        the device should end up. Writes are never retried automatically - a
        failed request may still have been applied.
        """
        if port not in self.data.outputs:
            raise HomeAssistantError(f"Output {port} is not known to the device")

        key = (port, param)
        generation = self._generation[key] = self._generation.get(key, 0) + 1
        self._async_update_output(port, {param: value})

        async with self._param_locks[key]:
            if self._generation[key] != generation:
                return
            try:
                await self.client.async_set_audio(
                    {f"output{port}.{param}": device_value}
                )
            except SavantError as err:
                # A newer command for this param owns the displayed value now.
                if self._generation[key] == generation and param in (
                    confirmed := self._confirmed.get(port, {})
                ):
                    self._async_update_output(port, {param: confirmed[param]})
                if isinstance(err, SavantAuthError):
                    self.config_entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    f"Failed to set {param} of output {port}: {err}"
                ) from err
            finally:
                self._last_command[port] = monotonic()

            self._confirmed.setdefault(port, {})[param] = value
            self._expected[key] = (value, self._last_command[port])

        self._activate_fast_polling()
        await self.async_request_refresh()

    async def async_set_volume(self, port: int, volume: float) -> None:
        """Set volume for a zone from Home Assistant's 0.0-1.0 scale."""
        span = MAX_VOLUME_DB - MIN_VOLUME_DB
        level_db = round(max(0.0, min(1.0, volume)) * span) + MIN_VOLUME_DB
        await self._async_send_command(port, "volume", str(level_db), level_db)

    async def async_set_mute(self, port: int, mute: bool) -> None:
        """Set mute state for a zone."""
        await self._async_send_command(
            port, "mute", "muted" if mute else "not-muted", mute
        )

    async def async_set_source(self, port: int, source: int) -> None:
        """Set the input source for a zone (0 turns the zone off)."""
        await self._async_send_command(port, "inputsrc", str(source), source)

    @callback
    def _activate_fast_polling(self) -> None:
        """Switch to a faster poll interval, resetting the cooldown timer."""
        if self._burst_cancel is not None:
            self._burst_cancel()
        self._burst_cancel = async_call_later(
            self.hass, _ACTIVE_POLL_DURATION, self._end_fast_polling
        )
        self._async_set_interval()

    @callback
    def _end_fast_polling(self, _now: Any = None) -> None:
        """Restore normal polling interval after the active window expires."""
        self._burst_cancel = None
        self._async_set_interval()

    @callback
    def _async_set_interval(self) -> None:
        """Pick the poll interval: backed off, fast after a command, or normal."""
        normal = self._normal_update_interval
        if (excess := self._failed_polls - _TOLERATED_POLL_FAILURES) > 0:
            backoff = normal * 2 ** min(excess, 10)
            self.update_interval = max(normal, min(backoff, _MAX_BACKOFF_INTERVAL))
        elif self._burst_cancel is not None:
            self.update_interval = _ACTIVE_POLL_INTERVAL
        else:
            self.update_interval = normal

    async def async_shutdown(self) -> None:
        """Cancel pending timers when the config entry is unloaded."""
        if self._burst_cancel is not None:
            self._burst_cancel()
            self._burst_cancel = None
        await super().async_shutdown()
