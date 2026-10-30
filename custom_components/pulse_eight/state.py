"""Transport-independent state, with per-value freshness and bounded raw frames."""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from copy import deepcopy


class MatrixState:
    def __init__(self):
        self.identity = {}
        self.ports = {}
        self.temperatures = {}
        self.psu = []
        self.psu_observed = None
        self.psu_transport = None
        self.health = {}
        self.features = {}
        self.settings = {}
        self.edid = {}
        self.last_seen = None
        self.last_udp = None
        self.last_monitoring = None
        self.revision = 0
        self.packet_counts = {}
        self.last_packets = OrderedDict()
        self.index_map = {}

    def port(self, mode, bay):
        if mode not in ("Input", "Output") or type(bay) is not int or not 0 <= bay < 32:
            raise ValueError("Invalid port address")
        return self.ports.setdefault(f"{mode}/{bay}", {"Mode": mode, "Bay": bay})

    def udp(self, packet, now=None):
        now = time.time() if now is None else now
        kind = packet["type"]
        self.last_seen = self.last_udp = now
        count_key = (
            str(kind)
            if str(kind) in self.packet_counts or len(self.packet_counts) < 64
            else "other"
        )
        self.packet_counts[count_key] = self.packet_counts.get(count_key, 0) + 1
        self.last_packets[str(kind)] = {"observed": now, "packet": packet}
        self.last_packets.move_to_end(str(kind))
        while len(self.last_packets) > 32:
            self.last_packets.popitem(last=False)
        if kind == 0:
            info = packet["identity"]
            self.identity.update(
                Model=info["model"], Serial=info["serial"], Version=info["version"]
            )
        elif kind == 2:
            for r in packet["ports"]:
                if r["mode"] not in ("Input", "Output") or not 0 <= r["bay"] < 32:
                    continue
                p = self.port(r["mode"], r["bay"])
                self.index_map[r["index"]] = f"{r['mode']}/{r['bay']}"
                p.update(
                    Name=r["name"],
                    ReportedStatus=r["status_text"],
                    RoutingBytes=r["routing_bytes"],
                    RawFlags=r["unknown_flags"],
                    HardwareTypeCode=r["hardware_type_code"],
                    udp_observed=now,
                    HasSignal=r["has_signal"],
                    HPD=r["hpd"],
                    CECPowerReported=r["cec_power_reported"],
                    CECPower=r["cec_power"],
                    signal_observed=now,
                    signal_transport="udp",
                )
                if r["mode"] == "Output":
                    a, b = r["routing_bytes"]
                    p["ReceiveFrom"] = a if a == b and 0 <= a < 32 else None
        elif kind == 3:
            for r in packet["records"]:
                if (key := self.index_map.get(r["index"])) in self.ports:
                    self.ports[key]["RemoteConfiguration"] = r
        elif kind == 21:
            for i, v in enumerate(packet["temperatures_celsius"]):
                self.temperatures[str(i)] = {
                    "value": v,
                    "observed": now,
                    "transport": "udp",
                }
        self.revision += 1

    @staticmethod
    def validate_features(data):
        if not isinstance(data, dict):
            raise ValueError("Invalid features")
        for kind in ("Video", "Audio"):
            if kind not in data:
                continue
            group = data[kind]
            if not isinstance(group, dict):
                raise ValueError("Invalid feature group")
            for direction in ("Input", "Output", "IR", "ARC"):
                if direction not in group:
                    continue
                if not isinstance(group[direction], dict):
                    raise ValueError("Invalid feature section")
                if "Bays" in group[direction]:
                    count = group[direction]["Bays"]
                    if type(count) is not int or not 0 <= count <= 32:
                        raise ValueError("Invalid feature port count")

    @staticmethod
    def validate_port(data):
        if not isinstance(data, dict):
            raise ValueError("Invalid port object")
        for field in ("LinkQuality", "RemoteDetails"):
            if field in data and not isinstance(data[field], dict):
                raise ValueError("Invalid port details")
        if "AllowedSources" in data:
            sources = data["AllowedSources"]
            if (
                not isinstance(sources, list)
                or len(sources) > 32
                or any(type(n) is not int or not 0 <= n < 32 for n in sources)
            ):
                raise ValueError("Invalid source list")
        for field in (
            "Name",
            "Type",
            "StatusMessage",
            "MonitorName",
            "LinkStatus",
            "Signal",
        ):
            if field in data and not isinstance(data[field], str):
                raise ValueError("Invalid port text")

    def telemetry(self, payload, now=None):
        # Validate the complete upload before changing state. A serial is an
        # identifier, not authentication; hostile LAN peers can reach this port.
        if not isinstance(payload, dict) or len(payload) > 128:
            raise ValueError("Invalid telemetry object")
        modules = payload.get("Modules", [])
        psu = payload.get("psu", [])
        if not isinstance(modules, list) or len(modules) > 64:
            raise ValueError("Invalid telemetry ports")
        for r in modules:
            self.validate_port(r)
            if (
                not isinstance(r, dict)
                or r.get("Mode") not in ("Input", "Output")
                or type(r.get("Bay")) is not int
                or not 0 <= r["Bay"] < 32
            ):
                raise ValueError("Invalid telemetry port")
        if (
            not isinstance(psu, list)
            or len(psu) > 32
            or any(not isinstance(r, dict) for r in psu)
        ):
            raise ValueError("Invalid telemetry supplies")
        if "SystemFeatures" in payload and not isinstance(
            payload["SystemFeatures"], dict
        ):
            raise ValueError("Invalid telemetry features")
        if "SystemFeatures" in payload:
            self.validate_features(payload["SystemFeatures"])
        for k, v in payload.items():
            if k.startswith("Temperature") and k[11:].isdigit():
                if (
                    int(k[11:]) >= 32
                    or type(v) not in (int, float)
                    or not math.isfinite(v)
                    or not -100 <= v <= 250
                ):
                    raise ValueError("Invalid telemetry temperature")
        now = time.time() if now is None else now
        self.last_seen = self.last_monitoring = now
        # Never expose native log dumps in entity attributes or public diagnostics.
        self.health.update(
            {
                k: deepcopy(v)
                for k, v in payload.items()
                if k
                in (
                    "StatusMessage",
                    "Status",
                    "PSU1Message",
                    "PSU1",
                    "InputModulesMessage",
                    "InputModules",
                    "OutputModulesMessage",
                    "OutputModules",
                    "SysUpdState",
                    "Uptime",
                    "Failures",
                )
            }
        )
        if "psu" in payload:
            self.psu = deepcopy(payload["psu"])
            self.psu_observed = now
            self.psu_transport = "monitoring"
        if isinstance(payload.get("SystemFeatures"), dict):
            self.features.update(deepcopy(payload["SystemFeatures"]))
        for k, v in payload.items():
            if k.startswith("Temperature") and k[11:].isdigit():
                self.temperatures[k[11:]] = {
                    "value": v,
                    "observed": now,
                    "transport": "monitoring",
                }
        for r in payload.get("Modules", []):
            self.merge_port(r, now, "monitoring")
        self.revision += 1

    def merge_port(self, data, now=None, transport="http"):
        if not isinstance(data, dict):
            return
        if (
            data.get("Mode") not in ("Input", "Output")
            or type(data.get("Bay")) is not int
            or not 0 <= data["Bay"] < 32
        ):
            return
        self.validate_port(data)
        p = self.port(data["Mode"], data["Bay"])
        p.update(deepcopy(data))
        p["details_observed"] = time.time() if now is None else now
        p["details_transport"] = transport
        if "HasSignal" in data or "HPD" in data:
            p["signal_observed"] = p["details_observed"]
            p["signal_transport"] = transport

    def response(self, path, data):
        if not isinstance(data, dict):
            raise ValueError("Invalid response object")
        now = time.time()
        p = path.lower().strip("/")
        if p == "system/details":
            self.identity.update(data)
        elif p == "system/health":
            self.health.update(
                {
                    k: v
                    for k, v in data.items()
                    if not k.startswith("Temperature") and k != "psu"
                }
            )
            self.psu = data.get("psu", self.psu)
            if "psu" in data:
                self.psu_observed = now
                self.psu_transport = "http"
            for k, v in data.items():
                if k.startswith("Temperature") and k[11:].isdigit():
                    self.temperatures[k[11:]] = {
                        "value": v,
                        "observed": now,
                        "transport": "http",
                    }
        elif p == "system/features":
            self.validate_features(data)
            self.features.update(data)
        elif p in ("port/list", "port/list/concise"):
            ports = data.get("Ports", [])
            if not isinstance(ports, list) or len(ports) > 96:
                raise ValueError("Invalid port inventory")
            for r in ports:
                self.validate_port(r)
            for r in ports:
                self.merge_port(r, now)
        elif p.startswith("port/details/"):
            self.merge_port(data, now)
        elif p.startswith("port/remotedetails/"):
            parts = path.strip("/").split("/")
            self.port(parts[-2].title(), int(parts[-1]))["RemoteDetails"] = data
        elif p.startswith("edid/"):
            self.edid[p] = deepcopy(data)
        else:
            self.settings[p] = deepcopy(data)
        self.last_seen = now
        self.revision += 1

    def snapshot(self):
        return deepcopy({k: v for k, v in vars(self).items() if k != "index_map"})
