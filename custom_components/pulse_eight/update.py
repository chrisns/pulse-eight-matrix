"""Read-only firmware availability; installation is deliberately unsupported."""

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    add_entities([MatrixFirmware(entry.runtime_data)])


class MatrixFirmware(MatrixEntity, UpdateEntity):
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_supported_features = UpdateEntityFeature(0)

    def __init__(self, hub):
        super().__init__(hub, "firmware", "Firmware")

    @property
    def available(self):
        d = self.hub.firmware.data
        return bool(
            self.hub.options["firmware_check_enabled"]
            and d.get("last_success")
            and not d.get("error")
            and d.get("installed") == self.installed_version
        )

    @property
    def installed_version(self):
        return self.hub.state.identity.get("Version")

    @property
    def latest_version(self):
        return self.hub.firmware.data.get("latest")

    @property
    def extra_state_attributes(self):
        return {
            **self.hub.firmware.snapshot(),
            "metadata_transport": "http",
            "advisory_only": True,
            "poll_interval_hours": 24,
        }

    async def async_update(self):
        await self.hub.firmware.check(force=True)
