"""Regression tests for hostile responses, cancellation and advisory updates."""

import asyncio
import importlib
import json
import unittest
from unittest.mock import patch

import test_native as native
from aiohttp import ClientSession, web

client = importlib.import_module("p8_native_test.client")
update = importlib.import_module("p8_native_test.update_client")


class AdversarialClientTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native.ClientTests.asyncSetUp
    asyncTearDown = native.ClientTests.asyncTearDown

    async def test_expired_auth_does_not_replay_mutation(self):
        self.client.password = "test"
        self.client._authenticated = True
        with self.assertRaises(client.MatrixAuthError):
            await self.client.request("/forbidden", method="POST", payload={})
        self.assertFalse(self.client._authenticated)
        await self.client.request("/get")
        self.assertEqual(
            self.calls,
            [("POST", "/forbidden"), ("POST", "/Account/Login"), ("GET", "/get")],
        )

    async def test_autopause_false_mutation_is_rejected(self):
        with self.assertRaises(client.MatrixError):
            await self.client.request(
                "/CEC/AutoPause", method="POST", payload={"AutoPause": True}
            )
        self.assertEqual(self.calls, [("POST", "/CEC/AutoPause")])


class StateValidationTests(unittest.TestCase):
    def test_bad_telemetry_has_no_partial_side_effects(self):
        for payload in [
            {"Modules": [None]},
            {"Modules": [{"Mode": "Output", "Bay": True}]},
            {"psu": {}},
            {"SystemFeatures": []},
            {"Temperature0": "nan"},
            {"Temperature99999": 20},
            {"Temperature0": float("nan")},
        ]:
            state = native.MatrixState()
            before = state.snapshot()
            with self.assertRaises(ValueError):
                state.telemetry(payload)
            self.assertEqual(state.snapshot(), before)

    def test_nested_features_and_sources_cannot_break_entity_properties(self):
        for payload in [
            {"SystemFeatures": {"Video": {"Input": []}}},
            {"SystemFeatures": {"Audio": {"Output": {"Bays": True}}}},
            {"Modules": [{"Mode": "Output", "Bay": 0, "LinkQuality": []}]},
            {"Modules": [{"Mode": "Output", "Bay": 0, "AllowedSources": [True]}]},
        ]:
            state = native.MatrixState()
            with self.assertRaises(ValueError):
                state.telemetry(payload)
            self.assertEqual(state.revision, 0)

    def test_unrecognized_telemetry_keys_do_not_accumulate(self):
        state = native.MatrixState()
        for i in range(200):
            state.telemetry({f"opaque_{i}": "private"})
        self.assertEqual(state.health, {})


class AdversarialCloudTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = native.CloudTests.asyncSetUp
    asyncTearDown = native.CloudTests.asyncTearDown

    async def identify(self):
        r, w = await asyncio.open_connection("127.0.0.1", self.port)
        w.write(b'{"Serial":"P8SN123","Model":"FF88"}\r')
        await w.drain()
        for _ in range(30):
            if (
                self.cloud.writer
                and self.cloud.writer.get_extra_info("peername")[1]
                == w.get_extra_info("sockname")[1]
            ):
                return r, w
            await asyncio.sleep(0.001)
        self.fail("Connection not identified")

    async def test_cancelled_query_closes_socket_before_next_query(self):
        r, w = await self.identify()
        task = asyncio.create_task(self.cloud.query("/System/Health"))
        self.assertEqual(await r.readuntil(b"\r"), b"system/health\r")
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await asyncio.wait_for(r.read(), 1), b"")
        with self.assertRaises(client.MatrixError):
            await self.cloud.query("/System/Health")
        w.close()
        await w.wait_closed()

    async def test_replacement_cannot_answer_old_pending_query(self):
        r, w = await self.identify()
        task = asyncio.create_task(self.cloud.query("/System/Health"))
        await r.readuntil(b"\r")
        r2, w2 = await self.identify()
        w2.write(b'{"Temperature0":99}\r')
        await w2.drain()
        with self.assertRaises(client.MatrixError):
            await task
        self.assertNotIn("0", self.hub.state.temperatures)
        w.close()
        w2.close()
        await w.wait_closed()
        await w2.wait_closed()

    async def test_getter_prefix_is_not_enough(self):
        for p in [
            "/Port/Details/Output/32",
            "/Port/Details/Output/0/System/Restart",
            "/EDID/Details/Output/0/../Install",
            "/Port/Details/Unknown/0",
        ]:
            with self.assertRaises(client.MatrixError):
                await self.cloud.query(p)

    async def test_invalid_monitoring_returns_400(self):
        site = next(iter(self.cloud.runner.sites))
        port = site._server.sockets[0].getsockname()[1]
        async with (
            ClientSession() as session,
            session.post(
                f"http://127.0.0.1:{port}/?serial=P8SN123", json={"Modules": [None]}
            ) as response,
        ):
            self.assertEqual(response.status, 400)
        self.assertIsNone(self.hub.state.last_seen)


class UpdateMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reply = {"Update": False}
        self.status = 200
        self.requests = []

        async def handler(request):
            self.requests.append((request.method, dict(request.query)))
            if self.status == 302:
                return web.Response(
                    status=302, headers={"Location": "http://127.0.0.1/no"}
                )
            return web.Response(
                body=self.reply
                if isinstance(self.reply, bytes)
                else json.dumps(self.reply).encode(),
                status=self.status,
            )

        app = web.Application()
        app.router.add_route("*", "/Update/", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = (
            f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/Update/"
        )
        self.session = ClientSession()
        self.identity = {
            "Serial": "P8SNTEST",
            "Version": "3.10.5-1",
            "Model": "FF88",
            "VID": "P8",
            "password": "DO NOT SEND",
            "log": "DO NOT SEND",
        }
        self.patcher = patch.object(update, "UPDATE_ENDPOINT", self.url)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        await self.session.close()
        await self.runner.cleanup()

    async def test_current_and_newer_metadata_and_minimal_disclosure(self):
        result = await update.fetch_update(self.session, self.identity)
        self.assertEqual(
            result, {"installed": "3.10.5-1", "latest": "3.10.5-1", "available": False}
        )
        self.assertEqual(
            self.requests[0],
            (
                "GET",
                {
                    "app": "3.10.5",
                    "serial": "P8SNTEST",
                    "model": "FF88",
                    "vendor": "P8",
                    "apphw": "F8",
                },
            ),
        )
        self.reply = {
            "Update": True,
            "UpdateURL": "http://firmware.pulse-eight.com/MatrixOSUpdates/3/matrixos-F8-3.10.6.bin",
        }
        self.assertEqual(
            (await update.fetch_update(self.session, self.identity))["latest"], "3.10.6"
        )
        self.assertEqual(len(self.requests), 2)  # No request to image URL.

    async def test_invalid_metadata_redirects_oversize_and_downgrade(self):
        values = [
            {"Update": "false"},
            [],
            {"Update": True, "UpdateURL": "http://evil.example/file"},
            {
                "Update": True,
                "UpdateURL": "http://firmware.pulse-eight.com/MatrixOSUpdates/3/matrixos-OTHER-3.10.6.bin",
            },
            {
                "Update": True,
                "UpdateURL": "http://firmware.pulse-eight.com/MatrixOSUpdates/3/matrixos-F8-3.10.4.bin",
            },
            {"Update": True, "UpdateURL": {}},
            b"x" * 16385,
            b"[" * 2000,
        ]
        for value in values:
            self.reply = value
            with self.assertRaises(client.MatrixError):
                await update.fetch_update(self.session, self.identity)
        self.status = 302
        with self.assertRaises(client.MatrixError):
            await update.fetch_update(self.session, self.identity)

    async def test_unknown_family_does_not_report_false_up_to_date(self):
        self.identity["Model"] = "Unknown"
        with self.assertRaisesRegex(client.MatrixError, "hardware family"):
            await update.fetch_update(self.session, self.identity)
        self.assertFalse(self.requests)
