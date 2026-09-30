"""HTTP client for the Savant IP Audio web interface."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)

_AUDIO_PORTS_PATH = "/cgi-bin/avswitch?action=showAllAudioPortsInJson"
_SET_AUDIO_PATH = "/cgi-bin/avswitch?action=setAudio"
_STATUS_PATH = "/cgi-bin/status?outputType=application/json"
_CONSTANTS_PATH = "/cgi-bin/constants"

# Requests queue behind one another. Give up on a request that cannot even
# start within this time rather than letting callers wait indefinitely.
_QUEUE_TIMEOUT = 30

_MUTE_VALUES = (True, False, "muted", "not-muted")


class SavantError(Exception):
    """Base error for the Savant IP Audio client."""


class SavantConnectionError(SavantError):
    """The device could not be reached or returned an unusable response."""


class SavantAuthError(SavantError):
    """The device rejected the credentials."""


def _is_int(value: Any) -> bool:
    """Return whether a device value can be read as an integer."""
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def _output_problem(output: Any) -> str | None:
    """Return why an output entry can't be trusted, or None if it can.

    Missing routing information would read as "off", so a response lacking it
    is rejected and the last good state kept. Volume and mute may be absent
    (another model might not report them) but must be sane when present.
    """
    if not isinstance(output, dict):
        return f"{output!r} is not an object"
    for key in ("port", "inputsrc"):
        if not _is_int(output.get(key)):
            return f"{key} is {output.get(key)!r}"
    if "volume" in output and not _is_int(output["volume"]):
        return f"volume is {output['volume']!r}"
    if "mute" in output and output["mute"] not in _MUTE_VALUES:
        return f"mute is {output['mute']!r}"
    return None


class SavantClient:
    """Minimal client for the device's CGI endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the client."""
        self.host = host
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)
        self._base_url = f"http://{host}"
        # The device runs a small embedded CGI server; keep requests serial.
        self._lock = asyncio.Lock()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        exclusive: bool = True,
    ) -> Any:
        """Send a request, returning decoded JSON for GETs.

        Exclusive requests queue behind one another. A non-exclusive request
        skips the queue; the caller is then responsible for limiting how many
        are in flight.
        """
        if not exclusive:
            return await self._send(method, path, data)
        try:
            async with asyncio.timeout(_QUEUE_TIMEOUT):
                await self._lock.acquire()
        except TimeoutError as err:
            raise SavantConnectionError(
                f"{self.host} is busy; request not sent within {_QUEUE_TIMEOUT}s"
            ) from err
        try:
            return await self._send(method, path, data)
        finally:
            self._lock.release()

    async def _send(
        self, method: str, path: str, data: dict[str, str] | None
    ) -> Any:
        """Perform one HTTP request against the device."""
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                data=data,
                auth=self._auth,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise SavantAuthError(f"Authentication failed ({resp.status})")
                resp.raise_for_status()
                if method != "GET":
                    return None
                # The device doesn't reliably label its JSON responses.
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SavantConnectionError(
                f"Error communicating with {self.host}: {err or type(err).__name__}"
            ) from err
        except ValueError as err:
            raise SavantConnectionError(
                f"Invalid response from {self.host}: {err}"
            ) from err

    async def _get_dict(self, path: str) -> dict[str, Any]:
        """GET an endpoint that is expected to return a JSON object."""
        result = await self._request("GET", path)
        if not isinstance(result, dict):
            raise SavantConnectionError(f"Unexpected response from {self.host}")
        return result

    async def async_get_audio_ports(self) -> dict[str, Any]:
        """Return the inputs and outputs of the audio switch."""
        result = await self._get_dict(_AUDIO_PORTS_PATH)
        if not isinstance(result.get("outputs"), list):
            raise SavantConnectionError(
                f"Response from {self.host} does not describe any audio outputs"
            )
        for output in result["outputs"]:
            if problem := _output_problem(output):
                raise SavantConnectionError(
                    f"Response from {self.host} has an unusable output: {problem}"
                )
        return result

    async def async_get_status(self) -> dict[str, Any]:
        """Return device status (savantID, firmware version)."""
        return await self._get_dict(_STATUS_PATH)

    async def async_get_constants(self) -> dict[str, Any]:
        """Return device constants (model/chassis)."""
        return await self._get_dict(_CONSTANTS_PATH)

    async def async_set_audio(
        self, params: dict[str, str], *, exclusive: bool = True
    ) -> None:
        """Set one or more audio parameters, e.g. {"output1.volume": "-20"}.

        The device applies a write within about 0.1 s but only answers about a
        second later, and it accepts overlapping writes. Pass exclusive=False
        to send without queueing behind other requests.
        (Measured on a PAV-SIPA125, firmware 9.4:706, 2026-09-30.)
        """
        await self._request("POST", _SET_AUDIO_PATH, data=params, exclusive=exclusive)
