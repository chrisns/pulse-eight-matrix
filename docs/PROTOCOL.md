# MatrixOS protocol notes

What this integration relies on, as observed on an FF88 running MatrixOS 3.10.5-1. Other models and firmware have not been tested. Everything here comes from vendor documentation and from watching the device's own traffic.

## Summary

| Interface | Used for | Notes |
| --- | --- | --- |
| UDP 8811, subnet broadcast | State pushes: identity, ports, routing, flags, CEC power, temperatures | Broadcast, so it only reaches a listener on the same layer 2 segment |
| HTTP/JSON, TCP 80 | Commands, and an optional startup inventory | One request at a time is kind to the device |
| Cloud channel, TCP 8989 | Optional local replacement for the vendor cloud | Plaintext, unauthenticated |
| Monitoring upload, HTTP POST to port 80 of the cloud host | Rich telemetry document, and crash reports | Plaintext, unauthenticated |
| RS232 | Vendor documented, not used here | 57600 8N1 |

Vendor API documentation lives on the vendor's monitoring portal (`https://monitoring.pulse-eight.com/APIDocs/Home`, sign-in required). Vendor CEC reference: <https://support.pulse-eight.com/support/solutions/articles/30000053359-understanding-cec-opcodes>.

## UDP 8811

Packets have a 24 byte header: `P8` magic, a version, an opaque sender header, a little-endian message type at offset 20 and a little-endian payload length at offset 22. The integration checks lengths and record alignment before decoding.

| Type | Meaning |
| --- | --- |
| 0 | Identity: model, serial, firmware version |
| 2 | 61 byte port records: mode and bay, names, routing bytes, status text, flags, hardware type code |
| 3 | 38 byte remote-configuration records |
| 21 | Count-prefixed temperatures in Celsius |
| 22 | Six floats and eight status codes from the supply monitor; rail ordering is not interpreted |
| other | Kept as raw bytes, never given a guessed meaning |

Type 2 flags: `0x08` signal present, `0x10` hot plug detected (which does not guarantee a picture). CEC power uses mask `0x380`: `0x180` is on or going on, `0x280` is standby or going to standby. Other values are reported as unknown.

The sender header is opaque, so the integration identifies a matrix by the serial in its identity packets rather than by source address. That keeps it working behind a container's UDP proxy. It is identification, not authentication.

## HTTP API

JSON over TCP 80. Bays are zero-based on the wire, one-based in the integration's actions.

| Request | Purpose |
| --- | --- |
| `GET /System/Details`, `/System/Features`, `/System/Health` | Identity, capabilities, health and temperatures |
| `GET /Port/List`, `/Port/Details/{Input\|Output}/{bay}` | Port inventory and details |
| `GET /Port/Set/{input}/{output}` | Route an input to an output |
| `GET /Port/EDID/Set/{profile}/{bay}` | Select an input's EDID profile |
| `GET /EDID/{Details\|Timings\|Audio\|HDMI\|HDR}/{mode}/{bay}` | EDID information |
| `GET /CEC/on\|off/{mode}/{bay}` | CEC power |
| `POST /cec/key/{mode}/{bay}` with `{"Code": n}` | CEC user control keys |
| `POST /cec/tx/Output/{bay}` with `{"Command":"40:8F"}` | Raw CEC frame |
| `GET /CEC/AutoPause`, `/CEC/ComboKeys`, `/CEC/Logging` | CEC settings |
| `GET /IR/Profiles`, `/IR/Details/{bay}`, `POST /IR/SetProfile/{bay}` | IR profile selection |
| `GET /Audio/Details` | Audio DSP ports, on models that have them |
| `GET /Monitoring/Settings`, `/Network/Settings` | Read-only settings |
| `POST /Cloud/Settings` with `{"server":..., "port":...}` | Where the matrix connects for its cloud channel |

A raw CEC transmit reply of `{"Result":true,"Message":"Transmitted"}` means the firmware accepted the frame, not that anything received it.

### Raw CEC frames

`40:8F` is a complete frame including the header: sender logical address 4, recipient 0 (the TV), opcode `8F` (Give Device Power Status). Destination `F` is broadcast. Choose a sender that suits the bus, because copying a sample sender can impersonate another device, and power, routing and key opcodes change the state of real equipment. Frames are 1 to 16 colon-separated hex bytes. `Output/0` selects the matrix bay, not a CEC logical address.

## Cloud channel, TCP 8989

The matrix opens a persistent connection to its configured server (the vendor's by default). There is no TLS and no server authentication.

- The matrix sends `noop` keepalives, and an unsolicited `system/details` JSON reply when it connects.
- A server sends a command as a line such as `system/details` ending in carriage return. The matrix replies with one JSON object ending in carriage return and a NUL byte.
- There are no request IDs, so a late reply to a cancelled request would be mistaken for the answer to the next one. The integration closes the connection on timeout or cancellation to prevent that.
- Only read-only getters are issued by the integration.

## Monitoring upload

The matrix periodically POSTs a JSON document to port 80 of the cloud host, with `?serial=<serial>` in the URL, and again at boot. The interval is advertised in `SystemFeatures.UpdInterval` (milliseconds, one hour on the tested unit). The document includes `Modules` (full port details), `IR`, `psu`, `Temperature0..4`, `Status` and `StatusMessage`, `Uptime`, `Failures`, `SystemFeatures`, network identity, `PDU` and `VideoXP`.

The integration answers `{"Time":"<unix>","Update":false,"ExecutionTime":"0"}` and never serves firmware.

### Crash reports

After a fault, the next upload carries a `log` field holding a crash report: a time and uptime line, the fault type, a register dump, memory ranges and a system log tail. The firmware writes raw newlines inside that JSON string, so it only parses with `json.loads(..., strict=False)`. The integration saves each distinct report to a file and raises a notification (see the README).

## Discovery

DHCP: hostname starting `p8sn` and the vendor's MAC prefix. SSDP: manufacturer `Pulse-Eight` or `Pulse Eight`. The matrix is always confirmed over HTTP before it is added.
