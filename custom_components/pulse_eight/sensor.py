"""Temperature, supply, link and transport diagnostics."""

from datetime import UTC, datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory

from .entity import MatrixEntity


async def async_setup_entry(hass, entry, add_entities):
    hub = entry.runtime_data
    entities = []
    for i in range(max(5, len(hub.state.temperatures))):
        entities.append(
            MatrixSensor(hub, "temperature", str(i), f"Temperature {i + 1}")
        )
    for i, r in enumerate(hub.state.psu):
        entities.append(
            MatrixSensor(hub, "psu", str(i), r.get("name", f"Power supply {i + 1}"))
        )
    for kind, name in [
        ("uptime", "Reported uptime"),
        ("status", "System status"),
        ("packets", "UDP packets"),
        ("last_udp", "Last UDP packet"),
        ("last_monitoring", "Last monitoring upload"),
    ]:
        entities.append(MatrixSensor(hub, kind, "", name))
    for key, p in hub.state.ports.items():
        entities.append(MatrixSensor(hub, "port", "status", "Status", key))
        for field, name in [
            ("Signal", "Signal format"),
            ("LinkStatus", "Link status"),
            ("EstimatedCableLength", "Estimated cable length"),
            ("HDCP", "HDCP code"),
            ("DPS", "CEC power code"),
        ]:
            entities.append(MatrixSensor(hub, "detail", field, name, key))
        for field in [
            "ChannelA",
            "ChannelB",
            "ChannelC",
            "ChannelD",
            "BER",
            "TotalBER",
            "ErrorsCRC",
            "ErrorsVideo",
        ]:
            entities.append(MatrixSensor(hub, "link", field, field, key))
    add_entities(entities)


class MatrixSensor(MatrixEntity, SensorEntity):
    def __init__(self, hub, kind, key, name, port=None):
        super().__init__(hub, f"sensor_{port or 'matrix'}_{kind}_{key}", name, port)
        self.kind = kind
        self.key = key
        if kind == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = "°C"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif kind == "psu":
            current = "current" in name.lower()
            self._attr_device_class = (
                SensorDeviceClass.CURRENT if current else SensorDeviceClass.VOLTAGE
            )
            self._attr_native_unit_of_measurement = "A" if current else "V"
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif kind in ("last_udp", "last_monitoring"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif kind == "uptime":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_native_unit_of_measurement = "s"
        if kind in (
            "packets",
            "last_udp",
            "last_monitoring",
            "uptime",
            "detail",
            "link",
        ):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if kind in ("detail", "link"):
            self._attr_entity_registry_enabled_default = False
        if kind == "detail" and key == "EstimatedCableLength":
            self._attr_native_unit_of_measurement = "m"
            self._attr_device_class = SensorDeviceClass.DISTANCE

    @property
    def native_value(self):
        s = self.hub.state
        if self.kind == "temperature":
            return s.temperatures.get(self.key, {}).get("value")
        if self.kind == "psu":
            i = int(self.key)
            return s.psu[i].get("value") if i < len(s.psu) else None
        if self.kind == "uptime":
            return s.health.get("Uptime")
        if self.kind == "status":
            return s.health.get("StatusMessage", s.identity.get("StatusMessage"))
        if self.kind == "packets":
            return sum(s.packet_counts.values())
        if self.kind in ("last_udp", "last_monitoring"):
            value = getattr(s, self.kind)
            return datetime.fromtimestamp(value, UTC) if value else None
        if self.kind == "port":
            return self.port.get("ReportedStatus", self.port.get("StatusMessage"))
        if self.kind == "detail":
            return self.port.get(self.key)
        if self.kind == "link":
            return self.port.get("LinkQuality", {}).get(self.key)
        return None

    @property
    def available(self):
        if self.kind in ("last_udp", "last_monitoring", "packets"):
            return True
        if self.kind == "temperature":
            return self.hub.fresh(
                self.hub.state.temperatures.get(self.key, {}).get("observed")
            )
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        s = self.hub.state
        if self.kind == "temperature":
            return s.temperatures.get(self.key, {})
        if self.kind == "psu":
            return (
                {
                    **s.psu[int(self.key)],
                    "observed": s.psu_observed,
                    "transport": s.psu_transport,
                }
                if int(self.key) < len(s.psu)
                else {}
            )
        if self.kind == "packets":
            return {
                "packet_types": s.packet_counts,
                "invalid_datagrams": self.hub.broker.invalid_packets
                if self.hub.broker
                else 0,
            }
        if self.kind == "uptime":
            return {"note": "Snapshot from device; not incremented locally"}
        if self.port_key:
            result = {
                k: v
                for k, v in self.port.items()
                if k not in ("RemoteDetails", "LinkQuality")
            }
            if self.kind in ("detail", "link"):
                result["note"] = (
                    "Last reported detail snapshot; refresh explicitly or enable local monitoring pushes"
                )
            return result
        return {}
