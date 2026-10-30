"""Push state and serialized on-demand controls for one matrix."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.event import async_track_time_interval

from .client import MatrixClient, MatrixError
from .cloud import LocalCloud
from .const import CRASH_DIR, CRASH_KEEP, DEFAULT_OPTIONS, DOMAIN, EDID_PROFILES
from .crash import CrashArchive
from .discovery import get_broker
from .firmware import FirmwareMonitor
from .state import MatrixState

_LOGGER = logging.getLogger(__name__)


class MatrixHub:
    def __init__(self, hass, entry, session):
        self.hass = hass
        self.entry = entry
        self.serial = entry.data["serial"]
        self.options = {**DEFAULT_OPTIONS, **entry.options}
        self.client = MatrixClient(
            session, entry.data["host"], entry.data.get("password", "")
        )
        self.state = MatrixState()
        self.listeners = set()
        self.broker = None
        self.cloud = None
        self.source_ips = set()
        self.tasks = set()
        self._unsub = None
        self._last_notify = 0.0
        self._notify_handle = None
        self._last_raw = {}
        self.firmware = FirmwareMonitor(self, session)
        self.crashes = None
        self.stopping = False

    async def start(self):
        identity = await self.client.identity()
        if identity["Serial"] != self.serial:
            raise MatrixError("This address belongs to a different matrix")
        self.ingest("/System/Details", identity)
        # Resolve the configured host once, outside the event loop.
        info = await asyncio.get_running_loop().getaddrinfo(
            self.client.host, 80, type=socket.SOCK_STREAM
        )
        self.source_ips = {r[4][0] for r in info}
        try:
            self.broker = await get_broker(self.hass, self.options["udp_port"])
            self.broker.hubs.add(self)
            for path in ["/System/Features", "/Port/List", "/System/Health"]:
                self.ingest(path, await self.client.request(path))
            for path in [
                "/CEC/AutoPause",
                "/CEC/ComboKeys",
                "/IR/Profiles",
                "/IR/Details",
            ]:
                try:
                    self.ingest(path, await self.client.request(path))
                except MatrixError:
                    pass
            if self.state.features.get("Audio", {}).get("Output", {}).get("Bays", 0):
                try:
                    self.ingest(
                        "/Audio/Details", await self.client.request("/Audio/Details")
                    )
                except MatrixError:
                    pass
            if self.options["cloud_enabled"]:
                self.cloud = LocalCloud(self)
                await self.cloud.start()

            async def tick(_):
                self.notify()

            self._unsub = async_track_time_interval(
                self.hass, tick, timedelta(seconds=30)
            )
        except BaseException:
            await self.stop()
            raise

    async def bootstrap_details(self):
        # One startup inventory. There is no periodic HTTP polling task.
        for p in list(self.state.ports.values()):
            try:
                await self.read(f"/Port/Details/{p['Mode']}/{p['Bay']}")
            except MatrixError:
                _LOGGER.warning(
                    "Initial port inventory stopped after an unavailable response; use Refresh details to try again"
                )
                return False
            if p["Mode"] == "Output":
                try:
                    await self.read(f"/IR/Details/{p['Bay']}")
                except MatrixError:
                    pass

        return True

    async def capture_crash(self, log):
        """Archive a crash report to /config and raise a notification once."""
        if self.crashes is None:
            self.crashes = CrashArchive(self.hass.config.path(CRASH_DIR), CRASH_KEEP)
        try:
            saved = await self.hass.async_add_executor_job(self.crashes.archive, log)
        except OSError:
            _LOGGER.exception("Could not write the matrix crash report")
            persistent_notification.async_create(
                self.hass,
                "The matrix reported a crash, but the report could not be saved. "
                "Check the Home Assistant log and free disk space.",
                title="Pulse-Eight matrix crash report lost",
                notification_id=f"{DOMAIN}_{self.serial}_crash_lost",
            )
            return
        if saved is None:
            return
        persistent_notification.async_create(
            self.hass,
            f"The matrix {self.serial} reported a crash ({saved.summary}).\n\n"
            f"The full report was saved to `{saved.path}`.",
            title="Pulse-Eight matrix crash report captured",
            notification_id=f"{DOMAIN}_{self.serial}_crash_{saved.digest}",
        )

    def spawn(self, coro):
        task = self.hass.async_create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    @property
    def online(self):
        return (
            self.state.last_seen is not None
            and time.time() - self.state.last_seen < self.options["stale_seconds"]
        )

    def fresh(self, observed):
        return (
            observed is not None
            and time.time() - observed < self.options["stale_seconds"]
        )

    def udp(self, packet):
        self.state.udp(packet)
        self.notify()
        if self.options["raw_events"]:
            # Preserve every accepted packet during ordinary traffic. Cap abusive
            # floods without allocating an unbounded queue in Home Assistant.
            now = time.monotonic()
            window, count = self._last_raw.get("window", (now, 0))
            if now - window >= 1:
                window, count = now, 0
            if count < 100:
                self.hass.bus.async_fire(
                    DOMAIN + "_udp_packet", {"device_id": self.serial, "packet": packet}
                )
            self._last_raw["window"] = (window, count + 1)

    def notify(self):
        # Coalesce noisy UDP types to at most four entity updates per second.
        if self.stopping or self._notify_handle is not None:
            return
        self._notify_handle = asyncio.get_running_loop().call_later(0.25, self._notify)

    def _notify(self):
        self._notify_handle = None
        if getattr(self, "device_id", None):
            registry = dr.async_get(self.hass)
            for key, p in self.state.ports.items():
                device = registry.async_get_device_by_identifier(
                    (DOMAIN, f"{self.serial}_{key}"), self.entry.entry_id
                )
                name = f"{p['Mode']} {p['Bay'] + 1}: {p.get('Name', p['Mode'])}"
                if device and device.name != name:
                    registry.async_update_device(device.id, name=name)
        for listener in tuple(self.listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("A Pulse-Eight entity update failed")

    def ingest(self, path, data):
        try:
            self.state.response(path, data)
        except (ValueError, TypeError, KeyError) as exc:
            raise MatrixError(f"Invalid device response for {path}") from exc

    async def read(self, path, transport="http"):
        if transport == "cloud":
            if not self.cloud:
                raise MatrixError("Local cloud listener is disabled")
            return await self.cloud.query(path)
        data = await self.client.request(path)
        self.ingest(path, data)
        self.notify()
        return data

    async def command(self, path, *, method="GET", payload=None, refresh=None):
        try:
            result = await self.client.request(path, method=method, payload=payload)
        except MatrixError as exc:
            raise HomeAssistantError(str(exc)) from exc
        if refresh:
            try:
                await self.read(refresh)
            except MatrixError as exc:
                raise HomeAssistantError(
                    "The matrix accepted the command, but its status could not be read back. Do not retry automatically."
                ) from exc
        self.notify()
        return result

    async def route(self, bay, source):
        p = self.state.port("Output", bay)
        allowed = p.get("AllowedSources")
        if allowed is not None and source not in allowed:
            raise HomeAssistantError("Input is not allowed for this output")
        return await self.command(
            f"/Port/Set/{source}/{bay}", refresh=f"/Port/Details/Output/{bay}"
        )

    async def edid(self, bay, profile):
        if f"Input/{bay}" not in self.state.ports:
            raise HomeAssistantError("Input does not exist")
        if (
            profile not in EDID_PROFILES
            and f"Output/{profile - 101}" not in self.state.ports
            and profile != self.state.port("Input", bay).get("EdidProfile")
        ):
            raise HomeAssistantError("Unsupported EDID profile")
        if profile in (7, 8) and not self.state.features.get("Video", {}).get(
            "Scrambling"
        ):
            raise HomeAssistantError(
                "This matrix does not report HDR EDID profile support"
            )
        return await self.command(
            f"/Port/EDID/Set/{profile}/{bay}", refresh=f"/Port/Details/Input/{bay}"
        )

    async def cec(self, mode, bay, frame):
        try:
            return await self.client.cec(mode, bay, frame)
        except (ValueError, MatrixError) as exc:
            raise HomeAssistantError(str(exc)) from exc

    async def stop(self):
        self.stopping = True
        await self.firmware.stop()
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._notify_handle:
            self._notify_handle.cancel()
            self._notify_handle = None
        for t in tuple(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.cloud:
            await self.cloud.stop()
            self.cloud = None
        if self.broker:
            self.broker.hubs.discard(self)
            if not self.broker.hubs:
                self.broker.close()
                self.hass.data[DOMAIN + "_runtime"]["brokers"].pop(
                    self.broker.port, None
                )
            self.broker = None
