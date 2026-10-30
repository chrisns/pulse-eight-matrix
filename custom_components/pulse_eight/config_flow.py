"""UI configuration, discovery, reconfiguration and optional listeners."""

from __future__ import annotations

from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import MatrixAuthError, MatrixClient, MatrixError, validate_host
from .const import DEFAULT_OPTIONS, DOMAIN


class PulseEightConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _discovered = None

    async def _validate(self, data):
        client = MatrixClient(
            async_get_clientsession(self.hass),
            data[CONF_HOST],
            data.get(CONF_PASSWORD, ""),
        )
        return await client.identity()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = validate_host(user_input[CONF_HOST])
                identity = await self._validate(user_input)
                expected = (self._discovered or {}).get("serial")
                if expected and identity["Serial"] != expected:
                    raise MatrixError("Discovery identity does not match this address")
                await self.async_set_unique_id(identity["Serial"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST]}
                )
                return self.async_create_entry(
                    title=f"{identity['Model']} {identity['Serial']}",
                    data={
                        **user_input,
                        "serial": identity["Serial"],
                        "model": identity["Model"],
                    },
                )
            except MatrixAuthError:
                errors["base"] = "invalid_auth"
            except (MatrixError, OSError):
                errors["base"] = "cannot_connect"
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
        default = (user_input or self._discovered or {}).get(CONF_HOST, "")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=default): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    async def _discovery(self, host, serial=None):
        if serial:
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured()  # Never overwrite a host from a UDP proxy address.
        self._discovered = {CONF_HOST: host, "serial": serial}
        self.context["title_placeholders"] = {"name": serial or host}
        return await self.async_step_confirm()

    async def async_step_dhcp(self, discovery_info):
        return await self._discovery(discovery_info.ip)

    async def async_step_ssdp(self, discovery_info):
        url = urlsplit(discovery_info.ssdp_location)
        try:
            port = url.port
        except ValueError:
            return self.async_abort(reason="not_supported")
        if url.scheme != "http" or not url.hostname or port not in (None, 80):
            return self.async_abort(reason="not_supported")
        return await self._discovery(
            url.hostname, discovery_info.upnp.get("serialNumber")
        )

    async def async_step_integration_discovery(self, discovery_info):
        return await self._discovery(
            discovery_info["host"], discovery_info.get("serial")
        )

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return await self.async_step_user(
                {
                    CONF_HOST: user_input.get(CONF_HOST, self._discovered[CONF_HOST]),
                    CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                }
            )
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=self._discovered[CONF_HOST]): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            description_placeholders={"host": self._discovered[CONF_HOST]},
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        errors = {}
        if user_input:
            try:
                identity = await self._validate(user_input)
                await self.async_set_unique_id(identity["Serial"])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
            except MatrixAuthError:
                errors["base"] = "invalid_auth"
            except (MatrixError, ValueError, OSError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Optional(
                        CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                identity = await self._validate({**entry.data, **user_input})
                await self.async_set_unique_id(identity["Serial"])
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
            except MatrixAuthError:
                errors["base"] = "invalid_auth"
            except (MatrixError, ValueError, OSError):
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PulseEightOptionsFlow()


class PulseEightOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        options = {**DEFAULT_OPTIONS, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            ports = [user_input["udp_port"]]
            if user_input["cloud_enabled"]:
                ports += [user_input["cloud_port"], user_input["monitoring_port"]]
            if len(ports) != len(set(ports)):
                errors["base"] = "port_conflict"
            else:
                return self.async_create_entry(title="", data=user_input)
        schema = {}
        for key, value in options.items():
            if key not in DEFAULT_OPTIONS:
                continue
            validator = (
                str
                if key in ("firmware_hardware", "trusted_sources")
                else bool
                if isinstance(value, bool)
                else vol.All(vol.Coerce(int), vol.Range(min=30, max=3600))
                if key == "stale_seconds"
                else vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535))
            )
            schema[vol.Required(key, default=value)] = validator
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema), errors=errors
        )
