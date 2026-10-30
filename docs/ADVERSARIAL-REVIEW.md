# Adversarial review of the native integration

Review date: 29 October 2026. Source changes are in 0.3.0. This is a bounded code/protocol review and regression suite, not a proof that the integration or legacy firmware has no remaining vulnerabilities.

| Finding | Change and regression coverage |
| --- | --- |
| Cancelling a TCP query could leave its late reply eligible for the next query, since the protocol has no request IDs | Close the connection on cancellation as well as timeout; test with real local TCP sockets |
| A replacement cloud connection could answer an outstanding query for the old connection | Bind pending replies to their writer; fail the outstanding request when replacing a connection; socket regression test |
| Cloud getter prefix checks allowed malformed extra path components | Anchor the complete supported getter grammar, modes and bay range; reject path suffixes before transmission |
| The AutoPause false-state exception also accepted rejected POST mutations | Restrict that exception to GET; test POST Result=false rejects without retry |
| An expired HTTP auth state remained cached | Invalidate the local authenticated flag on HTTP 401/403; a later explicit request may log in again; never replay the failed mutation |
| Monitoring nested types could throw during state ingestion or partly update state before an invalid port | Validate modules, supply lists, feature hierarchy and temperatures before mutating state; reject malformed uploads with HTTP 400 |
| Unrecognized top-level telemetry keys accumulated in health state | Allowlist retained health fields; regression test hundreds of changing keys |
| Invalid features or port containers could crash entity code | Validate nested feature and port containers, source lists and textual fields; surface invalid device responses as transport errors |
| Discovery could advertise serial A but configure a different serial B at confirmation | Compare HTTP identity with the advertised serial; reject malformed SSDP port syntax without throwing |
| Live HA API verification | Retain the current singular DeviceEntry.config_entry_id API; reject an attempted plural-property change after the live runtime flagged it as deprecated |
| A failing entity callback interrupted updates to every later entity | Isolate callback failures, log them and continue other entities; HA-runtime regression test |
| Port inventory stopped early but Refresh returned success | Return a completion boolean and propagate it in the action response |
| Registry creation failure or HA shutdown could leave listeners/tasks running | Clean up a started hub on registry errors; stop hub tasks and optional listeners on HA stop; suppress post-stop state updates |
| Update checking could inadvertently reintroduce firmware download/installation or endpoint injection | Fixed metadata endpoint, bounded JSON, no redirects, strict offered-image URL/family/version validation, no image fetch, read-only HA update entity |
| Restarts/manual refreshes could repeatedly poll the vendor or notify every day for the same offer | Persist last attempt and notified version, serialize checks, daily gating and one-minute manual limit; isolated HA tests cover restarts/dedupe/failure/disabled mode |

The existing no-retry mutation policy, 45-second serialized local HTTP calls, 30-second absolute TCP identity deadline, bounded raw caches, disabled-by-default raw events and explicit diagnostic redaction remain in place. The default local cloud listeners remain off for new installs.

## Remaining material limits

- UDP, native TCP and monitoring HTTP have no cryptographic device authentication. A malicious peer on an allowed LAN can forge identity/serial data. Input validation cannot fix that protocol property. Enforce network restrictions and never expose listeners publicly.
- Advisory update metadata is HTTP and can be forged or suppressed on its path. This creates no firmware installation path in HA. The daily request discloses the matrix's serial/version/model/family to the vendor and can be disabled.
- Only FF88 / MatrixOS 3.10.5-1 and the local HA 2026.10.2 deployment have live validation. Other firmware, hardware families, DSP hardware and network topologies remain unverified.
- CEC acknowledgement is not recipient action or raw CEC reception. The main firmware has no verified network route to the secondary MCU's IR transmitter, so the integration cannot honestly expose arbitrary IR yet. `Blast=false` is hard-coded and is not proof that the transmitter hardware is absent.
- Tests are independent transport/socket, failure and HA-runtime probes, not a whole-device emulator or a hardware fault-injection campaign.
- Other clients, such as REST sensors or a browser on the vendor UI, can issue requests concurrently. This integration's lock cannot serialize them.

## Verified outcome

The suite has 69 tests: protocol, transport and security tests that run on the host, and Home Assistant runtime tests that run in the HA container image. The HA tests exercise the real update state renderer and the real persistent-notification component, including notification creation and dismissal.

Checked against a real FF88 on HA 2026.10: the entry loads, UDP arrives, the optional TCP cloud connection establishes, the firmware check reaches the vendor metadata service, and invalid CEC action input is rejected before transmission. No real new-version offer exists to test against, so the positive-update notification branch uses a synthetic response in the HA tests.

A feature-flag type error found during the first live restart was corrected and covered by a regression test. A proposed plural device-registry API change was reverted after the HA runtime flagged it as deprecated.
