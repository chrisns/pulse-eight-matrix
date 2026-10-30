"""CEC remote commands. This does not claim raw CEC reception."""

import asyncio

from homeassistant.components.remote import RemoteEntity
from homeassistant.exceptions import ServiceValidationError

from .const import CEC_KEYS
from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    add_entities(
        [
            MatrixRemote(h, key)
            for key, p in h.state.ports.items()
            if h.state.features.get("Video", {}).get(p["Mode"], {}).get("CEC")
        ]
    )


class MatrixRemote(MatrixEntity, RemoteEntity):
    def __init__(self, h, key):
        super().__init__(h, "remote_" + key, "Remote", key)

    @property
    def is_on(self):
        if self.port.get("CECPowerReported") and (
            self.port.get("udp_observed") or 0
        ) >= (self.port.get("details_observed") or 0):
            power = self.port.get("CECPower")
            return power == "on" if power is not None else None
        value = self.port.get("DPS")
        return value in (0, 2) if value in (0, 1, 2, 3) else None

    async def async_turn_on(self, **kwargs):
        await self.hub.command(f"/CEC/on/{self.port['Mode']}/{self.port['Bay']}")

    async def async_turn_off(self, **kwargs):
        await self.hub.command(f"/CEC/off/{self.port['Mode']}/{self.port['Bay']}")

    async def async_send_command(self, command, **kwargs):
        # Validate the complete request before sending the first key.
        commands = list(command) if not isinstance(command, str) else [command]
        if (
            not commands
            or len(commands) > 100
            or any(c not in CEC_KEYS for c in commands)
        ):
            raise ServiceValidationError(
                "Unsupported CEC key; use send_cec for raw frames"
            )
        repeats = kwargs.get("num_repeats", 1)
        delay = kwargs.get("delay_secs", 0.2)
        if not 1 <= repeats <= 10 or not 0 <= delay <= 5:
            raise ServiceValidationError("Invalid repeat or delay")
        if kwargs.get("hold_secs", 0):
            raise ServiceValidationError("Holding CEC keys has not been verified")
        for repeat in range(repeats):
            for c in commands:
                await self.hub.command(
                    f"/cec/key/{self.port['Mode']}/{self.port['Bay']}",
                    method="POST",
                    payload={"Code": CEC_KEYS[c]},
                )
                await asyncio.sleep(delay)
