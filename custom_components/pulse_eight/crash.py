"""Archive device crash reports found in monitoring uploads.

The firmware attaches a crash report (fault type, registers, memory map, system
log tail) as the `log` field of its next upload after a fault. Reports are kept
as files and never become entity state or diagnostics.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

MAX_LOG = 262_144
_HEADER = re.compile(r"time: [^\n]*uptime: \d+ seconds\r?\n")
_REGISTERS = re.compile(r"^\s*R0:", re.MULTILINE)


@dataclass(frozen=True)
class SavedReport:
    path: Path
    summary: str
    digest: str


def is_crash_report(log) -> bool:
    if not isinstance(log, str) or len(log) > MAX_LOG:
        return False
    m = _HEADER.match(log)
    if not m:
        return False
    return (
        bool(_REGISTERS.search(log))
        or "fault" in log[m.end() :].split("\n", 1)[0].lower()
    )


class CrashArchive:
    def __init__(self, directory, keep=50):
        self.directory = Path(directory)
        self.keep = keep

    def archive(self, log, now=None):
        """Write one file per distinct crash. Returns None for non-crashes and repeats.

        Blocking; call from an executor.
        """
        if not is_crash_report(log):
            return None
        # The firmware may resend the same report with a longer log tail.
        crash_part = log.split("\nsystem log:", 1)[0]
        digest = hashlib.sha256(crash_part.encode("utf-8", "replace")).hexdigest()[:16]
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(self.directory.glob(f"*-{digest}.txt")):
            return None
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now or time.time()))
        path = self.directory / f"crash-{stamp}-{digest}.txt"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(log)
        os.replace(tmp, path)
        for old in sorted(self.directory.glob("crash-*.txt"))[: -self.keep or None]:
            old.unlink(missing_ok=True)
        lines = log.splitlines()
        return SavedReport(path, lines[1].strip() if len(lines) > 1 else "", digest)
