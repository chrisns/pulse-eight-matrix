"""CEC controls and source selection; reported state never assumes success."""

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    h = entry.runtime_data
    add_entities(
        [
            MatrixPlayer(h, key)
            for key, p in h.state.ports.items()
            if h.state.features.get("Video", {}).get(p["Mode"], {}).get("CEC")
        ]
    )


class MatrixPlayer(MatrixEntity, MediaPlayerEntity):
    def __init__(self, h, key):
        super().__init__(h, "player_" + key, "CEC", key)
        self._attr_device_class = (
            MediaPlayerDeviceClass.TV
            if key.startswith("Output")
            else MediaPlayerDeviceClass.RECEIVER
        )
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
        )
        if key.startswith("Output"):
            self._attr_supported_features |= MediaPlayerEntityFeature.SELECT_SOURCE

    @property
    def state(self):
        if self.port.get("CECPowerReported") and (
            self.port.get("udp_observed") or 0
        ) >= (self.port.get("details_observed") or 0):
            power = self.port.get("CECPower")
            return (
                MediaPlayerState.ON
                if power == "on"
                else MediaPlayerState.OFF
                if power == "off"
                else None
            )
        value = self.port.get("DPS")
        if value in (0, 2):
            return MediaPlayerState.ON
        if value in (1, 3):
            return MediaPlayerState.OFF
        return None

    @property
    def extra_state_attributes(self):
        return {
            "power_status_code": self.port.get("DPS"),
            "observed": self.port.get("udp_observed")
            if self.port.get("CECPowerReported")
            else self.port.get("details_observed"),
            "transport": "udp" if self.port.get("CECPowerReported") else "snapshot",
            "note": "Last reported CEC power state; command acceptance is not TV acknowledgement",
        }

    @property
    def source_list(self):
        return [
            f"{p['Bay'] + 1}: {p.get('Name', 'Input')}"
            for p in self.hub.state.ports.values()
            if p["Mode"] == "Input"
            and p["Bay"] in self.port.get("AllowedSources", range(32))
        ]

    @property
    def source(self):
        value = self.port.get("ReceiveFrom")
        return next(
            (v for v in self.source_list if int(v.split(":", 1)[0]) - 1 == value), None
        )

    async def async_select_source(self, source):
        from homeassistant.exceptions import ServiceValidationError

        if source not in self.source_list:
            raise ServiceValidationError("Unknown source")
        await self.hub.route(self.port["Bay"], int(source.split(":", 1)[0]) - 1)

    async def _power(self, on):
        await self.hub.command(
            f"/CEC/{'on' if on else 'off'}/{self.port['Mode']}/{self.port['Bay']}"
        )

    async def async_turn_on(self):
        await self._power(True)

    async def async_turn_off(self):
        await self._power(False)

    async def _key(self, code):
        await self.hub.command(
            f"/cec/key/{self.port['Mode']}/{self.port['Bay']}",
            method="POST",
            payload={"Code": code},
        )

    async def async_volume_up(self):
        await self._key(0x41)

    async def async_volume_down(self):
        await self._key(0x42)

    async def async_media_play(self):
        await self._key(0x44)

    async def async_media_pause(self):
        await self._key(0x46)

    async def async_media_stop(self):
        await self._key(0x45)

    async def async_media_next_track(self):
        await self._key(0x4B)

    async def async_media_previous_track(self):
        await self._key(0x4C)
