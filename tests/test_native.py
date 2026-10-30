"""Native integration protocol/client tests without a Home Assistant runtime."""

import asyncio
import importlib.util
import json
import struct
import sys
import types
import unittest
from pathlib import Path

from aiohttp import ClientSession, TCPConnector, web
from aiohttp.abc import AbstractResolver

ROOT = Path(__file__).resolve().parents[1] / "custom_components/pulse_eight"
# Import transport modules independently of HA's integration setup module.
pkg = types.ModuleType("p8_native_test")
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
for name in ["client", "protocol", "state", "crash"]:
    spec = importlib.util.spec_from_file_location(
        "p8_native_test." + name, ROOT / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
from p8_native_test.client import (
    MatrixAuthError,
    MatrixClient,
    MatrixError,
    validate_cec,
    validate_host,
)
from p8_native_test.protocol import decode
from p8_native_test.state import MatrixState


def packet(kind, p):
    return b"P8\x01\0" + bytes(16) + struct.pack("<HH", kind, len(p)) + p


class ProtocolTests(unittest.TestCase):
    def test_identity_and_remote_configuration(self):
        body = (
            bytes(2)
            + b"FF88".ljust(16, b"\0")
            + b"P8SN123".ljust(16, b"\0")
            + b"3.10".ljust(16, b"\0")
            + bytes(4)
        )
        self.assertEqual(decode(packet(0, body))["identity"]["serial"], "P8SN123")
        r = (
            bytes([0, 1])
            + b"alpha".ljust(16, b"\0")
            + b"beta".ljust(16, b"\0")
            + struct.pack("<I", 0x602)
        )
        out = decode(packet(3, r))["records"][0]
        self.assertEqual(
            (out["label_a"], out["label_b"], out["hardware_type_code"]),
            ("alpha", "beta", 0x602),
        )

    def test_lengths_versions_and_unknown_packets(self):
        for b in [
            b"P8",
            b"P8\x02\0" + bytes(20),
            packet(3, b"a"),
            packet(22, b"1" * 31),
            packet(21, b"\x03\x31"),
        ]:
            with self.assertRaises(ValueError):
                decode(b)
        self.assertEqual(decode(packet(30, b"opaque"))["raw"], b"opaque".hex())

    def test_psu_rejects_nan(self):
        x = decode(packet(22, struct.pack("<6f", 12, 1, 5, 2, 3.3, 3) + bytes(8)))
        self.assertAlmostEqual(x["rail_values"][4], 3.3, places=5)
        with self.assertRaises(ValueError):
            decode(
                packet(22, struct.pack("<6f", float("nan"), 1, 2, 3, 4, 5) + bytes(8))
            )

    def test_cec_validation(self):
        self.assertEqual(validate_cec("4f:82:10:00"), "4F:82:10:00")
        for x in ["", "40;8f", "40:8F\rSystem/Restart", ":40", "40:" * 17, "40|8f"]:
            with self.assertRaises(ValueError):
                validate_cec(x)

    def test_host_validation(self):
        for x in [
            "http://matrix",
            "matrix:80",
            "matrix/path",
            "127.0.0.1",
            "0.0.0.0",
            "239.1.1.1",
            "999.999.1.1",
            "localhost",
            "matrix\n.example",
            "matrix@host",
        ]:
            with self.assertRaises(ValueError, msg=x):
                validate_host(x)
        self.assertEqual(validate_host("p8sn123.local"), "p8sn123.local")

    def test_state_identity_is_not_overwritten_by_edid_serial(self):
        s = MatrixState()
        s.response("/System/Details", {"Serial": "P8SN123", "Model": "FF88"})
        s.response(
            "/Port/Details/Output/0",
            {"Mode": "Output", "Bay": 0, "Serial": 1234, "Name": "TV"},
        )
        self.assertEqual(s.identity["Serial"], "P8SN123")

    def test_verified_signal_hpd_and_cec_power_flags(self):
        for flags, signal, hpd, power in [
            (0x2DC, True, True, "off"),
            (0x18, True, True, None),
            (0x10, False, True, None),
            (0x0C, True, False, None),
            (0x180, False, False, "on"),
        ]:
            r = (
                bytes([8, 1, 0, 7, 7])
                + b"Output 1".ljust(16, b"\0")
                + b"Display".ljust(16, b"\0")
                + b"ok".ljust(16, b"\0")
                + struct.pack("<II", flags, 0x641)
            )
            decoded = decode(packet(2, r))["ports"][0]
            self.assertEqual(
                (decoded["has_signal"], decoded["hpd"], decoded["cec_power"]),
                (signal, hpd, power),
            )

    def test_routing_unknown_does_not_invent_source(self):
        s = MatrixState()
        r = (
            bytes([8, 1, 0, 255, 255])
            + b"Output 1".ljust(16, b"\0")
            + b"TV".ljust(16, b"\0")
            + b"ok".ljust(16, b"\0")
            + bytes(8)
        )
        s.udp(decode(packet(2, r)), now=100)
        self.assertIsNone(s.ports["Output/0"]["ReceiveFrom"])
        self.assertEqual(s.last_udp, 100)
        s.udp(decode(packet(21, bytes([2, 45, 46]))), now=110)
        self.assertEqual(
            s.temperatures["1"], {"value": 46, "observed": 110, "transport": "udp"}
        )

    def test_monitoring_strips_log_and_retains_all_port_fields(self):
        s = MatrixState()
        p = {
            "Modules": [{"Mode": "Input", "Bay": 4, "EdidProfile": 11}],
            "log": "private data",
            "Temperature0": 50,
            "psu": [{"name": "12V", "value": 12, "ok": True}],
        }
        s.telemetry(p, now=100)
        self.assertNotIn("log", s.health)
        self.assertEqual(s.ports["Input/4"]["EdidProfile"], 11)
        self.assertEqual(s.psu[0]["value"], 12)

    def test_unknown_packet_cache_is_bounded(self):
        s = MatrixState()
        for kind in range(100, 500):
            s.udp(decode(packet(kind, b"opaque")), now=1)
        self.assertEqual(len(s.last_packets), 32)
        self.assertLessEqual(len(s.packet_counts), 65)
        self.assertEqual(sum(s.packet_counts.values()), 400)

    def test_bounds(self):
        for bay in [-1, 32, True]:
            with self.assertRaises(ValueError):
                MatrixState().port("Output", bay)


class Resolver(AbstractResolver):
    async def resolve(self, host, port=0, family=0):
        return [
            {
                "hostname": host,
                "host": "127.0.0.1",
                "port": port,
                "family": 2,
                "proto": 0,
                "flags": 0,
            }
        ]

    async def close(self):
        pass


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.auth_ok = True

        async def handler(request):
            self.active += 1
            self.max_active = max(self.active, self.max_active)
            self.calls.append((request.method, request.path))
            try:
                await asyncio.sleep(0.01)
                if request.path == "/Account/Login":
                    return web.json_response({"Result": self.auth_ok})
                if request.path == "/CEC/AutoPause":
                    return web.json_response({"Result": False})
                if request.path == "/forbidden":
                    return web.Response(status=403)
                if request.path == "/reject":
                    return web.json_response({"Result": False, "ErrorMessage": "No"})
                if request.path == "/redirect":
                    return web.Response(
                        status=302, headers={"Location": "/not_allowed"}
                    )
                if request.path == "/oversize":
                    return web.Response(body=b"x" * 2_000_001)
                if request.path == "/invalid":
                    return web.Response(text="not JSON")
                if request.path == "/System/Details":
                    return web.json_response(
                        {
                            "Result": True,
                            "Serial": "P8SN123",
                            "Model": "FF88",
                            "Version": "3.10.5-1",
                        }
                    )
                return web.json_response({"Result": True})
            finally:
                self.active -= 1

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.session = ClientSession(connector=TCPConnector(resolver=Resolver()))
        self.client = MatrixClient(self.session, "matrix.test", port=port)

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def test_serialization(self):
        await asyncio.gather(*(self.client.request("/get") for _ in range(5)))
        self.assertEqual(self.max_active, 1)

    async def test_identity(self):
        self.assertEqual((await self.client.identity())["Serial"], "P8SN123")

    async def test_mutation_failure_no_retry(self):
        with self.assertRaises(MatrixError):
            await self.client.request("/reject", method="POST", payload={})
        self.assertEqual(self.calls, [("POST", "/reject")])

    async def test_password_failure(self):
        self.auth_ok = False
        self.client.password = "bad"
        with self.assertRaises(MatrixAuthError):
            await self.client.identity()
        self.assertEqual(self.calls, [("POST", "/Account/Login")])

    async def test_false_autopause_is_valid_state(self):
        self.assertFalse((await self.client.request("/CEC/AutoPause"))["Result"])

    async def test_redirect_invalid_and_oversize(self):
        for path in ["/redirect", "/invalid", "/oversize"]:
            with self.assertRaises(MatrixError):
                await self.client.request(path)
        self.assertNotIn(("GET", "/not_allowed"), self.calls)


# Cloud transport has no HA imports and can be exercised end-to-end locally.
spec = importlib.util.spec_from_file_location("p8_native_test.cloud", ROOT / "cloud.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
from p8_native_test.cloud import Decoder, LocalCloud


class CloudTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = types.SimpleNamespace(
            serial="P8SN123",
            options={"cloud_port": 0, "monitoring_port": 0, "source_filter": True},
            source_ips={"127.0.0.1"},
            client=types.SimpleNamespace(lock=asyncio.Lock()),
            state=MatrixState(),
            notify=lambda: None,
            crashes=[],
        )

        async def capture_crash(log):
            self.hub.crashes.append(log)

        self.hub.capture_crash = capture_crash
        self.cloud = LocalCloud(self.hub)
        await self.cloud.start()
        self.port = self.cloud.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        await self.cloud.stop()

    async def test_fragmented_keepalive_and_json(self):
        d = Decoder()
        self.assertEqual(d.feed(b"no"), [])
        self.assertEqual(d.feed(b'op{"Result":tr'), [None])
        self.assertEqual(d.feed(b"ue}\r\0"), [{"Result": True}])

    async def test_wrong_identity_closed(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"Serial":"P8OTHER"}\r\0')
        await w.drain()
        self.assertEqual(await asyncio.wait_for(r.read(), 1), b"")
        self.assertFalse(self.cloud.connected)
        w.close()
        await w.wait_closed()

    async def test_native_query_and_readonly_boundary(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"Result":true,"Serial":"P8SN123","Model":"FF88"}\r\0')
        await w.drain()

        async def device():
            command = await r.readuntil(b"\r")
            self.assertEqual(command, b"system/details\r")
            w.write(b'noop{"Result":true,"Serial":"P8SN123","Model":"FF88"}\r\0')
            await w.drain()

        await asyncio.sleep(0.02)
        task = asyncio.create_task(device())
        result = await self.cloud.query("/System/Details")
        await task
        self.assertEqual(result["Serial"], "P8SN123")
        with self.assertRaises(MatrixError):
            await self.cloud.query("/System/Restart")
        w.close()
        await w.wait_closed()

    async def test_monitoring_newlines_and_no_update(self):
        site = next(iter(self.cloud.runner.sites))
        port = site._server.sockets[0].getsockname()[1]
        async with ClientSession() as s:
            async with s.post(
                f"http://127.0.0.1:{port}/Update/?serial=P8SN123",
                data=b'{"Modules":[],"Temperature0":50,"log":"line1\nline2"}',
            ) as r:
                self.assertEqual(r.status, 200)
                self.assertFalse((await r.json())["Update"])
            async with s.post(
                f"http://127.0.0.1:{port}/Update/?serial=WRONG", json={}
            ) as r:
                self.assertEqual(r.status, 403)
        self.assertEqual(self.hub.state.temperatures["0"]["value"], 50)
        self.assertNotIn("log", self.hub.state.health)

    async def post(self, body, serial="P8SN123"):
        site = next(iter(self.cloud.runner.sites))
        port = site._server.sockets[0].getsockname()[1]
        async with (
            ClientSession() as s,
            s.post(f"http://127.0.0.1:{port}/Update/?serial={serial}", data=body) as r,
        ):
            return r.status

    async def test_monitoring_rejects_unlisted_source_before_parsing(self):
        self.hub.source_ips = {"192.0.2.1"}
        self.assertEqual(await self.post(b'{"Temperature0":50}'), 403)
        self.assertEqual(await self.post(b"not json"), 403)
        self.assertIsNone(self.hub.state.last_seen)

    async def test_filter_is_off_by_default(self):
        self.hub.options["source_filter"] = False
        self.hub.source_ips = set()
        self.assertEqual(await self.post(b'{"Temperature0":50}'), 200)

    async def test_monitoring_fails_closed_without_known_source(self):
        self.hub.source_ips = set()
        self.assertEqual(await self.post(b"{}"), 403)

    async def test_trusted_sources_option_allows_gateway_nat(self):
        self.hub.source_ips = {"192.0.2.1"}
        self.hub.options["trusted_sources"] = "198.51.100.9, 127.0.0.1"
        self.assertEqual(await self.post(b'{"Temperature0":50}'), 200)
        self.hub.options["trusted_sources"] = "garbage,,198.51.100.9"
        self.assertEqual(await self.post(b'{"Temperature0":50}'), 403)

    async def test_native_socket_rejects_unlisted_source(self):
        self.hub.source_ips = {"192.0.2.1"}
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"Serial":"P8SN123"}\r')
        await w.drain()
        try:
            self.assertEqual(await asyncio.wait_for(r.read(100), 2), b"")
        except ConnectionResetError:
            pass  # closed with unread data: the kernel sends a reset
        self.assertFalse(self.cloud.connected)
        w.close()

    async def test_crash_report_is_captured_not_exposed(self):
        log = "time: 29/10/2026 15:55:16, uptime: 8194 seconds\nbus fault: X\n\n  R0:1"
        body = json.dumps({"Temperature0": 50, "log": log}).replace("\\n", "\n")
        self.assertEqual(await self.post(body.encode()), 200)
        self.assertEqual(self.hub.crashes, [log])
        self.assertEqual(self.hub.state.temperatures["0"]["value"], 50)
        self.assertNotIn("log", self.hub.state.health)

    async def test_crash_capture_failure_does_not_lose_telemetry(self):
        async def boom(log):
            raise OSError("disk full")

        self.hub.capture_crash = boom
        self.assertEqual(await self.post(b'{"Temperature0":51,"log":"x"}'), 200)
        self.assertEqual(self.hub.state.temperatures["0"]["value"], 51)

    async def test_non_string_log_is_ignored(self):
        self.assertEqual(await self.post(b'{"Temperature0":52,"log":{"a":1}}'), 200)
        self.assertEqual(self.hub.crashes, [])

    async def test_keepalive_only_connection_gets_one_identity_challenge(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b"noop")
        await w.drain()
        self.assertEqual(
            await asyncio.wait_for(r.readuntil(b"\r"), 1), b"system/details\r"
        )
        w.write(b'{"Result":true,"Serial":"P8SN123","Model":"FF88"}\r\0')
        await w.drain()
        await asyncio.sleep(0.02)
        self.assertTrue(self.cloud.connected)
        self.assertFalse(self.hub.client.lock.locked())
        w.close()
        await w.wait_closed()

    async def test_noops_cannot_extend_identity_deadline_forever(self):
        from unittest.mock import patch

        with patch.object(LocalCloud, "HANDSHAKE_TIMEOUT", 0.08):
            r, w = await asyncio.open_connection("127.0.0.1", self.port)
            w.write(b"noop")
            await w.drain()
            await r.readuntil(b"\r")
            for _ in range(3):
                await asyncio.sleep(0.015)
                w.write(b"noop")
                await w.drain()
            self.assertEqual(await asyncio.wait_for(r.read(), 1), b"")
            self.assertFalse(self.hub.client.lock.locked())
            w.close()
            await w.wait_closed()

    async def test_stop_closes_an_active_native_connection(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"Result":true,"Serial":"P8SN123","Model":"FF88"}\r\0')
        await w.drain()
        await asyncio.sleep(0.02)
        self.assertTrue(self.cloud.connected)
        await asyncio.wait_for(self.cloud.stop(), 2)
        self.assertEqual(await asyncio.wait_for(r.read(), 1), b"")
        w.close()
        await w.wait_closed()

    async def test_unload_closes_ports(self):
        await self.cloud.stop()
        with self.assertRaises(OSError):
            await asyncio.open_connection("127.0.0.1", self.port)
