"""Constants for Savant IP Audio integration."""

DOMAIN = "savant_ipaudio"

CONF_UPDATE_INTERVAL = "update_interval"

DEFAULT_USERNAME = "RPM"
DEFAULT_PASSWORD = "RPM"
DEFAULT_UPDATE_INTERVAL = 30
MIN_UPDATE_INTERVAL = 5
MAX_UPDATE_INTERVAL = 3600

# Turn-on behaviour for a zone: restore the last used source, or a fixed input.
TURN_ON_LAST = "last"

# Device volume range in dB, mapped onto Home Assistant's 0.0-1.0 scale.
MIN_VOLUME_DB = -60
MAX_VOLUME_DB = 0


def input_name_option(port: int) -> str:
    """Return the options key holding the name override for an input."""
    return f"input_{port}"


def zone_source_option(port: int) -> str:
    """Return the options key holding the turn-on source for a zone."""
    return f"zone_{port}_source"
