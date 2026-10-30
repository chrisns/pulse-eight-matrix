"""CEC behavior and panel configuration. No settings change during setup."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    entities = []
    for key, name in [
        ("CEC", "CEC enabled"),
        ("CEC_Switching", "CEC automatic switching"),
        ("StatusLED", "Status LEDs"),
    ]:
        if key in h.state.features:
            entities.append(MatrixSwitch(h, key, name))
    entities += [
        MatrixSwitch(h, "autopause", "CEC auto pause"),
        MatrixSwitch(h, "combokeys", "CEC combination keys"),
    ]
    add_entities(entities)


class MatrixSwitch(MatrixEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, h, key, name):
        super().__init__(h, "switch_" + key, name)
        self.key = key

    @property
    def is_on(self):
        s = self.hub.state
        if self.key == "autopause":
            return s.settings.get("cec/autopause", {}).get(
                "AutoPause", s.settings.get("cec/autopause", {}).get("Result")
            )
        if self.key == "combokeys":
            return s.settings.get("cec/combokeys", {}).get("ComboKeys")
        v = s.features.get(self.key)
        return v.lower() == "on" if isinstance(v, str) else v

    @property
    def available(self):
        return super().available and self.is_on is not None

    async def _set(self, value):
        if self.key == "CEC":
            await self.hub.command(
                "/CEC/" + ("Enable" if value else "Disable"),
                method="POST",
                payload={},
                refresh="/System/Features",
            )
        elif self.key == "CEC_Switching":
            await self.hub.command(
                "/cec/switching/" + ("on" if value else "off"),
                method="POST",
                payload={},
                refresh="/System/Features",
            )
        elif self.key == "StatusLED":
            await self.hub.command(
                "/System/StatusLED/" + ("On" if value else "Off"),
                method="POST",
                payload={},
                refresh="/System/Features",
            )
        elif self.key == "autopause":
            await self.hub.command(
                "/CEC/AutoPause",
                method="POST",
                payload={"AutoPause": value},
                refresh="/CEC/AutoPause",
            )
        else:
            await self.hub.command(
                "/CEC/ComboKeys/set/" + str(value).lower(), refresh="/CEC/ComboKeys"
            )

    async def async_turn_on(self, **kwargs):
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        await self._set(False)
