"""Common device registry and push entity behavior."""

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN


class MatrixEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hub, key, name, port=None):
        self.hub = hub
        self.port_key = port
        self._attr_name = name
        self._attr_unique_id = f"{hub.serial}_{key}"

    @property
    def port(self):
        return self.hub.state.ports.get(self.port_key, {}) if self.port_key else {}

    @property
    def available(self):
        return self.hub.online

    @property
    def device_info(self):
        identity = self.hub.state.identity
        if self.port_key:
            mode, bay = self.port_key.split("/")
            p = self.port
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self.hub.serial}_{self.port_key}")},
                name=f"{mode} {int(bay) + 1}: {p.get('Name', mode)}",
                manufacturer="Pulse-Eight",
                model=p.get("Type", f"{mode} port"),
                via_device_id=self.hub.device_id,
                configuration_url=f"http://{self.hub.client.host}/",
            )
        info = DeviceInfo(
            identifiers={(DOMAIN, self.hub.serial)},
            name=f"{identity.get('Model', 'Matrix')} {self.hub.serial}",
            manufacturer="Pulse-Eight",
            model=identity.get("Model"),
            sw_version=identity.get("Version"),
            serial_number=self.hub.serial,
            configuration_url=f"http://{self.hub.client.host}/",
        )
        if identity.get("MAC"):
            info["connections"] = {(CONNECTION_NETWORK_MAC, identity["MAC"].lower())}
        return info

    async def async_added_to_hass(self):
        self.hub.listeners.add(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        self.hub.listeners.discard(self.async_write_ha_state)
