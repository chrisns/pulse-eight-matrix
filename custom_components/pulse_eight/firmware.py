"""Daily advisory check from HA, independently of the isolated matrix."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .client import MatrixError
from .const import DOMAIN
from .update_client import fetch_update

_LOGGER = logging.getLogger(__name__)
DAY = 86400


class FirmwareMonitor:
    def __init__(self, hub, session):
        self.hub, self.session = hub, session
        self.store = None
        self._ready = False
        self.data = {}
        self.lock = asyncio.Lock()
        self._unsub = None
        self._stopped = False
        self._task = None

    async def start(self):
        if self._stopped or not self.hub.options["firmware_check_enabled"]:
            return
        self.store = Store(
            self.hub.hass, 1, f"{DOMAIN}.firmware.{self.hub.entry.entry_id}"
        )
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            # Private HA storage, but don't trust it to supply URLs or executable data.
            self.data = {
                k: v
                for k, v in stored.items()
                if k
                in (
                    "last_attempt",
                    "last_success",
                    "installed",
                    "latest",
                    "available",
                    "notified",
                    "error",
                )
            }
        self._ready = True
        self.hub.notify()
        if self._stopped:
            return

        async def tick(_):
            await self.check()

        self._unsub = async_track_time_interval(self.hub.hass, tick, timedelta(hours=1))
        await self.check()

    async def check(self, force=False):
        async with self.lock:
            if self._stopped or not self.hub.options["firmware_check_enabled"]:
                return
            if not self._ready:
                raise MatrixError("Firmware monitor is still initializing")
            now = time.time()
            previous = self.data.get("last_attempt", 0)
            if isinstance(previous, (int, float)) and 0 <= now - previous < (
                60 if force else DAY
            ):
                return
            self._task = asyncio.current_task()
            self.data["last_attempt"] = now
            try:
                result = await fetch_update(
                    self.session,
                    self.hub.state.identity,
                    self.hub.options["firmware_hardware"],
                )
                self.data.update(result, last_success=now, error=None)
                if (
                    result["available"]
                    and self.data.get("notified") != result["latest"]
                ):
                    persistent_notification.async_create(
                        self.hub.hass,
                        f"Pulse-Eight reports firmware {result['latest']} is available for "
                        f"{self.hub.state.identity.get('Model', 'matrix')} (installed {result['installed']}). "
                        "This is advisory metadata received over HTTP, not a verified image. "
                        "Review the release with Pulse-Eight before updating manually. "
                        "Home Assistant has not downloaded or installed firmware.",
                        title="Pulse-Eight firmware update available",
                        notification_id=f"{DOMAIN}_firmware_{self.hub.entry.entry_id}",
                    )
                    self.data["notified"] = result["latest"]
                elif not result["available"]:
                    persistent_notification.async_dismiss(
                        self.hub.hass, f"{DOMAIN}_firmware_{self.hub.entry.entry_id}"
                    )
                    self.data.pop("notified", None)
            except MatrixError as exc:
                # Preserve last known result, but make the current check failure visible.
                self.data["error"] = str(exc)
                _LOGGER.warning("Firmware metadata check failed: %s", exc)
            finally:
                try:
                    await self.store.async_save(self.data)
                finally:
                    self._task = None
                    self.hub.notify()

    def snapshot(self):
        return dict(self.data)

    async def stop(self):
        self._stopped = True
        if self._unsub:
            self._unsub()
            self._unsub = None
        task = self._task
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
