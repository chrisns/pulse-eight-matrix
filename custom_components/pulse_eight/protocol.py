"""Strict decoder for observed MatrixOS UDP 8811 datagrams.
Undecoded bytes stay visible; unknown flags are not invented into HA states.
"""

import struct


def text(b):
    return b.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def decode(data):
    if (
        len(data) < 24
        or len(data) > 65507
        or data[:2] != b"P8"
        or data[2:4] not in (b"\x00\x00", b"\x01\x00")
    ):
        raise ValueError("Not a MatrixOS datagram")
    kind, size = struct.unpack_from("<HH", data, 20)
    if size != len(data) - 24:
        raise ValueError("Datagram payload length mismatch")
    p = data[24:]
    out = {
        "type": kind,
        "source_header": data[:20].hex(),
        "payload_bytes": len(p),
        "payload_hex": p.hex(),
    }
    if kind == 0:
        if len(p) != 54:
            raise ValueError("Unexpected identity length")
        out["identity"] = {
            "model": text(p[2:18]),
            "serial": text(p[18:34]),
            "version": text(p[34:50]),
            "unknown_prefix": p[:2].hex(),
            "unknown_suffix": p[50:].hex(),
        }
    elif kind == 2:
        if len(p) % 61 or len(p) > 64 * 61:
            raise ValueError("Invalid port record alignment")
        ports = []
        for off in range(0, len(p), 61):
            r = p[off : off + 61]
            flags = struct.unpack_from("<I", r, 53)[0]
            power = flags & 0x380
            ports.append(
                {
                    "index": r[0],
                    "mode": {0: "Input", 1: "Output"}.get(r[1], f"Unknown-{r[1]}"),
                    "bay": r[2],
                    "routing_bytes": list(r[3:5]),
                    "label": text(r[5:21]),
                    "name": text(r[21:37]),
                    "status_text": text(r[37:53]),
                    "unknown_flags": r[53:57].hex(),
                    "has_signal": bool(flags & 0x08),
                    "hpd": bool(flags & 0x10),
                    "cec_power_reported": bool(flags & 0x80),
                    "cec_power": "on"
                    if power == 0x180
                    else "off"
                    if power == 0x280
                    else None,
                    "hardware_type_code": struct.unpack_from("<I", r, 57)[0],
                }
            )
        out["ports"] = ports
    elif kind == 21:
        if not p or len(p) > 33 or p[0] != len(p) - 1:
            raise ValueError("Invalid temperature count")
        out["temperatures_celsius"] = list(p[1:])
    elif kind == 3:
        if len(p) % 38 or len(p) > 64 * 38:
            raise ValueError("Invalid detail record alignment")
        out["records"] = [
            {
                "index": p[o],
                "flag": p[o + 1],
                "label_a": text(p[o + 2 : o + 18]),
                "label_b": text(p[o + 18 : o + 34]),
                "hardware_type_code": struct.unpack_from("<I", p, o + 34)[0],
                "raw": p[o : o + 38].hex(),
            }
            for o in range(0, len(p), 38)
        ]
    elif kind == 22:
        if len(p) != 32:
            raise ValueError("Invalid PSU record length")
        values = struct.unpack_from("<6f", p)
        import math

        if not all(math.isfinite(v) for v in values):
            raise ValueError("Invalid PSU readings")
        out["rail_values"] = list(values)
        out["status_codes"] = list(p[24:32])
    else:
        out["raw"] = p.hex()
    return out
