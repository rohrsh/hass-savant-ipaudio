from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN
import aiohttp
import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

class SavantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        
        if user_input is not None:
            try:
                # Validate connection using the same endpoint and auth as the integration
                session = async_get_clientsession(self.hass)
                auth = aiohttp.BasicAuth(user_input["username"], user_input["password"])
                url = f"http://{user_input['host']}/cgi-bin/avswitch?action=showAllAudioPortsInJson"
                async with session.get(url, auth=auth) as resp:
                    if resp.status == 200:
                        return self.async_create_entry(
                            title="Savant IP Audio",
                            data=user_input
                        )
                    else:
                        _LOGGER.error(f"Connection failed: status {resp.status}, reason: {resp.reason}")
                        errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.error("Failed to connect to Savant IP Audio: %s", str(e))
                errors["base"] = "cannot_connect"

        schema = vol.Schema({
            vol.Required("host"): str,
            vol.Required("username", default="RPM"): str,
            vol.Required("password", default="RPM"): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors
        )

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(config_entry):
        return SavantOptionsFlow(config_entry)

class SavantOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Use current options as defaults
        schema = vol.Schema({
            vol.Optional("input_1", default=self.config_entry.options.get("input_1", "")): str,
            vol.Optional("input_2", default=self.config_entry.options.get("input_2", "")): str,
            vol.Optional("input_3", default=self.config_entry.options.get("input_3", "")): str,
            vol.Optional("input_4", default=self.config_entry.options.get("input_4", "")): str,
            vol.Optional("input_5", default=self.config_entry.options.get("input_5", "")): str,
        })

        # Cannot add messages to the options flow
        # description = "<b>Note:</b> After saving changes, please reload the integration for them to take effect."

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={"note": description},
            errors={}
        )
