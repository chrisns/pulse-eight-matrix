"""Serialized MatrixOS HTTP client. Mutations are never retried."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout


class MatrixError(Exception):
    """Device rejected a request or returned an invalid response."""


class MatrixAuthError(MatrixError):
    """Authentication failed."""


def validate_host(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Invalid device address")
    value = value.strip()
    if not value or len(value) > 253 or any(c in value for c in "/?#@:\\ \r\n\0"):
        raise ValueError("Enter an IPv4 address or hostname, without a URL or port")
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        if re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value
        ) is None or re.fullmatch(r"[0-9.]+", value):
            raise ValueError("Invalid hostname") from None
        if value.lower() == "localhost":
            raise ValueError("Invalid device address")
    else:
        if (
            ip.is_unspecified
            or ip.is_multicast
            or ip.is_loopback
            or str(ip) == "255.255.255.255"
        ):
            raise ValueError("Device address must be unicast")
    return value


def validate_cec(frame: str) -> str:
    if not isinstance(frame, str) or not re.fullmatch(
        r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){0,15}", frame
    ):
        raise ValueError("CEC frame must contain 1 to 16 colon-separated hex bytes")
    return frame.upper()


class MatrixClient:
    def __init__(
        self, session: ClientSession, host: str, password: str = "", port: int = 80
    ):
        self.session, self.host, self.password, self.port = (
            session,
            validate_host(host),
            password,
            port,
        )
        self.lock = asyncio.Lock()
        self._authenticated = False
        self.last_request = 0.0

    async def _request(self, method: str, path: str, payload: Any = None) -> dict:
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path:
            raise ValueError("Invalid matrix API path")
        url = f"http://{self.host}:{self.port}{path}"
        try:
            async with self.session.request(
                method,
                url,
                json=payload,
                timeout=ClientTimeout(total=45),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    self._authenticated = False
                    raise MatrixAuthError("Matrix authentication required")
                if response.status != 200:
                    raise MatrixError(f"Matrix returned HTTP {response.status}")
                if response.content_length and response.content_length > 2_000_000:
                    raise MatrixError("Oversized response")
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > 2_000_000:
                        raise MatrixError("Oversized response")
                if len(body) > 2_000_000:
                    raise MatrixError("Oversized response")
                import json

                result = json.loads(body, strict=False)
                if not isinstance(result, dict):
                    raise MatrixError("Invalid matrix response")
                if result.get("Result") is False and not (
                    method == "GET" and path.lower().rstrip("/") == "/cec/autopause"
                ):
                    if path.lower().rstrip("/") == "/account/login":
                        raise MatrixAuthError("Matrix password rejected")

                    raise MatrixError(
                        result.get(
                            "ErrorMessage",
                            result.get("Message", "Matrix rejected the request"),
                        )
                    )
                return result
        except (ClientError, TimeoutError, ValueError, RecursionError) as exc:
            if isinstance(exc, MatrixError):
                raise
            raise MatrixError(
                f"Unable to complete {path}: {type(exc).__name__}"
            ) from exc

    async def request(
        self, path: str, *, method: str = "GET", payload: Any = None
    ) -> dict:
        async with self.lock:
            delay = 0.15 - (time.monotonic() - self.last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                if self.password and not self._authenticated:
                    await self._request(
                        "POST", "/Account/Login", {"Password": self.password}
                    )
                    self._authenticated = True
                return await self._request(method, path, payload)
            finally:
                self.last_request = time.monotonic()

    async def identity(self) -> dict:
        data = await self.request("/System/Details")
        if (
            not isinstance(data.get("Serial"), str)
            or not data["Serial"].startswith("P8")
            or not re.fullmatch(r"P8[A-Za-z0-9_-]{1,30}", data["Serial"])
            or not isinstance(data.get("Model"), str)
            or not 1 <= len(data["Model"]) <= 32
            or not isinstance(data.get("Version"), str)
            or len(data["Version"]) > 32
        ):
            raise MatrixError("Not a supported Pulse-Eight matrix")
        return data

    async def cec(self, mode: str, bay: int, frame: str) -> dict:
        if mode not in ("Input", "Output") or not 0 <= bay < 32:
            raise ValueError("Invalid CEC port")
        return await self.request(
            f"/cec/tx/{mode}/{bay}",
            method="POST",
            payload={"Command": validate_cec(frame)},
        )
