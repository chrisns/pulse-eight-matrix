"""Output source routing, input EDID and front-panel settings."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from .const import EDID_PROFILES
from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    e = []
    for key, p in h.state.ports.items():
        if p["Mode"] == "Output":
            e.append(MatrixSelect(h, "route", "Source", key))
            if h.state.settings.get("ir/profiles", {}).get("Profiles"):
                e.append(MatrixSelect(h, "ir", "IR profile", key))
        elif p.get("Type") != "AUDIO IN":
            e.append(MatrixSelect(h, "edid", "EDID profile", key))
    if h.state.features.get("BacklightLED") is not None:
        e.append(MatrixSelect(h, "backlight", "Logo backlight"))
    if h.state.settings.get("ir/details", {}).get("Id") is not None:
        e.append(MatrixSelect(h, "ir_global", "Control IR profile"))
    add_entities(e)


class MatrixSelect(MatrixEntity, SelectEntity):
    def __init__(self, h, kind, name, port=None):
        super().__init__(h, f"select_{kind}_{port or 'matrix'}", name, port)
        self.kind = kind
        if kind != "route":
            self._attr_entity_category = EntityCategory.CONFIG

    def mapping(self):
        if self.kind == "backlight":
            return {"Off": "off", "On": "on", "Auto": "auto"}
        if self.kind in ("ir", "ir_global"):
            return {
                f"{r['Id']}: {r['Name']}": r["Id"]
                for r in self.hub.state.settings.get("ir/profiles", {}).get(
                    "Profiles", []
                )
                if self.kind != "ir_global" or r["Id"] < 3
            }
        if self.kind == "route":
            inputs = [p for p in self.hub.state.ports.values() if p["Mode"] == "Input"]
            allowed = self.port.get("AllowedSources")
            return {
                f"{p['Bay'] + 1}: {p.get('Name', 'Input')}": p["Bay"]
                for p in inputs
                if allowed is None or p["Bay"] in allowed
            }
        profiles = dict(EDID_PROFILES)
        if not self.hub.state.features.get("Video", {}).get("Scrambling"):
            profiles.pop(7, None)
            profiles.pop(8, None)
        for p in self.hub.state.ports.values():
            if p["Mode"] == "Output":
                profiles[101 + p["Bay"]] = (
                    f"Clone output {p['Bay'] + 1}: {p.get('Name', 'Output')}"
                )
        value = self.port.get("EdidProfile")
        if value is not None and value not in profiles:
            profiles[value] = f"Custom profile {value}"
        return {label: value for value, label in profiles.items()}

    @property
    def options(self):
        return list(self.mapping())

    @property
    def current_option(self):
        if self.kind == "ir":
            value = self.hub.state.settings.get(
                f"ir/details/{self.port['Bay']}", {}
            ).get("Id")
        elif self.kind == "ir_global":
            value = self.hub.state.settings.get("ir/details", {}).get("Id")
        elif self.kind == "route":
            value = self.port.get("ReceiveFrom")
        elif self.kind == "edid":
            value = self.port.get("EdidProfile")
        else:
            value = str(self.hub.state.features.get("BacklightLED", "")).lower()
        return next((name for name, v in self.mapping().items() if v == value), None)

    async def async_select_option(self, option):
        if option not in self.mapping():
            raise ServiceValidationError("Unknown selection")
        value = self.mapping()[option]
        if self.kind == "route":
            await self.hub.route(self.port["Bay"], value)
        elif self.kind == "ir":
            await self.hub.command(
                f"/IR/SetProfile/{self.port['Bay']}",
                method="POST",
                payload={"Profile": value},
                refresh=f"/IR/Details/{self.port['Bay']}",
            )
        elif self.kind == "ir_global":
            await self.hub.command(
                "/IR/SetProfile",
                method="POST",
                payload={"Profile": value},
                refresh="/IR/Details",
            )
        elif self.kind == "edid":
            await self.hub.edid(self.port["Bay"], value)
        else:
            await self.hub.command(
                f"/System/BacklightLED/{value}",
                method="POST",
                payload={},
                refresh="/System/Features",
            )
