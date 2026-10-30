"""Optional local-only MatrixOS cloud and monitoring endpoints.

No vendor forwarding, firmware downloads, redirect configuration or router changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import time

from aiohttp import web

from .client import MatrixError

_LOGGER = logging.getLogger(__name__)


def _normalise(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    return str(addr.ipv4_mapped or addr) if addr.version == 6 else str(addr)


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 1_048_576:
            raise ValueError("Cloud frame too large")
        out = []
        while self.buffer:
            if self.buffer.startswith(b"noop"):
                del self.buffer[:4]
                out.append(None)
                continue
            if len(self.buffer) < 4 and b"noop".startswith(self.buffer):
                break
            positions = [
                p for c in (b"\r", b"\n", b"\0") if (p := self.buffer.find(c)) >= 0
            ]
            if not positions:
                break
            n = min(positions)
            raw = bytes(self.buffer[:n])
            del self.buffer[: n + 1]
            if raw:
                obj = json.loads(raw)
                if not isinstance(obj, dict):
                    raise ValueError("Invalid cloud response")
                out.append(obj)
        return out


class LocalCloud:
    HANDSHAKE_TIMEOUT = 30

    def __init__(self, hub):
        self.hub = hub
        self.server = None
        self.runner = None
        self.writer = None
        self.pending = None
        self.pending_writer = None
        self.writers = set()
        self.tasks = set()
        self.connected = False

    async def start(self):
        self.server = await asyncio.start_server(
            self.accept, "0.0.0.0", self.hub.options["cloud_port"]
        )
        try:
            app = web.Application(client_max_size=2_000_000)
            app.router.add_post("/{tail:.*}", self.monitoring)
            self.runner = web.AppRunner(app, access_log=None)
            await self.runner.setup()
            await web.TCPSite(
                self.runner, "0.0.0.0", self.hub.options["monitoring_port"]
            ).start()
        except BaseException:
            await self.stop()
            raise

    def allowed(self, peer):
        """Optional source-IP filter (the `source_filter` option, off by default).

        When on, only the matrix (and any `trusted_sources` NAT/proxy address) may
        connect, and with no known source nothing is accepted. The serial is an
        identifier, not authentication, so this is the only gate before parsing.
        """
        if not self.hub.options.get("source_filter"):
            return True
        peer = _normalise(peer)
        extra = str(self.hub.options.get("trusted_sources") or "").split(",")
        known = {_normalise(i) for i in (*self.hub.source_ips, *map(str.strip, extra))}
        known.discard(None)
        return peer is not None and peer in known

    async def monitoring(self, request):
        if not self.allowed(request.remote):
            return web.json_response({"Update": False}, status=403)
        if request.query.get("serial") != self.hub.serial:
            return web.json_response({"Update": False}, status=403)
        # The native log string contains unescaped newlines on this firmware.
        try:
            payload = json.loads(await request.read(), strict=False)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return web.json_response({"Update": False}, status=400)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("Modules", []), list
        ):
            return web.json_response({"Update": False}, status=400)
        try:
            self.hub.state.telemetry(payload)
        except (ValueError, TypeError, KeyError):
            return web.json_response({"Update": False}, status=400)
        log = payload.get("log")
        if isinstance(log, str) and log:
            try:
                await self.hub.capture_crash(log)
            except Exception:
                # Never lose the rest of the upload over a failed archive.
                _LOGGER.exception("Could not archive the matrix crash report")
        self.hub.notify()
        return web.json_response(
            {"Time": str(int(time.time())), "Update": False, "ExecutionTime": "0"}
        )

    async def accept(self, reader, writer):
        task = asyncio.current_task()
        peer = (writer.get_extra_info("peername") or ("",))[0]
        if not self.allowed(peer) or len(self.writers) >= 4:
            writer.close()
            return
        self.tasks.add(task)
        self.writers.add(writer)
        identified = False
        challenged = False
        handshake_lock = False
        deadline = asyncio.get_running_loop().time() + self.HANDSHAKE_TIMEOUT
        decoder = Decoder()
        try:
            while True:
                data = await asyncio.wait_for(
                    reader.read(8192),
                    90
                    if identified
                    else max(0.001, deadline - asyncio.get_running_loop().time()),
                )
                if not data:
                    break
                for obj in decoder.feed(data):
                    if not identified:
                        if obj is None:
                            if not challenged:
                                # A reconnected/proxied device can start with only
                                # keepalives. Request its identity once; never poll.
                                await asyncio.wait_for(
                                    self.hub.client.lock.acquire(),
                                    max(
                                        0.001,
                                        deadline - asyncio.get_running_loop().time(),
                                    ),
                                )
                                handshake_lock = True
                                writer.write(b"system/details\r")
                                await writer.drain()
                                challenged = True
                            continue
                        if obj.get("Serial") != self.hub.serial:
                            raise ValueError("Unexpected serial")
                        if self.writer and self.writer is not writer:
                            self.writer.close()
                            if self.pending and not self.pending.done():
                                self.pending.set_exception(
                                    MatrixError("Cloud connection replaced")
                                )
                        self.writer = writer
                        identified = True
                        self.connected = True
                        if handshake_lock:
                            self.hub.client.lock.release()
                            handshake_lock = False
                        self.hub.state.response("/System/Details", obj)
                        self.hub.notify()
                    elif (
                        obj is not None
                        and self.pending_writer is writer
                        and self.pending is not None
                        and not self.pending.done()
                    ):
                        self.pending.set_result(obj)
        except (ValueError, TimeoutError, ConnectionError, RecursionError):
            pass
        finally:
            if handshake_lock:
                self.hub.client.lock.release()
            if self.writer is writer:
                self.writer = None
                self.connected = False
                if self.pending and not self.pending.done():
                    self.pending.set_exception(MatrixError("Cloud connection closed"))
                self.hub.notify()
            self.writers.discard(writer)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            self.tasks.discard(task)

    async def query(self, path):
        command = path.strip("/").lower()
        # Only getters that are known not to have side effects.
        allowed = (
            "system/details",
            "system/health",
            "system/features",
            "port/list",
            "monitoring/settings",
            "network/settings",
            "cec/logging",
            "cec/autopause",
            "cec/combokeys",
        )
        if command not in allowed and not re.fullmatch(
            r"(?:port/(?:details|remotedetails)|edid/(?:details|timings|audio|hdmi|hdr))/(?:input|output)/(?:[0-9]|[12][0-9]|3[01])",
            command,
        ):
            raise MatrixError("Cloud query must be a supported getter")
        if any(c in command for c in "\r\n\0?#") or len(command) > 200:
            raise ValueError("Invalid command")
        async with self.hub.client.lock:
            writer = self.writer
            if not writer or writer.is_closing():
                raise MatrixError("No local cloud connection")
            future = asyncio.get_running_loop().create_future()
            self.pending = future
            self.pending_writer = writer
            try:
                writer.write(command.encode() + b"\r")
                await writer.drain()
                result = await asyncio.wait_for(future, 45)
                if result.get("Result") is False and command != "cec/autopause":
                    raise MatrixError(result.get("ErrorMessage", "Query rejected"))
                try:
                    self.hub.state.response(path, result)
                except (ValueError, TypeError, KeyError) as exc:
                    raise MatrixError("Invalid cloud response") from exc
                self.hub.notify()
                return result
            except asyncio.CancelledError:
                # There are no request IDs. A late reply after cancellation must
                # never become the response to the next command.
                writer.close()
                raise
            except (ConnectionError, OSError) as exc:
                writer.close()
                raise MatrixError("Cloud connection failed") from exc
            except TimeoutError:
                writer.close()
                raise MatrixError(
                    "Cloud query timed out; connection closed to discard late responses"
                ) from None
            finally:
                self.pending = None
                self.pending_writer = None

    async def stop(self):
        server, self.server = self.server, None
        if server:
            server.close()
        for w in tuple(self.writers):
            w.close()
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        # Python 3.13+ waits for accepted connections too. Close their writers
        # and finish handler tasks before waiting for the server to be closed.
        if server:
            await server.wait_closed()
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
