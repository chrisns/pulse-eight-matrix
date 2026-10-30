"""Native, local-push Pulse-Eight MatrixOS integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import MatrixAuthError, MatrixError
from .const import DOMAIN, PLATFORMS
from .hub import MatrixHub
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    async_register_services(hass)
    return True


async def async_setup_entry(hass, entry: ConfigEntry):
    hub = MatrixHub(hass, entry, async_get_clientsession(hass))
    try:
        await hub.start()
    except MatrixAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except (MatrixError, OSError, TimeoutError) as exc:
        raise ConfigEntryNotReady(str(exc)) from exc
    entry.runtime_data = hub
    try:
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, hub.serial)},
            manufacturer="Pulse-Eight",
            model=hub.state.identity.get("Model"),
            name=f"{hub.state.identity.get('Model', 'Matrix')} {hub.serial}",
            serial_number=hub.serial,
            sw_version=hub.state.identity.get("Version"),
        )
    except BaseException:
        await hub.stop()
        raise
    hub.device_id = device.id
    hass.data[DOMAIN + "_runtime"]["hubs"][entry.entry_id] = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        hass.data[DOMAIN + "_runtime"]["hubs"].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_options_updated))

    async def stop(_):
        await hub.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    hub.spawn(hub.bootstrap_details())
    hub.spawn(hub.firmware.start())
    return True


async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        hass.data[DOMAIN + "_runtime"]["hubs"].pop(entry.entry_id, None)
        return True
    return False
