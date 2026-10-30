"""Audio DSP controls on models that report DSP audio ports."""

from homeassistant.components.number import NumberEntity, NumberMode

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    ports = h.state.settings.get("audio/details", {}).get("Ports", [])
    add_entities(
        [
            AudioNumber(h, p["Bay"], field)
            for p in ports
            for field in ["Volume", "Delay"]
            if field in p
        ]
    )


class AudioNumber(MatrixEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_step = 1

    def __init__(self, h, bay, field):
        super().__init__(h, f"audio_{bay}_{field}", f"Audio {bay + 1} {field.lower()}")
        self.bay = bay
        self.field = field
        self._attr_native_max_value = 100 if field == "Volume" else 340
        self._attr_native_unit_of_measurement = "%" if field == "Volume" else "ms"

    @property
    def native_value(self):
        return next(
            (
                p.get(self.field)
                for p in self.hub.state.settings.get("audio/details", {}).get(
                    "Ports", []
                )
                if p["Bay"] == self.bay
            ),
            None,
        )

    async def async_set_native_value(self, value):
        await self.hub.command(
            f"/Audio/{self.field}/{self.bay}/{int(value)}", refresh="/Audio/Details"
        )
