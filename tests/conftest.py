"""Fixtures for Savant IP Audio tests.

Run from the repository root with:
    pip install pytest-homeassistant-custom-component
    pytest
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

# The repository root *is* the integration package. Expose it to Home
# Assistant's loader as custom_components/savant_ipaudio.
_ROOT = Path(__file__).parent.parent
_BASE = Path(tempfile.mkdtemp(prefix="savant_ipaudio_tests_"))
(_BASE / "custom_components").mkdir()
(_BASE / "custom_components" / "__init__.py").touch()
(_BASE / "custom_components" / "savant_ipaudio").symlink_to(
    _ROOT, target_is_directory=True
)
sys.path.insert(0, str(_BASE))

import pytest  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
)

from custom_components.savant_ipaudio.const import DOMAIN  # noqa: E402

HOST = "192.0.2.10"
SAVANT_ID = "001122AABBCC0000"
BASE_URL = f"http://{HOST}"
AUDIO_URL = f"{BASE_URL}/cgi-bin/avswitch?action=showAllAudioPortsInJson"
SET_URL = f"{BASE_URL}/cgi-bin/avswitch?action=setAudio"
STATUS_URL = f"{BASE_URL}/cgi-bin/status?outputType=application/json"
CONSTANTS_URL = f"{BASE_URL}/cgi-bin/constants"

ENTRY_DATA = {"host": HOST, "username": "RPM", "password": "RPM"}


def audio_ports(**zone1) -> dict:
    """Return an audio ports payload; keyword args override zone 1 fields."""
    return {
        "inputs": [
            {"port": 1, "id": "Streamer"},
            {"port": 2, "id": "TV"},
            {"port": 3, "id": "Aux"},
        ],
        "outputs": [
            {
                "port": 1,
                "id": "Kitchen",
                "volume": -30,
                "mute": False,
                "inputsrc": 0,
                "bass": 0,
                **zone1,
            },
            {"port": 2, "id": "Lounge", "volume": -20, "mute": False, "inputsrc": 2},
        ],
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the integration under test."""
    return


@pytest.fixture
def mock_device(aioclient_mock):
    """Mock a healthy device."""
    aioclient_mock.get(AUDIO_URL, json=audio_ports())
    aioclient_mock.get(
        STATUS_URL, json={"savantID": SAVANT_ID, "firmwareVersion": "9.4.6"}
    )
    aioclient_mock.get(CONSTANTS_URL, json={"chassis": "SIPA-125"})
    aioclient_mock.post(SET_URL, text="")
    return aioclient_mock


@pytest.fixture
def config_entry(hass):
    """Return a config entry added to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=ENTRY_DATA, unique_id=SAVANT_ID, title="Savant IP Audio"
    )
    entry.add_to_hass(hass)
    return entry
