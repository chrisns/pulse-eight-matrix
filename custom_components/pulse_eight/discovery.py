"""Shared native UDP receiver, passive device discovery and fanout."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from homeassistant.const import EVENT_HOMEASSISTANT_STOP

from .const import DOMAIN
from .protocol import decode


class Broker(asyncio.DatagramProtocol):
    def __init__(self, hass, port):
        self.hass, self.port = hass, port
        self.transport = None
        self.hubs = set()
        self.identities = OrderedDict()
        self.pending = OrderedDict()
        self.discovered = {}
        self.invalid_packets = 0
        self._flows = set()
        self._last_event = {}
        self._unsub_stop = None

    def datagram_received(self, data, addr):
        try:
            packet = decode(data)
        except (ValueError, IndexError):
            self.invalid_packets += 1
            return
        packet["peer"] = {"ip": addr[0], "port": addr[1]}
        header = packet["source_header"]
        kind = packet["type"]
        if kind == 0:
            serial = packet["identity"]["serial"]
            if not re.fullmatch(r"P8[A-Za-z0-9_-]{1,30}", serial):
                return
            self.identities[header] = serial
            self.identities.move_to_end(header)
            while len(self.identities) > 64:
                self.identities.popitem(last=False)
            self.discovered[serial] = {"host": addr[0], **packet["identity"]}
            if len(self.discovered) > 64:
                self.discovered.pop(next(iter(self.discovered)))
        serial = self.identities.get(header)
        if serial is None:
            cache = self.pending.setdefault(header, {})
            if kind in (2, 3, 21, 22):
                cache[kind] = packet
            while len(self.pending) > 32:
                self.pending.popitem(last=False)
        for hub in tuple(self.hubs):
            # Direct source matching is valid before the first identity datagram.
            # Docker's UDP proxy changes the IP; a serial-bound packet header then identifies it.
            if (serial == hub.serial) or (serial is None and addr[0] in hub.source_ips):
                if kind == 0 and serial != hub.serial:
                    continue
                if kind == 0:
                    for previous in self.pending.pop(header, {}).values():
                        hub.udp(previous)
                hub.udp(packet)
        if (
            kind == 0
            and len(self._flows) < 64
            and serial not in self._flows
            and not any(h.serial == serial for h in self.hubs)
        ):
            self._flows.add(serial)
            # Discovery only proposes a UI flow. It never configures or controls a matrix.
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "integration_discovery"},
                    data={
                        "serial": serial,
                        "host": addr[0],
                        "model": packet["identity"]["model"],
                    },
                )
            )

    def close(self):
        if self._unsub_stop:
            self._unsub_stop()
            self._unsub_stop = None
        if self.transport:
            self.transport.close()
            self.transport = None


async def get_broker(hass, port=8811):
    runtime = hass.data.setdefault(
        DOMAIN + "_runtime", {"brokers": {}, "hubs": {}, "lock": asyncio.Lock()}
    )
    async with runtime["lock"]:
        if port not in runtime["brokers"]:
            broker = Broker(hass, port)
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: broker, local_addr=("0.0.0.0", port)
            )
            broker.transport = transport
            runtime["brokers"][port] = broker

            async def stop_receiver(_):
                broker._unsub_stop = None
                broker.close()

            broker._unsub_stop = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, stop_receiver
            )
        return runtime["brokers"][port]
