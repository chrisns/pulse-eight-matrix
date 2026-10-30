# Pulse-Eight Matrix for Home Assistant

> [!CAUTION]
> ## Security advisory: never give these matrices internet access
>
> **MatrixOS firmware on these matrices is dangerously insecure. Keep the matrix off the internet, off any network you do not fully control, and away from anything you care about.** In my testing of an FF88 on MatrixOS 3.10.5-1:
>
> - **There is no real authentication on many controls.** Raw CEC transmit, for one, works without logging in, so anything that can reach TCP 80 can send CEC frames, and changing the admin password is not a substitute for isolation.
> - **There is an unauthenticated memory corruption bug** in the raw CEC parser, reachable with one HTTP request. I confirmed on the device that crafted requests corrupt memory and reliably fault or reset it. See the [finding](docs/CEC-HEAP-OVERFLOW.md).
> - **Firmware updates are fetched over plain HTTP and I found no signature check.** The only integrity check I found is a CRC and a tar checksum, which stop accidents, not attackers. Anyone who can answer the matrix's update request can plausibly make it install their firmware. That includes whoever controls or poisons your DNS, anyone who can mis-route its traffic, and any machine sitting on its path. See the [assessment](docs/HTTP-THREAT-ASSESSMENT.md).
> - **The matrix is a client of the vendor's cloud over plaintext, and it phones out by itself.** A normal firewall that allows replies to outbound connections lets that traffic carry commands back in, with no port forward needed.
>
> **What that means in practice.** Best case, a hostile or garbled update leaves you with a very expensive bricked matrix. Worst case, an attacker's firmware runs on a device on your network, which could attack other hosts, relay commands, and make the box part of a botnet. I have not built or tested hostile firmware, so that worst case is a credible risk, not a demonstrated exploit. The weaknesses above are real regardless.
>
> **What to do.**
> 1. Put the matrix on its own VLAN with **no route to the internet, IPv4 or IPv6**, and allow only Home Assistant and your management host.
> 2. Block with **REJECT, not DROP**. Silently dropping its outbound traffic made one matrix hang waiting on its own DNS and cloud connections, run out of memory and fault. A reject lets it fail fast.
> 3. Never port forward to it, and never expose its web UI, this integration's listeners or Home Assistant's access to it to the internet.
> 4. Do not trust DNS for it: serve it no resolver, or one you control that cannot be poisoned. Confusing its DNS or routing is how an attacker gets in front of the update request.
> 5. **If your router cannot firewall it, a weaker fallback is plausible:** give the matrix a manual (not DHCP) network configuration on the same subnet as Home Assistant, with a gateway that is invalid, empty or genuinely unused, and no external DNS. With no working route off the subnet, the matrix cannot reach the internet or the vendor's servers, while Home Assistant on the same subnet still can. I have not proven this is sufficient on this firmware. It is **not isolation**: it does not protect other devices on that network, a separate IPv6 route may remain, and a compromised or malicious matrix could use another router it finds on the same segment. Use it only if nothing better is possible, and see the [fallback details](docs/NETWORK-SECURITY.md#basic-fallback-static-ipv4-with-no-working-gateway).
> 6. If you cannot isolate it at all, do not connect it to a network. Replacing it is cheaper than a compromised LAN.
>
> Installing this integration does not make the matrix safer. It does not change the matrix's settings or block anything. See [network protection](docs/NETWORK-SECURITY.md).

A native Home Assistant custom integration for Pulse-Eight MatrixOS HDMI matrices. It listens to the matrix's own UDP broadcasts, so state arrives as it changes with no polling, and it sends serialized local HTTP requests when you ask it to do something. No Pulse-Eight account, relay process or router controller is needed, and local control works offline.

This is an independent project. It is not made, supported or endorsed by Pulse-Eight or by Home Assistant. "Pulse-Eight" and its logo are trademarks of their owner and are used here only to identify the hardware this integration works with.

I run it against an **FF88 on MatrixOS 3.10.5-1** with Home Assistant **2026.10**. Other models and firmware are untested. Reports are welcome, and please use the redacted diagnostics download rather than pasting raw state, which contains your names and identifiers.

## Install

### HACS (custom repository)

1. In HACS, open the menu, choose **Custom repositories**, add `https://github.com/chrisns/pulse-eight-matrix` as an **Integration**.
2. Install **Pulse-Eight Matrix** and restart Home Assistant.
3. Go to **Settings, Devices & services, Add integration, Pulse-Eight Matrix**.

### Manual

Copy `custom_components/pulse_eight` into your Home Assistant `custom_components` directory, restart Home Assistant fully, then add the integration as above.

Enter the matrix's IPv4 address or hostname and, if you changed it, its admin password. No YAML is needed. The serial number is the unique ID, so the same matrix cannot be added twice. Home Assistant needs to reach the matrix on TCP 80, and the matrix's UDP broadcasts to port 8811 need to reach Home Assistant. On Linux, host networking is the simplest way to receive broadcasts. In a container with bridge networking, publish UDP 8811. Broadcasts do not cross VLANs, so Home Assistant needs a leg on the matrix's network.

DHCP and SSDP discovery are supported, and always ask you to confirm. Nothing is sent to the matrix during discovery other than read-only requests.

## What it provides

| Feature | How |
| --- | --- |
| Identity, port names, routing, signal and hot-plug flags, CEC power, temperatures | Pushed over UDP 8811 |
| Output source selection | Select entities and media player source selection |
| Input EDID profile | Select entities: dynamic, fixed, and clone-output profiles |
| Port names | Text entities |
| CEC control | Media players, remotes and a raw-frame action: power, navigation, media keys |
| Voltage, current, health, port and HDBaseT link detail | A startup inventory and explicit Refresh buttons, or the optional monitoring upload |
| EDID information | An action returning display metadata, timings, audio, HDMI and HDR data |
| IR profile selection | Existing profiles only (see limits) |
| CEC settings and front-panel LEDs | Switches and selects |
| Audio volume and delay | Number entities on models that report audio DSP ports (untested on hardware) |
| Firmware availability | A daily advisory check, a read-only update entity and one notification per offered version. Nothing is downloaded or installed |
| Crash reports | Captured to files and announced with a notification (see below) |

The matrix is one device, with each input and output as a child device, so ports can have their own areas. Detail entities show when and how they were last observed. Unsupported ones stay unavailable rather than showing an invented value.

**Not verified:** raw CEC reception and remote button detection. CEC transmit acceptance only means the matrix accepted the request, not that a TV received it or changed state.

## Actions

Pick a port by name. The input and output fields of `set_route`, `set_edid` and `set_ir_profile` are entity pickers listing your ports by their names, so an automation reads like this:

```yaml
action: pulse_eight.set_route
data:
  output: media_player.output_2_lounge_tv_cec
  input: media_player.input_1_apple_tv_cec
```

Outputs are the TV-class CEC media players and inputs are the receiver-class ones. The matrix is worked out from the port you pick. Plain one-based numbers (matching the printed port numbers) plus a `device_id` also still work.

- `pulse_eight.set_route`: `output`, `input`.
- `pulse_eight.set_edid`: `input`, numeric `profile`.
- `pulse_eight.set_ir_profile`: `output`, existing profile ID.
- `pulse_eight.send_cec`: `device_id`, `mode`, `port`, `frame` (1 to 16 colon-separated hex bytes).
- `pulse_eight.get_port_details`: `device_id`, `mode`, `port`, optional `include_receiver` and `transport`.
- `pulse_eight.read_edid`: `device_id`, `mode`, `port`, optional `transport`. Supports an action response.
- `pulse_eight.check_firmware`: check the vendor metadata now (at most once a minute).
- `pulse_eight.refresh`: one explicit inventory pass.
- `pulse_eight.get_status`: the cached state, with no request to the matrix.

Remote commands: `up`, `down`, `left`, `right`, `select`, `home`, `menu`, `back`, `volume_up`, `volume_down`, `mute`, `play`, `pause`, `stop`, `record`, `rewind`, `fast_forward`, `next`, `previous`. Never retry a timed-out command automatically, because the matrix may already have acted on it.

## Optional local listeners and crash reports

In **Configure**, enable the local cloud listeners. Defaults:

- **TCP 18989**: the matrix's native cloud channel. Only explicitly requested read-only queries are sent.
- **TCP 18080**: the matrix's monitoring upload. It always replies "no update", and never serves firmware.

They never contact Pulse-Eight. Enabling them does not redirect anything: you point the matrix at Home Assistant yourself, either with its own cloud destination setting or with a router rule. If a router redirects the matrix's traffic on the same subnet, it usually needs source NAT as well, and then Home Assistant sees the router's address as the sender.

```sh
curl --request POST --header 'Content-Type: application/json' \
  --data '{"server":"YOUR_HA_LAN_IP","port":18989}' \
  http://YOUR_MATRIX_IP/Cloud/Settings
```

That changes the matrix's configuration, so save the old settings first. Never expose these unauthenticated legacy listeners to the internet.

**Crash reports.** These matrices can fault and reboot. When one does, its next monitoring upload carries a crash report: fault type, registers, memory ranges and a system log tail. The integration writes each distinct report to `<config>/pulse_eight_crashes/` as its own file (the newest 50 are kept) and raises one persistent notification with the fault line and the file path. The report is never put in entity state or diagnostics. A report that is re-sent is not archived twice.

**Source filter.** Off by default. When you turn on **Only accept listener connections from the matrix**, both listeners reject any peer that is not the matrix's address, before parsing anything. If your router rewrites the matrix's source address, list the address Home Assistant actually sees in **Additional trusted source addresses** (comma separated). With the filter on and no known address, nothing is accepted. This is a convenience on top of proper firewalling, not a replacement for it: the legacy protocols have no cryptographic authentication.

Raw UDP events (`pulse_eight_udp_packet`) are off by default for protocol investigation. They carry local names and identifiers and can be chatty.

## Firmware update notifications

A daily advisory check runs from Home Assistant, independently of any WAN block on the matrix. It sends only the serial, model, vendor, installed version and hardware family to the vendor's update metadata service. The vendor endpoint is plain HTTP and Home Assistant never downgrades or disables certificate checks to reach it, so treat the answer as advice, not authenticated evidence. Returned URLs are validated and never fetched. The automatic hardware family mapping is proven only for **FF88 to F8**: other models need the family set under Configure, or the check reports a visible failure. Turn the check off for fully offline use.

## Limits

- IR sending is not exposed. The main firmware has no verified route to the IR transmitter.
- Firmware upload, factory reset, raw EDID upload, IR learning and network reconfiguration are not implemented.
- HTTP controls are serialized per matrix, but other clients (a browser, REST sensors) are outside that lock. Avoid hammering this small embedded device.
- Unknown UDP types are kept as raw data and never given a guessed meaning.

## Development

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -q
```

The Home Assistant runtime tests need Home Assistant and are skipped on the host. Run everything in the HA image with:

```sh
docker run --rm -v "$PWD":/repo -w /repo --entrypoint python3 \
  ghcr.io/home-assistant/home-assistant:2026.10.2 -m unittest discover -s tests
```

Notes: [protocol](docs/PROTOCOL.md), [design and testing](docs/INTEGRATION-DESIGN.md), [adversarial review](docs/ADVERSARIAL-REVIEW.md), [network protection](docs/NETWORK-SECURITY.md), and the firmware findings: [CEC heap overflow](docs/CEC-HEAP-OVERFLOW.md) and [firmware update threat assessment](docs/HTTP-THREAT-ASSESSMENT.md).

## Licence

MIT, see [LICENSE](LICENSE). Provided as is, with no warranty and no claim of vendor support or compatibility with every MatrixOS model.
