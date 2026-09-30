"""Tests for the Savant IP Audio zones."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
from unittest.mock import patch

from aiohttp import ClientError
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.savant_ipaudio import coordinator as coordinator_module
from custom_components.savant_ipaudio.api import SavantConnectionError
from custom_components.savant_ipaudio.const import DOMAIN
from custom_components.savant_ipaudio.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from conftest import (
    AUDIO_URL,
    CONFIRMED_WRITES,
    CONSTANTS_URL,
    ENTRY_DATA,
    SAVANT_ID,
    SET_URL,
    STATUS_URL,
    audio_ports,
)

KITCHEN = "media_player.savant_ip_audio_kitchen"
LOUNGE = "media_player.savant_ip_audio_lounge"


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, mock_device, config_entry):
    """Set up the integration, unloading it again after the test."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    if config_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(
        "media_player", service, {"entity_id": entity_id, **data}, blocking=True
    )


def _posts(mock) -> list[dict]:
    return [call[2] for call in mock.mock_calls if call[0] == "POST"]


async def test_state_is_correct_right_after_setup(hass, loaded_entry) -> None:
    """Zones must not report off/0 until the second poll."""
    lounge = hass.states.get(LOUNGE)
    assert lounge.state == "on"
    assert lounge.attributes["source"] == "TV"
    assert lounge.attributes["volume_level"] == pytest.approx(40 / 60)
    assert lounge.attributes["source_list"] == ["Streamer", "TV", "Aux"]

    kitchen = hass.states.get(KITCHEN)
    assert kitchen.state == "off"
    assert kitchen.attributes["bass"] == 0

    registry = er.async_get(hass)
    assert registry.async_get(KITCHEN).unique_id == f"{SAVANT_ID}_zone_1"


async def test_turn_on_leaves_a_playing_zone_alone(
    hass, loaded_entry, mock_device
) -> None:
    await _call(hass, "turn_on", LOUNGE)
    assert _posts(mock_device) == []
    assert hass.states.get(LOUNGE).attributes["source"] == "TV"


async def test_turn_on_restores_last_source(hass, loaded_entry, mock_device) -> None:
    await _call(hass, "turn_off", LOUNGE)
    assert hass.states.get(LOUNGE).state == "off"
    await _call(hass, "turn_on", LOUNGE)
    assert _posts(mock_device) == [
        {"output2.inputsrc": "0"},
        {"output2.inputsrc": "2"},
    ]
    assert hass.states.get(LOUNGE).attributes["source"] == "TV"


async def test_turn_on_without_history_uses_first_input(
    hass, loaded_entry, mock_device
) -> None:
    await _call(hass, "turn_on", KITCHEN)
    assert _posts(mock_device) == [{"output1.inputsrc": "1"}]


async def test_turn_on_uses_configured_source(hass, mock_device) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        unique_id=SAVANT_ID,
        options={"zone_1_source": "3", "input_3": "Turntable", **CONFIRMED_WRITES},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await _call(hass, "turn_on", KITCHEN)
    assert _posts(mock_device) == [{"output1.inputsrc": "3"}]
    assert hass.states.get(KITCHEN).attributes["source"] == "Turntable"

    await hass.config_entries.async_unload(entry.entry_id)


async def test_volume_and_mute(hass, loaded_entry, mock_device) -> None:
    await _call(hass, "volume_set", LOUNGE, volume_level=0.5)
    await _call(hass, "volume_up", LOUNGE)
    await _call(hass, "volume_mute", LOUNGE, is_volume_muted=True)
    assert _posts(mock_device) == [
        {"output2.volume": "-30"},
        {"output2.volume": "-27"},
        {"output2.mute": "muted"},
    ]
    state = hass.states.get(LOUNGE)
    assert state.attributes["volume_level"] == pytest.approx(0.55)
    assert state.attributes["is_volume_muted"] is True


async def test_stale_poll_does_not_undo_a_command(
    hass, loaded_entry, mock_device
) -> None:
    """A poll racing a command still returns the old value; keep the new one."""
    await _call(hass, "volume_set", LOUNGE, volume_level=0.5)
    await loaded_entry.runtime_data.async_refresh()
    assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)


async def test_unknown_source_is_an_error(hass, loaded_entry, mock_device) -> None:
    with pytest.raises(ServiceValidationError):
        await _call(hass, "select_source", LOUNGE, source="Nope")
    assert _posts(mock_device) == []


async def test_failed_command_rolls_back(hass, loaded_entry, mock_device) -> None:
    mock_device.clear_requests()
    mock_device.post(SET_URL, exc=ClientError("boom"))
    mock_device.get(AUDIO_URL, json=audio_ports())

    with pytest.raises(HomeAssistantError):
        await _call(hass, "select_source", LOUNGE, source="Aux")
    assert hass.states.get(LOUNGE).attributes["source"] == "TV"


async def _settle() -> None:
    """Let queued tasks run up to their next real wait."""
    for _ in range(10):
        await asyncio.sleep(0)


async def test_rapid_volume_changes_are_coalesced(hass, loaded_entry) -> None:
    """A slider drag against a slow device sends the value in flight and the
    latest one, not every step in between."""
    coordinator = loaded_entry.runtime_data
    release = asyncio.Event()
    sent: list[dict] = []

    async def slow_set_audio(params: dict) -> None:
        sent.append(params)
        await release.wait()

    with patch.object(coordinator.client, "async_set_audio", slow_set_audio):
        tasks = []
        for level in (0.1, 0.2, 0.3, 0.4, 0.5):
            tasks.append(hass.async_create_task(coordinator.async_set_volume(2, level)))
            await _settle()
        assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)
        release.set()
        await asyncio.gather(*tasks)

    assert sent == [{"output2.volume": "-54"}, {"output2.volume": "-30"}]
    assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)


async def test_failure_does_not_undo_a_newer_command(hass, loaded_entry) -> None:
    """An older command failing must not roll back a newer one's value."""
    coordinator = loaded_entry.runtime_data
    release = asyncio.Event()
    sent: list[dict] = []

    async def set_audio(params: dict) -> None:
        sent.append(params)
        if len(sent) == 1:
            await release.wait()
            raise SavantConnectionError("dropped")

    with patch.object(coordinator.client, "async_set_audio", set_audio):
        older = hass.async_create_task(coordinator.async_set_volume(2, 0.1))
        await _settle()
        newer = hass.async_create_task(coordinator.async_set_volume(2, 0.5))
        await _settle()
        release.set()
        with pytest.raises(HomeAssistantError):
            await older
        await newer

    assert sent == [{"output2.volume": "-54"}, {"output2.volume": "-30"}]
    assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)


async def test_ignored_command_is_logged_and_corrected(
    hass, loaded_entry, mock_device, caplog
) -> None:
    """HTTP 200 is not proof: the next poll shows what the device really did."""
    coordinator = loaded_entry.runtime_data
    with patch.object(coordinator_module, "_COMMAND_SETTLE_TIME", 0):
        await _call(hass, "volume_set", LOUNGE, volume_level=0.5)
        with caplog.at_level(logging.WARNING):
            await coordinator.async_refresh()

    # The mocked device still reports -20 dB
    assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(40 / 60)
    assert "Output 2 accepted volume=-30 but reports -20" in caplog.text


async def test_incomplete_poll_keeps_last_good_state(
    hass, loaded_entry, mock_device
) -> None:
    """An output without its routing would read as off; don't believe it."""
    payload = audio_ports()
    del payload["outputs"][1]["inputsrc"]
    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, json=payload)

    await loaded_entry.runtime_data.async_refresh()
    lounge = hass.states.get(LOUNGE)
    assert lounge.state == "on"
    assert lounge.attributes["source"] == "TV"


async def test_polling_backs_off_while_offline(hass, loaded_entry, mock_device) -> None:
    coordinator = loaded_entry.runtime_data
    normal = timedelta(seconds=30)
    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, exc=ClientError("down"))

    intervals = []
    for _ in range(6):
        await coordinator.async_refresh()
        intervals.append(coordinator.update_interval)
    assert intervals == [
        normal,
        normal,
        2 * normal,
        4 * normal,
        8 * normal,
        10 * normal,
    ]

    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, json=audio_ports())
    await coordinator.async_refresh()
    assert coordinator.update_interval == normal


async def test_unavailable_after_repeated_poll_failures(
    hass, loaded_entry, mock_device
) -> None:
    coordinator = loaded_entry.runtime_data
    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, exc=ClientError("down"))

    for _ in range(2):
        await coordinator.async_refresh()
        assert hass.states.get(LOUNGE).state == "on"

    await coordinator.async_refresh()
    assert hass.states.get(LOUNGE).state == "unavailable"

    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, json=audio_ports())
    await coordinator.async_refresh()
    assert hass.states.get(LOUNGE).state == "on"


async def test_status_failure_keeps_unique_ids(
    hass, aioclient_mock, config_entry
) -> None:
    """A failed status request at startup must not create new entities."""
    aioclient_mock.get(AUDIO_URL, json=audio_ports())
    aioclient_mock.get(STATUS_URL, exc=ClientError("slow boot"))
    aioclient_mock.get(CONSTANTS_URL, exc=ClientError("slow boot"))

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    assert registry.async_get(KITCHEN).unique_id == f"{SAVANT_ID}_zone_1"
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_status_failure_without_stored_id_retries(hass, aioclient_mock) -> None:
    aioclient_mock.get(AUDIO_URL, json=audio_ports())
    aioclient_mock.get(STATUS_URL, exc=ClientError("slow boot"))
    aioclient_mock.get(CONSTANTS_URL, json={})
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []
    await hass.config_entries.async_unload(entry.entry_id)


async def test_legacy_entry_gets_unique_id(hass, mock_device) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.unique_id == SAVANT_ID
    await hass.config_entries.async_unload(entry.entry_id)


async def test_rejected_credentials_start_reauth(
    hass, aioclient_mock, config_entry
) -> None:
    aioclient_mock.get(STATUS_URL, status=401)
    aioclient_mock.get(AUDIO_URL, status=401)

    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_last_source_survives_restart(hass, mock_device, config_entry) -> None:
    mock_restore_cache_with_extra_data(
        hass, [(State(KITCHEN, "off"), {"last_source": 3})]
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    await _call(hass, "turn_on", KITCHEN)
    assert _posts(mock_device) == [{"output1.inputsrc": "3"}]
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_diagnostics_redacts_secrets(hass, loaded_entry) -> None:
    result = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["status"]["savantID"] == "**REDACTED**"
    assert SAVANT_ID not in str(result)
    assert [out["id"] for out in result["outputs"]] == ["Kitchen", "Lounge"]


async def test_diagnostics_leave_out_stray_device_memory(hass, sipa125) -> None:
    result = await async_get_config_entry_diagnostics(hass, sipa125)
    assert not [
        key for out in result["outputs"] for key in out if key.startswith("hpf")
    ]
    assert result["outputs"][0]["delayleft"] == 84


def _sipa125_zone(port: int) -> str:
    return f"media_player.savant_ip_audio_output_{port}"


def _features(hass: HomeAssistant, port: int) -> MediaPlayerEntityFeature:
    state = hass.states.get(_sipa125_zone(port))
    return MediaPlayerEntityFeature(state.attributes["supported_features"])


@pytest.fixture
async def sipa125(hass: HomeAssistant, aioclient_mock, config_entry):
    """Set up against the payload captured from a real PAV-SIPA125."""
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "sipa125_audio_ports.json").read_text()
    )
    aioclient_mock.get(AUDIO_URL, json=payload)
    aioclient_mock.get(STATUS_URL, json={"savantID": SAVANT_ID})
    aioclient_mock.get(CONSTANTS_URL, json={"chassis": "PAV-SIPA125"})
    aioclient_mock.post(SET_URL, text="")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    yield config_entry
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_real_sipa125_payload(hass, sipa125) -> None:
    """Payload captured from a PAV-SIPA125 on firmware 9.4:706."""
    # The device's own ids ("output1", "input1") are not worth showing
    states = hass.states.async_all("media_player")
    assert sorted(state.name for state in states) == [
        f"Savant IP Audio Output {port}" for port in range(1, 7)
    ]
    zone = hass.states.get(_sipa125_zone(1))
    assert zone.state == "off"
    assert zone.attributes["source_list"] == [f"Input {n}" for n in range(1, 6)]
    assert zone.attributes["delayleft"] == 84


async def test_optical_output_has_no_volume_control(hass, sipa125) -> None:
    """Output 6 is the fixed-volume TOSLINK out; output 5 is the line out.

    The device accepts volume and mute on output 6 and then ignores them, so
    the zone must not offer controls that would silently do nothing.
    """
    line_out, optical = _features(hass, 5), _features(hass, 6)

    for feature in (
        MediaPlayerEntityFeature.VOLUME_SET,
        MediaPlayerEntityFeature.VOLUME_STEP,
        MediaPlayerEntityFeature.VOLUME_MUTE,
    ):
        assert feature in line_out
        assert feature not in optical

    # Routing is unaffected on both line-level outputs.
    for features in (line_out, optical):
        assert MediaPlayerEntityFeature.SELECT_SOURCE in features
        assert MediaPlayerEntityFeature.TURN_ON in features
        assert MediaPlayerEntityFeature.TURN_OFF in features


async def test_optical_output_refuses_volume_commands(hass, sipa125) -> None:
    """Home Assistant itself rejects the call, so nothing reaches the device."""
    with pytest.raises(HomeAssistantError):
        await _call(hass, "volume_set", _sipa125_zone(6), volume_level=0.5)
    with pytest.raises(HomeAssistantError):
        await _call(hass, "volume_mute", _sipa125_zone(6), is_volume_muted=True)


async def test_other_models_keep_volume_on_output_6(
    hass, aioclient_mock, config_entry
) -> None:
    """The optical-out special case is only known to hold for the SIPA125."""
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "sipa125_audio_ports.json").read_text()
    )
    aioclient_mock.get(AUDIO_URL, json=payload)
    aioclient_mock.get(STATUS_URL, json={"savantID": SAVANT_ID})
    aioclient_mock.get(CONSTANTS_URL, json={"chassis": "PAV-SIPA50"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert MediaPlayerEntityFeature.VOLUME_SET in _features(hass, 6)
    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_uninitialised_filter_fields_are_not_published(hass, sipa125) -> None:
    """Output 3's hpf* values are device memory garbage, not filter settings."""
    attributes = hass.states.get(_sipa125_zone(3)).attributes
    assert not [key for key in attributes if key.startswith("hpf")]
    # Neighbouring DSP fields are still exposed.
    assert attributes["delayleft"] == 88


# --- Fast (optimistic) commands: the default -------------------------------


@pytest.fixture
async def fast_entry(hass: HomeAssistant, mock_device, optimistic_entry):
    """Set up the integration with default options (fast commands on)."""
    assert await hass.config_entries.async_setup(optimistic_entry.entry_id)
    await hass.async_block_till_done()
    yield optimistic_entry
    if optimistic_entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(optimistic_entry.entry_id)
        await hass.async_block_till_done()


class _SlowDevice:
    """Stand-in for SavantClient.async_set_audio that answers when released."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[dict] = []
        self.exclusive: list[bool] = []
        self.release = asyncio.Event()
        self.error = error

    async def __call__(self, params: dict, *, exclusive: bool = True) -> None:
        self.sent.append(params)
        self.exclusive.append(exclusive)
        await self.release.wait()
        if self.error is not None:
            raise self.error


_WINDOW = 0.01


async def _past_window() -> None:
    await asyncio.sleep(_WINDOW * 4)


async def test_fast_commands_return_at_once_and_are_batched(hass, fast_entry) -> None:
    """A script routing several zones sends one request, and doesn't wait for it."""
    device = _SlowDevice()
    with (
        patch.object(fast_entry.runtime_data.client, "async_set_audio", device),
        patch.object(coordinator_module, "_WRITE_BATCH_WINDOW", _WINDOW),
    ):
        # Returns although the device never answers
        await _call(hass, "select_source", [KITCHEN, LOUNGE], source="Aux")
        await _call(hass, "volume_set", [KITCHEN, LOUNGE], volume_level=0.25)
        assert hass.states.get(LOUNGE).attributes["source"] == "Aux"
        assert hass.states.get(KITCHEN).attributes["volume_level"] == pytest.approx(0.25)

        await _past_window()
        assert device.sent == [
            {
                "output1.inputsrc": "3",
                "output2.inputsrc": "3",
                "output1.volume": "-45",
                "output2.volume": "-45",
            }
        ]
        assert device.exclusive == [False]
        device.release.set()
        await _settle()


async def test_fast_command_failure_is_logged_and_rolled_back(
    hass, fast_entry, caplog
) -> None:
    """Nobody waits on the request, so a failure is logged, not raised."""
    device = _SlowDevice(error=SavantConnectionError("dropped"))
    device.release.set()
    with (
        patch.object(fast_entry.runtime_data.client, "async_set_audio", device),
        patch.object(coordinator_module, "_WRITE_BATCH_WINDOW", _WINDOW),
        caplog.at_level(logging.WARNING),
    ):
        await _call(hass, "select_source", LOUNGE, source="Aux")
        assert hass.states.get(LOUNGE).attributes["source"] == "Aux"
        await _past_window()
        await _settle()

    assert hass.states.get(LOUNGE).attributes["source"] == "TV"
    assert "Could not set output2.inputsrc: dropped" in caplog.text


async def test_fast_commands_to_a_busy_device_send_one_more_batch(
    hass, fast_entry
) -> None:
    """A slider drag while earlier requests await their slow answers.

    Two requests may be in flight; everything after joins the next batch, so
    only the latest value is sent once a slot frees up.
    """
    device = _SlowDevice()
    coordinator = fast_entry.runtime_data
    with (
        patch.object(coordinator.client, "async_set_audio", device),
        patch.object(coordinator_module, "_WRITE_BATCH_WINDOW", _WINDOW),
    ):
        for level in (0.1, 0.2, 0.3, 0.4, 0.5):
            await coordinator.async_set_volume(2, level)
            await _past_window()
        assert device.sent == [{"output2.volume": "-54"}, {"output2.volume": "-48"}]

        device.release.set()
        await _past_window()
        await _settle()

    assert device.sent[2:] == [{"output2.volume": "-30"}]
    assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)


async def test_poll_during_a_fast_command_keeps_the_new_value(
    hass, fast_entry
) -> None:
    """The mocked device still reports the old volume while the write is out."""
    device = _SlowDevice()
    coordinator = fast_entry.runtime_data
    with (
        patch.object(coordinator.client, "async_set_audio", device),
        patch.object(coordinator_module, "_WRITE_BATCH_WINDOW", _WINDOW),
        patch.object(coordinator_module, "_COMMAND_SETTLE_TIME", 0),
    ):
        await _call(hass, "volume_set", LOUNGE, volume_level=0.5)
        await _past_window()
        assert device.sent  # in flight, unanswered
        await coordinator.async_refresh()
        assert hass.states.get(LOUNGE).attributes["volume_level"] == pytest.approx(0.5)
        device.release.set()
        await _settle()
