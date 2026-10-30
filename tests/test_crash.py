"""Crash-report archive tests (no Home Assistant runtime needed)."""

import importlib
import tempfile
import unittest
from pathlib import Path

import test_native  # noqa: F401  (registers the p8_native_test package)

crash = importlib.import_module("p8_native_test.crash")

REPORT = (
    "time: 29/10/2026 15:55:16, uptime: 8194 seconds\n"
    "bus fault: BFARVALID(BFAR:E000ED38), PRECISERR\n\n"
    "  R0:10007FE0   R1:00000000\n\nsystem log:\nnetmgr: DNS ping\r\n"
)


class CrashArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "crashes"
        self.archive = crash.CrashArchive(self.dir, keep=3)

    def test_detects_crash_reports_only(self):
        self.assertTrue(crash.is_crash_report(REPORT))
        for x in ["", "ordinary log line\n", "time: now\nfault", None, 5]:
            self.assertFalse(crash.is_crash_report(x))
        self.assertFalse(crash.is_crash_report(REPORT + "x" * crash.MAX_LOG))

    def test_archives_one_file_per_report_verbatim(self):
        saved = self.archive.archive(REPORT, now=1_793_000_000)
        self.assertEqual(saved.path.read_text(newline=""), REPORT)
        self.assertEqual(
            saved.summary, "bus fault: BFARVALID(BFAR:E000ED38), PRECISERR"
        )
        self.assertEqual(saved.path.parent, self.dir)
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_repeat_of_the_same_crash_is_not_archived_twice(self):
        self.archive.archive(REPORT, now=1_793_000_000)
        again = REPORT + "(W) emac: later log tail\r\n"
        self.assertIsNone(self.archive.archive(again, now=1_793_000_900))
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_non_crash_text_is_not_archived(self):
        self.assertIsNone(self.archive.archive("hello"))
        self.assertFalse(self.dir.exists())

    def test_retention_keeps_newest(self):
        for i in range(5):
            self.archive.archive(REPORT.replace("8194", str(i)), now=1_793_000_000 + i)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(len(names), 3)
        kept = [f"uptime: {n} " for n in "234"]
        texts = [(self.dir / n).read_text(newline="") for n in names]
        self.assertTrue(all(k in t for k, t in zip(kept, texts)))


if __name__ == "__main__":
    unittest.main()
