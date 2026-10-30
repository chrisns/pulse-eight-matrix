"""Read advisory vendor metadata only; never fetch or execute an offered image."""

from __future__ import annotations

import json
import re

from aiohttp import ClientError, ClientTimeout

from .client import MatrixError

UPDATE_ENDPOINT = "http://update.pulse-eight.com/Update/"
# Only families established from an actual native check-in belong here.
MODEL_HARDWARE = {"FF88": "F8"}
VERSION = r"[0-9]{1,3}(?:\.[0-9]{1,5}){2}(?:-[0-9]{1,5})?"


def version(value):
    if not isinstance(value, str) or not re.fullmatch(VERSION, value):
        raise MatrixError("Invalid firmware version")
    return value


async def fetch_update(session, identity, hardware=""):
    installed = version(identity.get("Version"))
    model = identity.get("Model", "")
    family = hardware or MODEL_HARDWARE.get(model)
    if not isinstance(family, str) or not re.fullmatch(r"[A-Za-z0-9]{1,16}", family):
        raise MatrixError(
            "Unknown firmware hardware family; set it in integration options"
        )
    serial = identity.get("Serial", "")
    vendor = identity.get("VID", "P8")
    if (
        not re.fullmatch(r"P8[A-Za-z0-9_-]{1,30}", serial)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", model)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", vendor)
    ):
        raise MatrixError("Invalid update identity")
    # Omitting apphw silently returns Update=false even for an older version.
    params = {
        "app": installed.split("-")[0],
        "serial": serial,
        "apphw": family,
        "model": model,
        "vendor": vendor,
    }
    try:
        async with session.get(
            UPDATE_ENDPOINT,
            params=params,
            allow_redirects=False,
            timeout=ClientTimeout(total=20),
        ) as response:
            if response.status != 200:
                raise MatrixError(f"Update service returned HTTP {response.status}")
            body = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                body.extend(chunk)
                if len(body) > 16384:
                    raise MatrixError("Oversized update metadata")
            data = json.loads(body)
    except (ClientError, TimeoutError, ValueError, RecursionError) as exc:
        raise MatrixError(
            f"Unable to check vendor metadata: {type(exc).__name__}"
        ) from exc
    if not isinstance(data, dict) or type(data.get("Update")) is not bool:
        raise MatrixError("Invalid update metadata")
    if not data["Update"]:
        return {"installed": installed, "latest": installed, "available": False}
    # Strict hostname/path matching excludes credentials, arbitrary links and redirects.
    match = re.fullmatch(
        rf"https?://firmware\.pulse-eight\.com/MatrixOSUpdates/[0-9]+/matrixos-{re.escape(family)}-({VERSION})\.bin",
        data.get("UpdateURL", "") if isinstance(data.get("UpdateURL"), str) else "",
    )
    if not match:
        raise MatrixError("Unrecognized vendor firmware URL; no image was downloaded")
    latest = version(match[1])

    # Never turn a downgrade or same-version response into an update notification.
    def key(v):
        parts = tuple(int(n) for n in re.split(r"[.-]", v))
        return parts + (0,) * (4 - len(parts))

    if key(latest) <= key(installed):
        raise MatrixError("Vendor offered a non-newer firmware version")
    return {"installed": installed, "latest": latest, "available": True}
