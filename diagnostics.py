"""Diagnostics support for Savant IP Audio."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import UNRELIABLE_OUTPUT_KEYS
from .coordinator import SavantConfigEntry

# The savantID contains the MAC address of the device
_TO_REDACT = {CONF_HOST, CONF_PASSWORD, CONF_USERNAME, "savantID", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SavantConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(entry.as_dict(), _TO_REDACT),
        "status": async_redact_data(coordinator.status, _TO_REDACT),
        "constants": async_redact_data(coordinator.constants, _TO_REDACT),
        "inputs": list(coordinator.data.inputs.values()),
        "outputs": [
            {k: v for k, v in output.items() if k not in UNRELIABLE_OUTPUT_KEYS}
            for output in coordinator.data.outputs.values()
        ],
    }
