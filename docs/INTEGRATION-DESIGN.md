# Native integration design and validation

The installable component is `custom_components/pulse_eight`.

## Transport separation

- HTTP: serialized local client, one startup inventory, explicit user controls and refreshes. No periodic HTTP scheduler. A rejected or timed-out mutation is never automatically retried. If a command succeeds but its status readback fails, the error explicitly says the command was accepted.
- UDP: one shared socket per configured listen port; identity/serial association and per-matrix fanout. Docker proxy source-address rewriting is handled by associating the opaque packet header with an identity datagram. This is not cryptographic authentication.
- Optional TCP cloud: accepts a native connection. If it begins with keepalives rather than an identity, asks for `system/details` once. The handshake shares the HTTP command lock, has an absolute deadline, and cannot be kept alive indefinitely with noops. Queries are explicitly requested getters only. A query timeout or cancellation closes that connection to avoid assigning its late reply to another query.
- Optional HTTP monitoring: accepts the native serial-tagged POST and updates richer snapshots. Tolerates the firmware's literal newlines in its JSON log string. A log that is a crash report is written to a file under the HA config directory and raises one notification per distinct crash; it is never placed in entity state or diagnostics. Replies Update=false.
- Optional source filter: off by default. When on, both listeners accept only the matrix's resolved addresses plus any `trusted_sources`, and reject everything else before parsing. Behind NAT the matrix may appear from the gateway's address, which is what `trusted_sources` is for. No vendor forwarding or firmware download is implemented. A separate optional advisory metadata client performs daily vendor checks from HA.

A 30-second local availability check expires cached connectivity without making a device request. No background refresh loop is used.

## Decoded UDP

Packets have a 24-byte header: P8 magic, a supported version, an opaque sender header, little-endian message type at offset 20 and payload length at offset 22. Lengths and record alignment are checked before decoding. Oversized record counts are rejected.

| Type | Interpretation |
| --- | --- |
| 0 | Fixed identity record: model, serial, firmware version |
| 2 | 61-byte port records: mode/bay, names, routing bytes, reported status, flags and hardware-type code |
| 3 | 38-byte remote-configuration records: index, flag, two labels and hardware code; not treated as video signal metadata |
| 21 | Count-prefixed unsigned temperature values in Celsius |
| 22 | Six float values and eight status codes from the firmware's supply encoder. Raw rail ordering/status semantics are not promoted into named HA supply measurements without additional validation |
| Other types | Exact raw payload retained, with per-type counts; no speculative CEC or IR interpretation |

Type-2 flag meanings were checked against HTTP data across all 18 ports of the tested unit:

- 0x08: matrix-reported signal present.
- 0x10: reported HPD state. This is not a guarantee that a picture is displayed.
- CEC power mask 0x380: 0x180 means on/transitioning on; 0x280 means standby/transitioning off. Other combinations do not invent an on/off state. The firmware merges the CEC steady and transitional states, so HA reports on/off, not an invented transition duration.

CEC power from UDP is preferred only when it is at least as recent as an HTTP detail snapshot. Ports without a usable UDP power report retain the explicitly timestamped detail snapshot/unknown state. Raw CEC receive and remote-button events remain unverified.

The latest packet cache is capped at 32 message types, counters are bounded by a fixed number of type buckets, and pending pre-identity senders are bounded. Raw diagnostic events are off by default and have a flood ceiling. Diagnostics use an explicit allowlist that omits credentials, addresses, serials, names, raw frames and log text.

## HA behavior

Config flows support manual, DHCP, SSDP and native UDP discovery confirmation. Serial-number unique IDs prevent duplicates; reconfigure and reauthentication keep the original identity. Default setup does not enable cloud listeners or alter network/device configuration. Options reload the entry. Multiple matrices share UDP reception; give optional TCP/monitoring listeners unique ports per entry.

The parent matrix and each input/output have stable device/entity identifiers. Input/output device names follow matrix labels while preserving HA user-assigned names. Routing, EDID, IR profiles, CEC behavior and panel settings use typed entities/actions. Audio DSP number entities are created only when compatible ports are reported; physical DSP hardware has not been tested.

On unload, accepted cloud connections are closed and handler tasks completed before waiting for the TCP server. This ordering is required by Python 3.13+; otherwise an active socket can prevent entry reload from finishing. The UDP receiver remains alive while another matrix still uses it and is closed after the last entry unloads.

## Testing

- Transport and protocol tests run on the host: fragmentation, malformed packets, bounded caches, identity checks, independent EDID serials, CEC format validation, serialized HTTP, no retry of failed mutations, blocked redirects, response size limits, keepalive-only handshakes, source filtering, crash report archiving and active-socket shutdown.
- HA-runtime tests run inside the Home Assistant container image: discovery confirmation, duplicate prevention, options defaults and conflicts, entity behaviour, named-port actions, shared listener unload, callback scheduling, crash notifications and diagnostics redaction.
- The integration has been used against an FF88 on MatrixOS 3.10.5-1: a route change and its restoration, an EDID profile change and its restoration, raw CEC power queries, and UDP push updates arriving without any polling.

Not covered: other models and firmware, audio DSP hardware, multicast SSDP discovery, and any whole-device emulation. This component is not a firmware patch and does not implement every vendor admin operation, raw EDID upload or IR learning.

## Hardening

Cloud cancellation, connection replacement, strict getter addressing, atomic monitoring validation, response shape validation, discovery serial mismatch, auth-session expiry, partial refresh reporting and shutdown cleanup are covered in the [adversarial review](ADVERSARIAL-REVIEW.md). The update entity and the daily vendor metadata check are independent of the local cloud collector. They bound response size, reject redirects and unexpected firmware URLs, and deduplicate notifications. There is no image download or installation path.

The tested FF88 reports IR `Blast=false`, but that field is a constant in the main firmware. Profile selection is not arbitrary IR transmission, and no route from the network to an IR transmitter has been verified.
