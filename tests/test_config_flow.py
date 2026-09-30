"""Tests for the Savant IP Audio config and options flows."""

from __future__ import annotations

from aiohttp import ClientError
import pytest

from custom_components.savant_ipaudio.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from conftest import AUDIO_URL, ENTRY_DATA, HOST, SAVANT_ID, STATUS_URL

NEW_HOST = "192.0.2.20"


@pytest.fixture(autouse=True)
async def unload_entries(hass):
    """Unload entries set up by a flow, so no timers outlive the test."""
    yield
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_user_flow(hass, mock_device) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == SAVANT_ID


@pytest.mark.parametrize(
    ("mock_kwargs", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"exc": ClientError("no route")}, "cannot_connect"),
        ({"text": "<html>not a savant</html>"}, "cannot_connect"),
        ({"json": {"something": "else"}}, "cannot_connect"),
        ({"json": {"outputs": [{"port": 1, "volume": -20}]}}, "cannot_connect"),
    ],
)
async def test_user_flow_errors(hass, aioclient_mock, mock_kwargs, error) -> None:
    aioclient_mock.get(AUDIO_URL, **mock_kwargs)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=ENTRY_DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_user_flow_already_configured(hass, mock_device, config_entry) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=ENTRY_DATA
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_rejects_another_device(
    hass, mock_device, config_entry
) -> None:
    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, json={"inputs": [], "outputs": []})
    mock_device.get(STATUS_URL, json={"savantID": "FFFFFFFFFFFF0000"})

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ENTRY_DATA
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_reconfigure_to_unidentified_host_is_refused(
    hass, mock_device, config_entry
) -> None:
    """A new address must be shown to be the same device before it is used."""
    mock_device.clear_requests()
    mock_device.get(
        f"http://{NEW_HOST}/cgi-bin/avswitch?action=showAllAudioPortsInJson",
        json={"inputs": [], "outputs": []},
    )
    mock_device.get(
        f"http://{NEW_HOST}/cgi-bin/status?outputType=application/json",
        exc=ClientError("no status"),
    )

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, "host": NEW_HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_identify"}
    assert config_entry.data["host"] == HOST


async def test_reconfigure_credentials_without_status(
    hass, mock_device, config_entry
) -> None:
    """Same address: the identity check can't fail, so a status hiccup is fine."""
    mock_device.clear_requests()
    mock_device.get(AUDIO_URL, json={"inputs": [], "outputs": []})
    mock_device.get(STATUS_URL, exc=ClientError("no status"))

    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, "password": "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reauth(hass, mock_device, config_entry) -> None:
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "admin", "password": "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()  # the entry is reloaded in the background
    assert config_entry.data == {
        "host": HOST,
        "username": "admin",
        "password": "secret",
    }


async def _options_step(hass, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_options(hass, mock_device, config_entry) -> None:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Name an input
    result = await _options_step(hass, config_entry, "inputs")
    assert [str(key) for key in result["data_schema"].schema] == [
        "input_1",
        "input_2",
        "input_3",
    ]
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"input_1": " Sonos ", "input_2": "TV"}
    )
    await hass.async_block_till_done()
    assert config_entry.options == {"input_1": "Sonos", "input_2": "TV"}
    state = hass.states.get("media_player.savant_ip_audio_kitchen")
    assert state.attributes["source_list"] == ["Sonos", "TV", "Aux"]

    # Other steps keep it; the interval shown is the stored one
    result = await _options_step(hass, config_entry, "polling")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"update_interval": 10}
    )
    await hass.async_block_till_done()
    result = await _options_step(hass, config_entry, "polling")
    (marker,) = result["data_schema"].schema
    assert marker.default() == 10
    hass.config_entries.options.async_abort(result["flow_id"])

    result = await _options_step(hass, config_entry, "zones")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"zone_1_source": "2", "zone_2_source": "last"}
    )
    await hass.async_block_till_done()

    # Clearing a name removes the override
    result = await _options_step(hass, config_entry, "inputs")
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"input_2": "TV"}
    )
    await hass.async_block_till_done()
    assert config_entry.options == {
        "input_2": "TV",
        "update_interval": 10,
        "zone_1_source": "2",
        "zone_2_source": "last",
    }
    state = hass.states.get("media_player.savant_ip_audio_kitchen")
    assert state.attributes["source_list"] == ["Streamer", "TV", "Aux"]
