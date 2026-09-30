"""Constants for Savant IP Audio integration."""

DOMAIN = "savant_ipaudio"

CONF_UPDATE_INTERVAL = "update_interval"
CONF_OPTIMISTIC_WRITES = "optimistic_writes"

DEFAULT_USERNAME = "RPM"
DEFAULT_PASSWORD = "RPM"
DEFAULT_UPDATE_INTERVAL = 30
MIN_UPDATE_INTERVAL = 5
MAX_UPDATE_INTERVAL = 3600

# Return from commands as soon as they are queued, batching near-simultaneous
# changes into one request, instead of waiting for the device's slow reply.
DEFAULT_OPTIMISTIC_WRITES = True

# Turn-on behaviour for a zone: restore the last used source, or a fixed input.
TURN_ON_LAST = "last"

# Device volume range in dB, mapped onto Home Assistant's 0.0-1.0 scale.
MIN_VOLUME_DB = -60
MAX_VOLUME_DB = 0

# The device returns uninitialised memory in the high-pass filter fields of
# outputs that have no filter configured. Decoded little-endian the values are
# fragments of filesystem paths ("255/", "/dat", "a/va"), and they change on
# every command sent to that output. They are neither published as attributes
# nor included in diagnostics: stray memory is not something to ask users to
# attach to a public issue.
# (Observed on a PAV-SIPA125, firmware 9.4:706, 2026-09-20.)
UNRELIABLE_OUTPUT_KEYS = frozenset(
    {"hpffreqleft", "hpffreqright", "hpfrolloffleft", "hpfrolloffright"}
)


def input_name_option(port: int) -> str:
    """Return the options key holding the name override for an input."""
    return f"input_{port}"


def zone_source_option(port: int) -> str:
    """Return the options key holding the turn-on source for a zone."""
    return f"zone_{port}_source"
