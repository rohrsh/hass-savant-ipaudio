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


class SavantError(Exception):
    """Base error for the Savant IP Audio client."""


class SavantConnectionError(SavantError):
    """The device could not be reached or returned an unusable response."""


class SavantAuthError(SavantError):
    """The device rejected the credentials."""


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
        self, method: str, path: str, *, data: dict[str, str] | None = None
    ) -> Any:
        """Send a request, returning decoded JSON for GETs."""
        try:
            async with (
                self._lock,
                self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    data=data,
                    auth=self._auth,
                    timeout=_REQUEST_TIMEOUT,
                ) as resp,
            ):
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
        return result

    async def async_get_status(self) -> dict[str, Any]:
        """Return device status (savantID, firmware version)."""
        return await self._get_dict(_STATUS_PATH)

    async def async_get_constants(self) -> dict[str, Any]:
        """Return device constants (model/chassis)."""
        return await self._get_dict(_CONSTANTS_PATH)

    async def async_set_audio(self, params: dict[str, str]) -> None:
        """Set one or more audio parameters, e.g. {"output1.volume": "-20"}."""
        await self._request("POST", _SET_AUDIO_PATH, data=params)
