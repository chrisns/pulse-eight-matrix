"""Explicit refreshes; setup and background state do not poll the device."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    add_entities([RefreshButton(h)] + [RefreshButton(h, key) for key in h.state.ports])


class RefreshButton(MatrixEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, h, key=None):
        super().__init__(h, "refresh_" + (key or "matrix"), "Refresh details", key)

    async def async_press(self):
        if self.port_key:
            mode, bay = self.port_key.split("/")
            await self.hub.read(f"/Port/Details/{mode}/{bay}")
            if mode == "Output":
                await self.hub.read(f"/Port/RemoteDetails/{mode}/{bay}")
        else:
            await self.hub.read("/System/Health")
            await self.hub.read("/System/Features")
            await self.hub.bootstrap_details()
