"""Config flow for Savant IP Audio."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import SavantAuthError, SavantClient, SavantConnectionError
from .const import (
    CONF_OPTIMISTIC_WRITES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_OPTIMISTIC_WRITES,
    DEFAULT_PASSWORD,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_USERNAME,
    DOMAIN,
    MAX_UPDATE_INTERVAL,
    MIN_UPDATE_INTERVAL,
    TURN_ON_LAST,
    input_name_option,
    zone_source_option,
)
from .coordinator import SavantConfigEntry, resolve_input_names

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

_CREDENTIALS_SCHEMA = {
    vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): _PASSWORD_SELECTOR,
}
_USER_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str, **_CREDENTIALS_SCHEMA})


class SavantConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Savant IP Audio."""

    VERSION = 1

    async def _async_validate(
        self, user_input: Mapping[str, Any], errors: dict[str, str]
    ) -> str | None:
        """Check that the device can be reached, returning its savantID.

        Fills `errors` when validation fails.
        """
        client = SavantClient(
            async_get_clientsession(self.hass),
            user_input[CONF_HOST],
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        try:
            await client.async_get_audio_ports()
        except SavantAuthError:
            errors["base"] = "invalid_auth"
            return None
        except SavantConnectionError as err:
            _LOGGER.debug("Cannot connect to Savant IP Audio: %s", err)
            errors["base"] = "cannot_connect"
            return None
        except Exception:
            _LOGGER.exception("Unexpected error validating Savant IP Audio device")
            errors["base"] = "unknown"
            return None

        try:
            return (await client.async_get_status()).get("savantID")
        except (SavantAuthError, SavantConnectionError) as err:
            _LOGGER.debug("Could not read savantID: %s", err)
            return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            savant_id = await self._async_validate(user_input, errors)
            if not errors:
                if savant_id:
                    await self.async_set_unique_id(savant_id)
                    self._abort_if_unique_id_configured()
                else:
                    self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(title="Savant IP Audio", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a change of host or credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            savant_id = await self._async_validate(user_input, errors)
            if not errors and entry.unique_id:
                if savant_id:
                    await self.async_set_unique_id(savant_id)
                    self._abort_if_unique_id_mismatch(reason="wrong_device")
                elif user_input[CONF_HOST] != entry.data[CONF_HOST]:
                    # Don't point the entry at an address that can't be shown
                    # to belong to the same device.
                    errors["base"] = "cannot_identify"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA, user_input or entry.data
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle the device rejecting the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            await self._async_validate({**entry.data, **user_input}, errors)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(_CREDENTIALS_SCHEMA),
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: SavantConfigEntry) -> SavantOptionsFlow:
        """Get the options flow handler."""
        return SavantOptionsFlow()


class SavantOptionsFlow(OptionsFlowWithReload):
    """Handle options for Savant IP Audio."""

    config_entry: SavantConfigEntry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            # Inputs and zones are read from the device
            return self.async_abort(reason="not_loaded")
        return self.async_show_menu(
            step_id="init", menu_options=["inputs", "zones", "polling"]
        )

    async def async_step_inputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name the inputs. A cleared field falls back to the device's name."""
        data = self.config_entry.runtime_data.data
        keys = {port: input_name_option(port) for port in sorted(data.inputs) if port}

        if user_input is not None:
            options = {
                k: v
                for k, v in self.config_entry.options.items()
                if k not in keys.values()
            }
            for key in keys.values():
                if name := str(user_input.get(key) or "").strip():
                    options[key] = name
            return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Optional(
                    key,
                    description={"suggested_value": self.config_entry.options.get(key)},
                ): str
                for port, key in keys.items()
            }
        )
        return self.async_show_form(step_id="inputs", data_schema=schema)

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which source each zone selects when it is turned on."""
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        data = self.config_entry.runtime_data.data
        input_names = resolve_input_names(data, self.config_entry.options)
        selector = SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=TURN_ON_LAST, label="Last used source"),
                    *(
                        SelectOptionDict(value=str(port), label=name)
                        for port, name in input_names.items()
                    ),
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        valid = {TURN_ON_LAST, *(str(port) for port in input_names)}
        fields: dict[vol.Marker, SelectSelector] = {}
        for port in sorted(data.outputs):
            key = zone_source_option(port)
            current = self.config_entry.options.get(key)
            fields[
                vol.Required(key, default=current if current in valid else TURN_ON_LAST)
            ] = selector
        return self.async_show_form(step_id="zones", data_schema=vol.Schema(fields))

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the polling interval and how commands are sent."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **self.config_entry.options,
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_OPTIMISTIC_WRITES: bool(user_input[CONF_OPTIMISTIC_WRITES]),
                }
            )

        entry = self.config_entry
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=entry.options.get(
                        CONF_UPDATE_INTERVAL,
                        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL,
                        max=MAX_UPDATE_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_OPTIMISTIC_WRITES,
                    default=entry.options.get(
                        CONF_OPTIMISTIC_WRITES, DEFAULT_OPTIMISTIC_WRITES
                    ),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="polling", data_schema=schema)
