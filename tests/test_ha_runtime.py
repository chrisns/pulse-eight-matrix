"""HA-facing tests. Run in an isolated venv inside the HA development image."""

import asyncio
import inspect
import json
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import homeassistant

    HAS_HA = True
except ImportError:
    HAS_HA = False
if HAS_HA:
    from homeassistant.components.media_player import MediaPlayerState
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.pulse_eight import services
    from custom_components.pulse_eight.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from custom_components.pulse_eight.discovery import Broker
    from custom_components.pulse_eight.hub import MatrixHub
    from custom_components.pulse_eight.media_player import MatrixPlayer
    from custom_components.pulse_eight.remote import MatrixRemote
    from custom_components.pulse_eight.select import MatrixSelect
    from custom_components.pulse_eight.state import MatrixState


@unittest.skipUnless(HAS_HA, "Requires the isolated HA runtime")
class HARuntimeTests(unittest.IsolatedAsyncioTestCase):
    def hub(self):
        state = MatrixState()
        state.port("Input", 0).update(Name="Source", EdidProfile=11)
        state.port("Output", 0).update(
            Name="Display", ReceiveFrom=0, AllowedSources=[0], DPS=-2
        )
        return types.SimpleNamespace(
            state=state,
            serial="P8SNTEST",
            online=True,
            device_id="parent",
            client=types.SimpleNamespace(host="matrix.local"),
            cloud=None,
            route=AsyncMock(),
            edid=AsyncMock(),
            command=AsyncMock(),
            cec=AsyncMock(),
            options={},
            listeners=set(),
            fresh=lambda _: True,
        )

    async def test_source_selection_and_profile(self):
        h = self.hub()
        route = MatrixSelect(h, "route", "Source", "Output/0")
        self.assertEqual(route.options, ["1: Source"])
        self.assertEqual(route.current_option, "1: Source")
        await route.async_select_option("1: Source")
        h.route.assert_awaited_once_with(0, 0)
        with self.assertRaises(ServiceValidationError):
            await route.async_select_option("999: Unsupported")
        edid = MatrixSelect(h, "edid", "EDID", "Input/0")
        self.assertEqual(edid.current_option, "Dynamic")
        self.assertNotIn("HDR10 7.1", edid.options)

    async def test_udp_power_preferred_to_stale_snapshot(self):
        h = self.hub()
        p = h.state.port("Output", 0)
        p.update(DPS=1, CECPowerReported=True, CECPower="on")
        player = MatrixPlayer(h, "Output/0")
        remote = MatrixRemote(h, "Output/0")
        self.assertEqual(player.state, MediaPlayerState.ON)
        self.assertTrue(remote.is_on)
        p["CECPower"] = None
        self.assertIsNone(player.state)
        self.assertIsNone(remote.is_on)

    async def test_remote_validates_all_keys_before_send(self):
        h = self.hub()
        remote = MatrixRemote(h, "Output/0")
        with self.assertRaises(ServiceValidationError):
            await remote.async_send_command(["up", "bad"])
        h.command.assert_not_awaited()
        with self.assertRaises(ServiceValidationError):
            await remote.async_send_command(["up"], hold_secs=2)
        await remote.async_send_command(["up", "down"], delay_secs=0)
        self.assertEqual(h.command.await_count, 2)

    async def test_diagnostics_omit_private_data(self):
        h = self.hub()
        h.state.identity = {"Serial": "P8SNTEST", "Model": "FF88", "Version": "3.10.5"}
        h.state.port("Input", 0)["Name"] = "PRIVATE NAME"
        entry = types.SimpleNamespace(
            runtime_data=h,
            data={
                "host": "192.168.12.34",
                "password": "PRIVATE PASSWORD",
                "serial": "P8SNTEST",
            },
            options={},
        )
        text = json.dumps(await async_get_config_entry_diagnostics(None, entry))
        for sensitive in [
            "P8SNTEST",
            "192.168.12.34",
            "PRIVATE PASSWORD",
            "PRIVATE NAME",
        ]:
            self.assertNotIn(sensitive, text)

    async def test_action_converts_one_based_port(self):
        h = self.hub()
        registry = MagicMock()
        registry.async_get.return_value = types.SimpleNamespace(config_entry_id="entry")
        hass = types.SimpleNamespace(
            services=MagicMock(), data={"pulse_eight_runtime": {"hubs": {"entry": h}}}
        )
        hass.services.has_service.return_value = False
        with patch.object(services.dr, "async_get", return_value=registry):
            services.async_register_services(hass)
            handler = hass.services.async_register.call_args_list[0].args[2]
            await handler(
                types.SimpleNamespace(
                    service="send_cec",
                    data={
                        "device_id": "dev",
                        "mode": "Output",
                        "port": 1,
                        "frame": "40:8F",
                    },
                )
            )
            h.cec.assert_awaited_once_with("Output", 0, "40:8F")
            with self.assertRaises(ServiceValidationError):
                await handler(
                    types.SimpleNamespace(
                        service="send_cec",
                        data={
                            "device_id": "dev",
                            "mode": "Output",
                            "port": 32,
                            "frame": "40:8F",
                        },
                    )
                )

    async def test_route_accepts_named_port_entities(self):
        h = self.hub()
        entities = {
            "media_player.output_1_tv_cec": ("pulse_eight", "P8SNTEST_player_Output/0"),
            "media_player.input_1_box_cec": ("pulse_eight", "P8SNTEST_player_Input/0"),
            "media_player.other": ("other", "x_player_Output/0"),
        }

        def entry(entity_id):
            platform, uid = entities[entity_id]
            return types.SimpleNamespace(
                platform=platform, unique_id=uid, config_entry_id="entry"
            )

        registry = MagicMock()
        registry.async_get.side_effect = entry
        hass = types.SimpleNamespace(
            services=MagicMock(), data={"pulse_eight_runtime": {"hubs": {"entry": h}}}
        )
        hass.services.has_service.return_value = False
        with patch.object(services.er, "async_get", return_value=registry):
            services.async_register_services(hass)
            calls = {c.args[1]: c for c in hass.services.async_register.call_args_list}
            handler = calls["set_route"].args[2]
            schema = calls["set_route"].kwargs["schema"]

            def call(**data):
                return types.SimpleNamespace(service="set_route", data=schema(data))

            await handler(
                call(
                    output="media_player.output_1_tv_cec",
                    input="media_player.input_1_box_cec",
                )
            )
            h.route.assert_awaited_once_with(0, 0)
            for bad in [
                {"output": "media_player.input_1_box_cec", "input": 1},
                {"output": "media_player.other", "input": 1},
                {"output": 1, "input": 1},
            ]:
                with self.assertRaises(ServiceValidationError):
                    await handler(call(**bad))
            self.assertEqual(h.route.await_count, 1)

    async def test_broker_cross_device_and_proxy_identity(self):
        import struct

        hass = types.SimpleNamespace(
            async_create_task=asyncio.create_task,
            config_entries=types.SimpleNamespace(
                flow=types.SimpleNamespace(async_init=AsyncMock())
            ),
        )
        broker = Broker(hass, 8811)
        h = self.hub()
        h.source_ips = {"192.168.12.34"}
        h.udp = MagicMock()
        fixture = type("HubFixture", (), {})()
        fixture.__dict__.update(vars(h))
        h = fixture
        broker.hubs.add(h)

        def packet(kind, p):
            return b"P8\x01\0" + bytes(16) + struct.pack("<HH", kind, len(p)) + p

        broker.datagram_received(packet(21, bytes([1, 50])), ("172.17.0.1", 123))
        h.udp.assert_not_called()
        identity = (
            bytes(2)
            + b"FF88".ljust(16, b"\0")
            + b"P8SNTEST".ljust(16, b"\0")
            + b"3.10.5".ljust(16, b"\0")
            + bytes(4)
        )
        broker.datagram_received(packet(0, identity), ("172.17.0.1", 123))
        self.assertEqual(h.udp.call_count, 2)
        broker.datagram_received(b"bad", ("172.17.0.1", 123))
        self.assertEqual(broker.invalid_packets, 1)

    async def test_shared_listener_unload_and_async_timer(self):
        unsub = MagicMock()
        hass = types.SimpleNamespace(
            data={}, bus=MagicMock(), async_create_task=asyncio.create_task
        )
        hass.bus.async_listen_once.return_value = unsub
        entry = types.SimpleNamespace(
            data={"host": "192.168.12.34", "serial": "P8SNTEST"},
            options={"udp_port": 0},
        )

        async def request(path):
            return (
                {"Ports": []}
                if path == "/Port/List"
                else {"psu": []}
                if path == "/System/Health"
                else {}
            )

        client = types.SimpleNamespace(
            identity=AsyncMock(return_value={"Serial": "P8SNTEST", "Model": "FF88"}),
            request=request,
            host="192.168.12.34",
        )
        a = MatrixHub(hass, entry, None)
        b = MatrixHub(hass, entry, None)
        a.client = client
        b.client = client
        timers = []

        def timer(hass, callback, interval):
            timers.append(callback)
            return MagicMock()

        with patch(
            "custom_components.pulse_eight.hub.async_track_time_interval",
            side_effect=timer,
        ):
            await a.start()
            await b.start()
            self.assertIs(a.broker, b.broker)
            broker = a.broker
            self.assertTrue(inspect.iscoroutinefunction(timers[0]))
            await timers[0](None)
            await a.stop()
            self.assertIsNotNone(broker.transport)
            await b.stop()
            self.assertIsNone(broker.transport)
            unsub.assert_called_once()

    async def test_accepted_command_readback_failure_is_explicit(self):
        from homeassistant.exceptions import HomeAssistantError

        from custom_components.pulse_eight.client import MatrixError

        h = MatrixHub(
            types.SimpleNamespace(),
            types.SimpleNamespace(
                data={"host": "matrix.local", "serial": "P8SNTEST"}, options={}
            ),
            None,
        )
        h.client.request = AsyncMock(return_value={"Result": True})
        h.read = AsyncMock(side_effect=MatrixError("timeout"))
        with self.assertRaisesRegex(HomeAssistantError, "accepted the command"):
            await h.command("/Port/Set/0/0", refresh="/Port/Details/Output/0")
        h.client.request.assert_awaited_once()


@unittest.skipUnless(HAS_HA, "Requires the isolated HA runtime")
class HAFlowTests(unittest.IsolatedAsyncioTestCase):
    def flow(self):
        from custom_components.pulse_eight.config_flow import PulseEightConfigFlow

        flow = PulseEightConfigFlow()
        flow.hass = MagicMock()
        flow.context = {}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow._validate = AsyncMock(return_value={"Serial": "P8SNTEST", "Model": "FF88"})
        return flow

    async def test_discovery_requires_confirmation(self):
        f = self.flow()
        result = await f.async_step_integration_discovery(
            {"serial": "P8SNTEST", "host": "192.168.1.2"}
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "confirm")
        f._validate.assert_not_awaited()

    async def test_ssdp_uses_real_device_description_host(self):
        f = self.flow()
        result = await f.async_step_ssdp(
            types.SimpleNamespace(
                ssdp_location="http://192.168.1.2/network/service.xml",
                upnp={"serialNumber": "P8SNTEST"},
            )
        )
        self.assertEqual(f._discovered["host"], "192.168.1.2")
        self.assertEqual(result["type"], "form")
        result = await f.async_step_ssdp(
            types.SimpleNamespace(ssdp_location="https://invalid.example/", upnp={})
        )
        self.assertEqual(result["reason"], "not_supported")

    async def test_user_validates_identity_and_deduplicates(self):
        from homeassistant.data_entry_flow import AbortFlow

        f = self.flow()
        f._abort_if_unique_id_configured.side_effect = AbortFlow("already_configured")
        with self.assertRaises(AbortFlow):
            await f.async_step_user({"host": "192.168.1.2", "password": ""})
        f.async_set_unique_id.assert_awaited_once_with("P8SNTEST")

    async def test_options_default_cloud_disabled_and_port_conflict(self):
        from unittest.mock import PropertyMock

        from custom_components.pulse_eight.config_flow import PulseEightOptionsFlow

        f = PulseEightOptionsFlow()
        f.hass = MagicMock()
        f.context = {}
        with patch.object(
            PulseEightOptionsFlow,
            "config_entry",
            new_callable=PropertyMock,
            return_value=types.SimpleNamespace(options={}),
        ):
            result = await f.async_step_init()
            defaults = result["data_schema"]({})
            self.assertFalse(defaults["cloud_enabled"])
            result = await f.async_step_init(
                {**defaults, "cloud_enabled": True, "cloud_port": defaults["udp_port"]}
            )
            self.assertEqual(result["errors"], {"base": "port_conflict"})


@unittest.skipUnless(HAS_HA, "Requires the isolated HA runtime")
class FirmwareAndHardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_firmware_daily_throttle_notification_dedupe_and_failure(self):
        from custom_components.pulse_eight import firmware
        from custom_components.pulse_eight.client import MatrixError
        from custom_components.pulse_eight.firmware import FirmwareMonitor

        hub = types.SimpleNamespace(
            hass=MagicMock(),
            entry=types.SimpleNamespace(entry_id="test"),
            options={"firmware_check_enabled": True, "firmware_hardware": ""},
            state=types.SimpleNamespace(
                identity={"Version": "3.10.5-1", "Model": "FF88"}
            ),
            notify=MagicMock(),
        )
        monitor = FirmwareMonitor(hub, None)
        store = types.SimpleNamespace(
            async_load=AsyncMock(return_value=None), async_save=AsyncMock()
        )
        result = {"installed": "3.10.5-1", "latest": "3.10.6", "available": True}
        with (
            patch.object(firmware, "Store", return_value=store),
            patch.object(
                firmware, "fetch_update", new=AsyncMock(return_value=result)
            ) as fetch,
            patch.object(
                firmware, "async_track_time_interval", return_value=MagicMock()
            ) as timer,
            patch.object(firmware.persistent_notification, "async_create") as create,
            patch.object(firmware.persistent_notification, "async_dismiss"),
        ):
            with patch.object(firmware.time, "time", return_value=100000):
                await monitor.start()
                await monitor.check()
                await monitor.check(force=True)
            fetch.assert_awaited_once()
            create.assert_called_once()
            self.assertEqual(timer.call_args.args[2].total_seconds(), 3600)
            with patch.object(firmware.time, "time", return_value=186401):
                await monitor.check()
            self.assertEqual(fetch.await_count, 2)
            create.assert_called_once()
            fetch.side_effect = MatrixError("Vendor unreachable")
            with patch.object(firmware.time, "time", return_value=272802):
                await monitor.check()
            self.assertEqual(monitor.data["latest"], "3.10.6")
            self.assertIn("unreachable", monitor.data["error"])
            await monitor.stop()
            await monitor.check(force=True)
            self.assertEqual(fetch.await_count, 3)

    async def test_firmware_cache_preserves_daily_interval_across_restart(self):
        from custom_components.pulse_eight import firmware
        from custom_components.pulse_eight.firmware import FirmwareMonitor

        hub = types.SimpleNamespace(
            hass=MagicMock(),
            entry=types.SimpleNamespace(entry_id="test"),
            options={"firmware_check_enabled": True, "firmware_hardware": ""},
            notify=MagicMock(),
        )
        monitor = FirmwareMonitor(hub, None)
        store = types.SimpleNamespace(
            async_load=AsyncMock(return_value={"last_attempt": 99999}),
            async_save=AsyncMock(),
        )
        with (
            patch.object(firmware, "Store", return_value=store),
            patch.object(firmware, "fetch_update", new=AsyncMock()) as fetch,
            patch.object(
                firmware, "async_track_time_interval", return_value=MagicMock()
            ),
            patch.object(firmware.time, "time", return_value=100000),
        ):
            await monitor.start()
            fetch.assert_not_awaited()
            await monitor.stop()
        hub.options["firmware_check_enabled"] = False
        monitor = FirmwareMonitor(hub, None)
        with patch.object(firmware, "Store") as store_factory:
            await monitor.start()
            store_factory.assert_not_called()

    async def test_read_only_update_entity_does_not_offer_install(self):
        from custom_components.pulse_eight.const import DEFAULT_OPTIONS
        from custom_components.pulse_eight.update import MatrixFirmware

        hub = HARuntimeTests().hub()
        hub.options = DEFAULT_OPTIONS
        hub.state.identity["Version"] = "3.10.5-1"
        hub.firmware = types.SimpleNamespace(
            data={"last_success": 1, "latest": "3.10.6", "installed": "3.10.5-1"},
            check=AsyncMock(),
        )
        entity = MatrixFirmware(hub)
        self.assertTrue(entity.available)
        self.assertEqual(entity.supported_features, 0)
        # Exercise the actual HA state renderer, which requires an IntFlag.
        self.assertEqual(entity.state_attributes["latest_version"], "3.10.6")
        hub.firmware.data["error"] = "failure"
        self.assertFalse(entity.available)
        await entity.async_update()
        hub.firmware.check.assert_awaited_once_with(force=True)

    async def test_one_broken_listener_does_not_block_others(self):
        hub = MatrixHub(
            types.SimpleNamespace(),
            types.SimpleNamespace(
                data={"host": "matrix.local", "serial": "P8SNTEST"}, options={}
            ),
            None,
        )
        good = MagicMock()
        bad = MagicMock(side_effect=ValueError("broken entity"))
        hub.listeners = [bad, good]
        with self.assertLogs("custom_components.pulse_eight.hub", level="ERROR"):
            hub._notify()
        good.assert_called_once()

    async def test_discovery_identity_mismatch_and_bad_ssdp_port(self):
        f = HAFlowTests().flow()
        await f.async_step_integration_discovery(
            {"serial": "P8DIFFERENT", "host": "192.168.1.2"}
        )
        result = await f.async_step_confirm({"host": "192.168.1.2"})
        self.assertEqual(result["errors"], {"base": "cannot_connect"})
        result = await f.async_step_ssdp(
            types.SimpleNamespace(
                ssdp_location="http://192.168.1.2:invalid/network/service.xml", upnp={}
            )
        )
        self.assertEqual(result["reason"], "not_supported")


@unittest.skipUnless(HAS_HA, "Requires the isolated HA runtime")
class NotificationFrameworkTest(unittest.IsolatedAsyncioTestCase):
    async def test_notification_is_created_by_real_ha_component(self):
        import tempfile

        from homeassistant.components import persistent_notification as pn
        from homeassistant.core import HomeAssistant

        from custom_components.pulse_eight import firmware

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            hub = types.SimpleNamespace(
                hass=hass,
                entry=types.SimpleNamespace(entry_id="notification_test"),
                options={"firmware_check_enabled": True, "firmware_hardware": ""},
                state=types.SimpleNamespace(
                    identity={"Version": "3.10.5-1", "Model": "FF88"}
                ),
                notify=MagicMock(),
            )
            monitor = firmware.FirmwareMonitor(hub, None)
            monitor._ready = True
            monitor.store = types.SimpleNamespace(async_save=AsyncMock())
            result = {"installed": "3.10.5-1", "latest": "3.10.6", "available": True}
            with patch.object(
                firmware, "fetch_update", new=AsyncMock(return_value=result)
            ):
                await monitor.check()
            notifications = pn._async_get_or_create_notifications(hass)
            self.assertEqual(len(notifications), 1)
            notification = next(iter(notifications.values()))
            self.assertIn("3.10.6", notification["message"])
            self.assertIn("not downloaded or installed", notification["message"])
            pn.async_dismiss(hass, notification["notification_id"])
            self.assertEqual(len(notifications), 0)
            await monitor.stop()
            await hass.async_block_till_done()
