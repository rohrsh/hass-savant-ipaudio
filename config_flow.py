from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN
import aiohttp
import logging
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL = 30

class SavantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        _LOGGER.debug("Starting Savant IP Audio configuration flow")
        errors = {}
        
        if user_input is not None:
            _LOGGER.debug("User input received: %s", user_input)
            try:
                # Validate connection using the same endpoint and auth as the integration
                session = async_get_clientsession(self.hass)
                auth = aiohttp.BasicAuth(user_input["username"], user_input["password"])
                url = f"http://{user_input['host']}/cgi-bin/avswitch?action=showAllAudioPortsInJson"
                _LOGGER.debug("Attempting to connect to %s", url)
                async with session.get(url, auth=auth) as resp:
                    if resp.status == 200:
                        _LOGGER.debug("Successfully connected to Savant device")
                        return self.async_create_entry(
                            title="Savant IP Audio",
                            data=user_input
                        )
                    else:
                        _LOGGER.error("Connection failed: status %s, reason: %s", resp.status, resp.reason)
                        errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.error("Failed to connect to Savant IP Audio: %s", str(e), exc_info=True)
                errors["base"] = "cannot_connect"

        schema = vol.Schema({
            vol.Required("host"): str,
            vol.Required("username", default="RPM"): str,
            vol.Required("password", default="RPM"): str,
            vol.Optional("update_interval", default=DEFAULT_UPDATE_INTERVAL): vol.All(int, vol.Range(min=5, max=3600)),
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
        _LOGGER.debug("Initializing options flow for entry: %s", config_entry.entry_id)

    async def async_step_init(self, user_input=None):
        _LOGGER.debug("Starting options flow step")
        if user_input is not None:
            _LOGGER.debug("Options flow user input: %s", user_input)
            return self.async_create_entry(title="", data=user_input)

        # Try to get current input names from the coordinator/device
        hass = self.config_entry.hass if hasattr(self.config_entry, 'hass') else None
        input_names = {}
        if hass:
            coordinator = hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
            if coordinator and hasattr(coordinator, 'data'):
                av = coordinator.data.get("av", {})
                for inp in av.get("inputs", []):
                    input_names[inp["port"]] = inp.get("id", f"Input {inp['port']}")

        schema = vol.Schema({
            vol.Optional("input_1", default=self.config_entry.options.get("input_1") or input_names.get(1, "")): str,
            vol.Optional("input_2", default=self.config_entry.options.get("input_2") or input_names.get(2, "")): str,
            vol.Optional("input_3", default=self.config_entry.options.get("input_3") or input_names.get(3, "")): str,
            vol.Optional("input_4", default=self.config_entry.options.get("input_4") or input_names.get(4, "")): str,
            vol.Optional("input_5", default=self.config_entry.options.get("input_5") or input_names.get(5, "")): str,
            vol.Optional("update_interval", default=self.config_entry.data.get("update_interval", DEFAULT_UPDATE_INTERVAL)): vol.All(int, vol.Range(min=5, max=3600)),
        })

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors={}
        )
