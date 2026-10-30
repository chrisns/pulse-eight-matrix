"""Safe, bounded diagnostics: omit identifiers, logs and arbitrary EDID text."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD


async def async_get_config_entry_diagnostics(hass, entry):
    hub = entry.runtime_data
    s = hub.state
    # Use an explicit allowlist. Native telemetry can contain logs and names that
    # recursively redacting a few dictionary keys would miss.
    return {
        "configuration": async_redact_data(
            dict(entry.data), {CONF_HOST, CONF_PASSWORD, "serial"}
        ),
        "options": dict(entry.options),
        "model": s.identity.get("Model"),
        "firmware": s.identity.get("Version"),
        "online": hub.online,
        "cloud_connected": bool(hub.cloud and hub.cloud.connected),
        "last_udp": s.last_udp,
        "last_monitoring": s.last_monitoring,
        "packet_counts": dict(s.packet_counts),
        "packet_shapes": {
            k: {
                "payload_bytes": v["packet"]["payload_bytes"],
                "observed": v["observed"],
            }
            for k, v in s.last_packets.items()
        },
        "temperatures": s.temperatures,
        "ports": [
            {
                "mode": p["Mode"],
                "bay": p["Bay"],
                "type": p.get("Type"),
                "reported_status": p.get("ReportedStatus"),
                "routing_bytes": p.get("RoutingBytes"),
                "raw_flags": p.get("RawFlags"),
                "hardware_type": p.get("HardwareTypeCode"),
            }
            for p in s.ports.values()
        ],
        "limitations": [
            "Raw CEC reception is not verified",
            "Unknown UDP types are retained without invented semantics",
            "HTTP/detail values are snapshots, not periodic polls",
        ],
    }
