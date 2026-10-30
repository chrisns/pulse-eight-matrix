"""Native input/output labels."""

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ServiceValidationError

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    add_entities(
        [PortName(entry.runtime_data, key) for key in entry.runtime_data.state.ports]
    )


class PortName(MatrixEntity, TextEntity):
    _attr_native_min = 1
    _attr_native_max = 16
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, h, key):
        super().__init__(h, "name_" + key, "Name", key)

    @property
    def native_value(self):
        return self.port.get("Name")

    async def async_set_value(self, value):
        if (
            not value
            or len(value.encode("utf-8")) > 16
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ServiceValidationError(
                "Port names must be 1 to 16 UTF-8 bytes without control characters"
            )
        mode, bay = self.port_key.split("/")
        await self.hub.command(
            f"/Port/SetName/{mode}/{bay}",
            method="POST",
            payload={"Name": value},
            refresh=f"/Port/Details/{mode}/{bay}",
        )
