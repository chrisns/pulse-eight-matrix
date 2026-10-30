"""Connectivity and explicitly reported port/supply flags."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    hub = entry.runtime_data
    entities = [
        MatrixBinarySensor(hub, "online", "Connected"),
        MatrixBinarySensor(hub, "cloud", "Local cloud connected"),
        MatrixBinarySensor(hub, "reboot", "Restart required"),
    ]
    for key in hub.state.ports:
        for field, name in [
            ("HasSignal", "Signal present"),
            ("HPD", "Hot plug detected"),
            ("POE", "Power over HDBaseT"),
        ]:
            entities.append(MatrixBinarySensor(hub, field, name, key))
    for i, r in enumerate(hub.state.psu):
        entities.append(
            MatrixBinarySensor(
                hub, f"psu_{i}", r.get("name", f"Power supply {i + 1}") + " problem"
            )
        )
    add_entities(entities)


class MatrixBinarySensor(MatrixEntity, BinarySensorEntity):
    def __init__(self, hub, key, name, port=None):
        super().__init__(hub, f"binary_{port or 'matrix'}_{key}", name, port)
        self.key = key
        if key in ("online", "cloud"):
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        if key == "reboot" or key.startswith("psu_"):
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if port and key != "HasSignal":
            self._attr_entity_registry_enabled_default = False

    @property
    def is_on(self):
        if self.key == "online":
            return self.hub.online
        if self.key == "cloud":
            return bool(self.hub.cloud and self.hub.cloud.connected)
        if self.key == "reboot":
            return self.hub.state.health.get(
                "RebootRequired", self.hub.state.identity.get("RebootRequired")
            )
        if self.key.startswith("psu_"):
            i = int(self.key[4:])
            s = self.hub.state.psu
            return not s[i]["ok"] if i < len(s) and "ok" in s[i] else None
        v = self.port.get(self.key)
        return bool(v) if v is not None else None

    @property
    def available(self):
        if self.key in ("HasSignal", "HPD"):
            return (
                self.hub.fresh(self.port.get("signal_observed"))
                and self.is_on is not None
            )
        return (
            True
            if self.key in ("online", "cloud")
            else super().available and self.is_on is not None
        )

    @property
    def extra_state_attributes(self):
        if self.port_key:
            return {
                "observed": self.port.get("signal_observed")
                if self.key in ("HasSignal", "HPD")
                else self.port.get("details_observed"),
                "transport": self.port.get("signal_transport")
                if self.key in ("HasSignal", "HPD")
                else self.port.get("details_transport"),
            }
        return {}
