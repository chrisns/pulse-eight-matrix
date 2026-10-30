"""Typed actions for automations; no generic firmware/network write endpoint."""

from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .client import MatrixError, validate_cec
from .const import DOMAIN, EDID_PROFILES

# The CEC media players are the one entity per port whose name is the port name.
PORT_PLAYER = re.compile(r"_player_(Input|Output)/(\d{1,2})$")


def async_register_services(hass):
    if hass.services.has_service(DOMAIN, "send_cec"):
        return

    def hub_for(data):
        if "device_id" not in data:
            return None
        device = dr.async_get(hass).async_get(data["device_id"])
        hubs = hass.data.get(DOMAIN + "_runtime", {}).get("hubs", {})
        matches = [
            h
            for entry_id, h in hubs.items()
            if device and entry_id == device.config_entry_id
        ]
        if len(matches) != 1:
            raise ServiceValidationError(
                "Select a device belonging to one loaded Pulse-Eight matrix"
            )
        return matches[0]

    def port_for(h, mode, number):
        bay = number - 1
        if f"{mode}/{bay}" not in h.state.ports:
            raise ServiceValidationError("Port does not exist on this matrix")
        return bay

    def resolve(ref, mode, h):
        """A port given as a named CEC player entity (or a legacy 1-based number)."""
        if isinstance(ref, int):
            if h is None:
                raise ServiceValidationError(
                    "Choose a matrix device when giving a port number"
                )
            return h, port_for(h, mode, ref)
        entry = er.async_get(hass).async_get(ref)
        found = (
            entry
            and entry.platform == DOMAIN
            and PORT_PLAYER.search(entry.unique_id or "")
        )
        if not found or found.group(1) != mode:
            raise ServiceValidationError(f"Choose a Pulse-Eight {mode.lower()}")
        owner = (
            hass.data.get(DOMAIN + "_runtime", {})
            .get("hubs", {})
            .get(entry.config_entry_id)
        )
        if owner is None or (h is not None and owner is not h):
            raise ServiceValidationError(
                "The chosen ports belong to different or unloaded matrices"
            )
        return owner, port_for(owner, mode, int(found.group(2)) + 1)

    async def handle(call):
        data = call.data
        h = hub_for(data)
        try:
            if call.service == "send_cec":
                bay = port_for(h, data["mode"], data["port"])
                return await h.cec(data["mode"], bay, validate_cec(data["frame"]))
            if call.service == "set_route":
                h, bay = resolve(data["output"], "Output", h)
                h, source = resolve(data["input"], "Input", h)
                return await h.route(bay, source)
            if call.service == "set_edid":
                h, bay = resolve(data["input"], "Input", h)
                profile = data["profile"]
                if (
                    profile not in EDID_PROFILES
                    and f"Output/{profile - 101}" not in h.state.ports
                ):
                    raise ServiceValidationError("Unsupported EDID profile")
                return await h.edid(bay, profile)
            if call.service == "get_port_details":
                bay = port_for(h, data["mode"], data["port"])
                result = await h.read(
                    f"/Port/Details/{data['mode']}/{bay}", data["transport"]
                )
                if data.get("include_receiver") and data["mode"] == "Output":
                    result = {
                        **result,
                        "Receiver": await h.read(
                            f"/Port/RemoteDetails/Output/{bay}", data["transport"]
                        ),
                    }
                return result
            if call.service == "read_edid":
                bay = port_for(h, data["mode"], data["port"])
                result = {}
                for section in ["Details", "Timings", "Audio", "HDMI", "HDR"]:
                    try:
                        result[section] = await h.read(
                            f"/EDID/{section}/{data['mode']}/{bay}", data["transport"]
                        )
                    except MatrixError as exc:
                        result[section] = {"available": False, "error": str(exc)}
                return result
            if call.service == "check_firmware":
                if not h.options["firmware_check_enabled"]:
                    raise ServiceValidationError(
                        "Firmware checks are disabled in integration options"
                    )
                await h.firmware.check(force=True)
                return h.firmware.snapshot()
            if call.service == "refresh":
                await h.read("/System/Health")
                await h.read("/System/Features")
                complete = await h.bootstrap_details()
                return {"refreshed": complete}
            if call.service == "get_status":
                # Response only to the authenticated HA caller; diagnostics downloads use a separate allowlist.
                return {
                    **h.state.snapshot(),
                    "transport": {
                        "udp_port": h.options["udp_port"],
                        "cloud_enabled": h.cloud is not None,
                        "cloud_connected": bool(h.cloud and h.cloud.connected),
                    },
                }
            if call.service == "set_ir_profile":
                h, bay = resolve(data["output"], "Output", h)
                return await h.command(
                    f"/IR/SetProfile/{bay}",
                    method="POST",
                    payload={"Profile": data["profile"]},
                    refresh=f"/IR/Details/{bay}",
                )
        except (MatrixError, ValueError) as exc:
            raise HomeAssistantError(str(exc)) from exc
        raise ServiceValidationError("Unsupported action")

    base = {vol.Required("device_id"): cv.string}
    # Ports given as entities identify their own matrix.
    base_optional = {vol.Optional("device_id"): cv.string}
    mode = {
        vol.Required("mode", default="Output"): vol.In(["Input", "Output"]),
        vol.Required("port"): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
    }
    transport = {vol.Optional("transport", default="http"): vol.In(["http", "cloud"])}

    def number():
        return vol.All(vol.Coerce(int), vol.Range(min=1, max=32))

    def port():
        return vol.Any(cv.entity_id, number())

    schemas = {
        "send_cec": {**base, **mode, vol.Required("frame"): validate_cec},
        "set_route": {
            **base_optional,
            vol.Required("output"): port(),
            vol.Required("input"): port(),
        },
        "set_edid": {
            **base_optional,
            vol.Required("input"): port(),
            vol.Required("profile"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=132)
            ),
        },
        "get_port_details": {
            **base,
            **mode,
            **transport,
            vol.Optional("include_receiver", default=False): cv.boolean,
        },
        "read_edid": {**base, **mode, **transport},
        "refresh": base,
        "check_firmware": base,
        "get_status": base,
        "set_ir_profile": {
            **base_optional,
            vol.Required("output"): port(),
            vol.Required("profile"): vol.All(
                vol.Coerce(int), vol.Range(min=0, max=65535)
            ),
        },
    }
    for name, schema in schemas.items():
        hass.services.async_register(
            DOMAIN,
            name,
            handle,
            schema=vol.Schema(schema),
            supports_response=SupportsResponse.OPTIONAL,
        )
